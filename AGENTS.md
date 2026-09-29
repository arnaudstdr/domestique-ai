# AGENTS.md

Guidance for agents working in this repository. OpenCode V2 reads `AGENTS.md`
(not `CLAUDE.md`).

## Délégation à des sous-agents

Règle transverse (voir aussi `~/.config/opencode/AGENTS.md`) : **toute
exploration de contexte ou de codebase part en sous-agent**, jamais en
exploration directe dans l'agent principal.

- `explore` : défaut pour la quasi-totalité des recherches — localiser un
  fichier, trouver un symbole, comprendre un flux, répondre à une question sur
  le code.
- `general` : recherche multi-étapes plus large (croiser plusieurs zones,
  comparer deux implémentations, croiser doc externe et code).

Concrètement dans ce repo :

- L'agent principal ne `read`/`grep`/`glob` que ce qu'il modifie ou vérifie juste
  après (fichier ciblé, diff, sortie de test).
- Le sous-agent commence par `graphify query "<question>"` (puis `graphify path`
  / `graphify explain`) avant tout `grep` ou lecture brute, conformément à la
  section [graphify](#graphify).
- Il respecte les commandes de ce guide : tests et lint via
  `.venv/bin/python -m pytest` / `.venv/bin/python -m ruff`, et **jamais** de
  préfixe `rtk` sur `pytest`/`ruff`/`vitest`.
- Quand l'exploration entre dans un sous-paquet (`domestique_ai/ingestion/`,
  `processing/`, `llm/`, `api/`, `frontend/`), le sous-agent **restitue les
  contraintes pertinentes de l'`AGENTS.md` du sous-paquet** dans sa synthèse —
  sinon l'agent principal ne voit pas ces conventions (voir
  [Carte du repo](#carte-du-repo)).

## Carte du repo

Le détail d'implémentation est éclaté dans des `AGENTS.md` de sous-paquet,
chargés **à la demande** quand l'agent explore le répertoire correspondant
(OpenCode découvre les `AGENTS.md` imbriqués en lisant un fichier ou en listant
un dossier).

| Zone | Guide |
| --- | --- |
| Ingestion — Garmin, TCX, Google Health, persistance DB | `domestique_ai/ingestion/AGENTS.md` |
| Traitement — charge, zones HR, tendances, plan déterministe, compliance, comparateur | `domestique_ai/processing/AGENTS.md` |
| Coach LLM — coach, mémoire, génération de plan, décisions | `domestique_ai/llm/AGENTS.md` |
| API & plateforme — routers, scheduler, notifs, export, auth/avatar | `domestique_ai/api/AGENTS.md` |
| Frontend PWA | `frontend/AGENTS.md` |

### Tenir ces guides à jour

Ces `AGENTS.md` sont la documentation de référence du repo : ils **doivent** être
mis à jour **dans le même commit** que le changement de code qu'ils décrivent.

- Une modification de comportement, d'architecture, d'endpoint, de schéma DB ou de
  convention se répercute dans le `AGENTS.md` du sous-paquet concerné (voir la
  carte ci-dessus).
- Un détail propre à un sous-paquet va dans **son** fichier, pas dans la racine.
- Une info transverse (invariant valable partout) va dans **la racine**, qui est
  toujours chargée — c'est le seul fichier visible quel que soit le dossier ouvert.

⚠️ Deux pièges : un `AGENTS.md` imbriqué n'est **pas** rechargé automatiquement
après édition (ouvrir une nouvelle session si le texte doit s'appliquer tout de
suite) ; et quand tu ne sais pas où placer une info, mieux vaut la mettre à la
racine que nulle part.

Un plugin OpenCode (`.opencode/plugins/agents-guide.js`) injecte ce rappel
automatiquement après chaque `write` / `edit` sur un fichier dont le dossier
porte un `AGENTS.md` (une fois par guide et par session). Il ne fait
qu'avertir : il n'édite ni ne bloque rien.

## Commandes courantes

```bash
# Setup (Python ≥ 3.12, testé en 3.12 dans la CI)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Lint + tests (ce que la CI exécute)
ruff check .
pytest

# Lancer un seul test
pytest tests/test_analyzer.py::test_calculate_hr_tss_anchored_to_100_at_threshold
pytest -k "hr_tss"               # filtre par expression
pytest tests/test_garmin.py -x   # stoppe au premier échec

# 1re connexion Garmin Connect (interactif, MFA inclus — seed data/.garmin_tokens)
python -m domestique_ai.export.garmin_connect

# Backend API FastAPI (production / runtime principal)
uvicorn domestique_ai.api.main:app --reload --port 8501

# Frontend React PWA (dev — dans un autre terminal)
cd frontend && npm install && npm run dev   # → http://localhost:5173

# Build complet (FastAPI sert ensuite le bundle React via StaticFiles)
cd frontend && npm run build
uvicorn domestique_ai.api.main:app --port 8501   # → http://localhost:8501
```

## Stack web

L'UI est une **PWA FastAPI + React** (Streamlit a été retiré) :

- `domestique_ai/api/` : FastAPI, un routeur par domaine (metrics, activities,
  morning, objective, garmin, coach, plan). Pydantic v2 pour la sérialisation.
- `frontend/` : React 18 + Vite + TypeScript + Tailwind + recharts + react-leaflet.
  Service worker manuel dans `public/sw.js` (NetworkFirst sur `/api/`).
- Le port runtime est **8501**. En dev, Vite écoute sur 5173 et proxy `/api`
  vers `http://localhost:8501`.
- Le coach LLM streame via **SSE** (`/api/coach/chat`) — `run_turn_stream()`
  yield les events `thinking` / `tool_call` / `tool_result` / `token` au fur
  et à mesure, consommés par `sse-starlette` côté serveur et par
  `consumeSseStream()` côté client.
- ⚠️ **Pas de push Garmin** : le endpoint `POST /api/plan/{id}/push-garmin`
  documenté ici est **fictif** — il n'existe pas dans le code. L'export se fait
  par téléchargement ZIP (fichiers `.FIT`) / ICS depuis la page Plan. Le token
  Garmin est seedé une fois via `python -m domestique_ai.export.garmin_connect`.

## Architecture

Pipeline en 4 couches, chacune isolée dans son sous-package :

```text
config.py  ──►  ingestion/  ──►  processing/  ──►  app/  (UI)
   │                │                │              │
   └─ .env / chemins └─ Garmin + DB  └─ TSS, CTL/ATL/TSB
                              │
                              └────►  llm/  (coach, mémoire, plan LLM)
```

Points structurants à connaître avant de toucher au code :

- **Source de vérité** : SQLite local (`data/strava_activities.db` par défaut — nom historique, override via `DOMESTIQUE_AI_DB_PATH`). Une seule table `activities`, alimentée par 4 sources (colonne `source`) : `garmin` (source d'ingestion courante, `garmin_id` + index unique partiel), `strava` (historique, ingestion supprimée en 09/2026 — Strava exige un abonnement payant pour son API depuis le 1ᵉʳ juillet 2026, `strava_id` UNIQUE), `manual` (saisie à la main) et `tcx` (import de fichiers). Au niveau API/DB, l'id exposé est `external_id` = `coalesce(strava_id, garmin_id, id)` — les activités manuelles/TCX n'ayant aucun id externe, c'est l'id autoincrement local qui fait office d'`external_id` (les ids Garmin/Strava sont à ~10 chiffres, très au-dessus des ids locaux : pas de collision en pratique). Les activités `manual`/`tcx` portent en plus un `source_uid` (sha1 du fichier pour TCX, `NULL` pour le manuel) — clé de dédup, index unique partiel.
- **Écriture en base** : le helper source-agnostique `insert_activity(record, ctx=...)` (`ingestion/db.py`) insère un dict de colonnes explicites et retourne le `rowid`. Utilisé par l'ajout manuel et l'import TCX ; `save_garmin_activity()` garde sa propre insertion idempotente sur `garmin_id`.
- **Migrations douces** : `init_db()` + `_ensure_column()` dans `ingestion/db.py`. Pour ajouter une colonne, étendre le `CREATE TABLE` ET ajouter un appel `_ensure_column()` (sinon les bases existantes ne migreront pas).
- **Pas de cycle d'imports** : `ingestion/db.py` (schéma + helpers de persistance) ne dépend ni du processing ni d'une source d'ingestion — les modules aval (`analyzer`, LLM, routers) peuvent l'importer au top-level. Ne pas remettre du code dépendant d'une source dedans.
- **Toute la config passe par `domestique_ai.config`** : ne jamais lire `os.getenv` ailleurs. Les getters renvoient `None` quand l'env est absent — les modules en aval gèrent le fallback. Les variables `STRAVA_FTP`/`STRAVA_HR_*`/`STRAVA_LTHR_PCT`/`STRAVA_SEX` sont des **paramètres du profil athlète** (nommage historique) — elles ne concernent pas l'API Strava et restent utilisées.
- **Identité & comptes (multi-tenant)** : DB plateforme séparée (`data/platform.db`, `domestique_ai/platform_db.py`) — users, sessions, invitations, lien coach↔athlète, tokens de vérif/reset. Entrée par invitation (`/accept-invite?token=`) ou, si `DOMESTIQUE_AI_SIGNUP_ENABLED=1`, inscription publique (`/signup`, lien coach réutilisable `/accept-invite?coach=`). 2FA TOTP obligatoire pour tout compte à mot de passe (garde middleware). Détail des endpoints/flux : `domestique_ai/api/AGENTS.md`.

## Conventions

- **⚠️ Outils `rtk` (proxy token-killer)** : ne jamais préfixer `pytest`, `ruff`
  ou `vitest` par `rtk` (ex. `rtk pytest …` → « No tests collected »). De plus,
  `source .venv/bin/activate` n'expose pas toujours `pytest` comme binaire dans
  le shell de l'agent. **Toujours lancer les tests/lint via le Python du venv** :
  `.venv/bin/python -m pytest …` et `.venv/bin/python -m ruff check .`.
- **Ruff** : `line-length = 100`, ignore `E501`. Règles activées : `E, F, I, UP, B, SIM` (voir `pyproject.toml`).
- **⚠️ Avant CHAQUE commit** : la CI exige `ruff check .` **et** `ruff format --check .`
  (voir `.github/workflows/ci.yml` — l'étape `lint` fait échouer le build sinon).
  Toujours formater avant de committer :
  ```bash
  .venv/bin/python -m ruff format .
  .venv/bin/python -m ruff check .
  ```
  Ne jamais committer du code non passé par `ruff format` (piège récurrent :
  les blocs réécrits à la main ne respectent pas le format Black-like du projet).
- **Imports** : `from __future__ import annotations` en tête de chaque module Python.
- **Fixtures de test** : utiliser `tmp_path` + `init_db(tmp_path/"x.db")` pour isoler la base. Neutraliser les vars HR via `monkeypatch.delenv("STRAVA_HR_REST", ...)` quand un test cible explicitement la branche TSS power (sinon la config locale du dev peut faire basculer le calcul).
- **⚠️ Toujours une todo** : pour toute tâche non triviale (3+ étapes), maintenir une todo list — une seule tâche `in_progress` à la fois, mise à jour en temps réel (ne cocher `completed` qu'après vérification réelle, jamais sur intention).

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
