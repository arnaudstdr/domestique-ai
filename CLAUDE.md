# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commandes courantes

```bash
# Setup (Python ≥ 3.10, testé en 3.10 et 3.12 dans la CI)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Lint + tests (ce que la CI exécute)
ruff check .
pytest

# Lancer un seul test
pytest tests/test_analyzer.py::test_calculate_hr_tss_anchored_to_100_at_threshold
pytest -k "hr_tss"               # filtre par expression
pytest tests/test_strava.py -x   # stoppe au premier échec

# Flow OAuth Strava (interactif, à exécuter une fois pour générer data/.strava_tokens.json)
python -m domestique_ai.ingestion.strava_oauth_flow

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
  morning, objective, strava, coach, plan). Pydantic v2 pour la sérialisation.
- `frontend/` : React 18 + Vite + TypeScript + Tailwind + recharts + react-leaflet.
  Service worker manuel dans `public/sw.js` (NetworkFirst sur `/api/`).
- Le port runtime est **8501**. En dev, Vite écoute sur 5173 et proxy `/api`
  vers `http://localhost:8501`.
- Le coach LLM streame via **SSE** (`/api/coach/chat`) — `run_turn_stream()`
  yield les events `thinking` / `tool_call` / `tool_result` / `token` au fur
  et à mesure, consommés par `sse-starlette` côté serveur et par
  `consumeSseStream()` côté client.
- Le push Garmin Connect est également streamé en SSE
  (`POST /api/plan/{id}/push-garmin`) — events `start` / `progress` / `result` /
  `done`. Le token Garmin est seedé une fois via `python -m
  domestique_ai.export.garmin_connect`.

## Architecture

Pipeline en 4 couches, chacune isolée dans son sous-package :

```text
config.py  ──►  ingestion/  ──►  processing/  ──►  app/  (UI)
   │                │                │              │
   └─ .env / chemins └─ Strava + DB  └─ TSS, CTL/ATL/TSB
                              │
                              └────►  llm/  (isolé, pas branché à l'UI — voir TODO)
```

Points structurants à connaître avant de toucher au code :

- **Source de vérité** : SQLite local (`data/strava_activities.db` par défaut, override via `DOMESTIQUE_AI_DB_PATH`). Une seule table `activities` avec `strava_id` UNIQUE — toute la pipeline est idempotente sur cette clé.
- **Migrations douces** : `init_db()` + `_ensure_column()` dans `ingestion/strava.py`. Pour ajouter une colonne, étendre le `CREATE TABLE` ET ajouter un appel `_ensure_column()` (sinon les bases existantes ne migreront pas).
- **Cycle d'imports** : `processing/analyzer.py` importe `ingestion.strava.init_db` **localement dans la fonction** (pas en haut de fichier) pour casser un cycle. Conserver ce pattern si du code partagé apparaît.
- **Toute la config passe par `domestique_ai.config`** : ne jamais lire `os.getenv` ailleurs. Les getters renvoient `None` quand l'env est absent — les modules en aval gèrent le fallback.

### Calcul de la charge — le cœur métier

`processing/analyzer.compute_training_load()` choisit la métrique selon les données disponibles :

1. **hr-TSS prioritaire** si `avg_hr` + `STRAVA_HR_REST` + `STRAVA_HR_MAX` sont présents.
   TRIMP exponentiel de Banister, normalisé pour qu'1h à `STRAVA_LTHR_PCT` (0.88 par défaut) de la HRR vaille **exactement 100 points**.
   C'est cet ancrage qui rend le score interchangeable avec un TSS basé puissance — ne pas le casser.
2. **TSS power** sinon, si `avg_power` + `STRAVA_FTP` sont présents.
3. **0.0** sinon (activité sans donnée exploitable).

Conséquences pratiques :

- Modifier le profil HR (`STRAVA_HR_REST`/`STRAVA_HR_MAX`) **ne recalcule pas** automatiquement les scores. C'est `recalculate_training_loads()` (bouton « 🔁 Recalculer la charge » du dashboard) qui rejoue tout.
- Compléter un champ rétroactivement (`max_heart_rate` typiquement) passe par `backfill_activity_fields()` — il refetch tout l'historique Strava et n'écrit que si la valeur change.
- CTL/ATL/TSB sont des EMA (constantes 42j / 7j) calculées sur la grille de **toutes les dates** entre la première et la dernière activité (les jours sans activité comptent comme TSS=0). Voir `calculate_ctl_atl_tsb()`.

### Tendances longues + projection FTP (`processing/trends.py`)

Agrégats saisonniers exposés via `GET /api/metrics/trends?period={3m|6m|1y|all}` et `GET /api/metrics/ftp-projection`. Page front dédiée : `/tendances`.

- **Résolution adaptative** de la courbe CTL/ATL/TSB selon la période : jour pour `3m`, semaine pour `6m` et `1y`, mois pour `all`. On garde la **dernière valeur du bucket** (cohérent avec un EMA cumulatif).
- **Agrégats mensuels** : distance, dénivelé, durée, séances, TSS. Couvre tous les mois entre la 1re activité de la période et aujourd'hui (mois sans activité = 0). Le comparatif N-1 (`distance_km_n1`, `tss_n1`) est tiré du même calcul sur l'année précédente — `null` si pas de donnée alignée.
- **Distribution Z1-Z5 par mois** : pourcentage du temps total HR ventilé. Une activité dont toutes les colonnes `hr_zX_time` sont `NULL` n'est pas comptée (sinon on diluerait la part). Les mois sans aucune ventilation renvoient `null` sur les `zN_pct`.
- **Projection FTP** : `+1 % de FTP par +5 points de CTL net sur 28 jours, plafonné à ±5 %`. La FTP courante vient de `config.get_ftp()` (profile YAML > `STRAVA_FTP` > 250 W par défaut). Si `delta_ctl_28d` n'est pas calculable (historique vide), `projected_ftp` est `None` et `delta_pct` reste à 0.
- **Confiance qualitative** (`low/medium/high`) :
  - `high` quand ≥ 60 j d'historique CTL ET part Z4-Z5 ∈ [4 %, 25 %] sur les 28 derniers jours (stimulus seuil/VO2max plausible).
  - `medium` quand ≥ 28 j d'historique.
  - `low` sinon.

### Zones HR (temps par zone)

Chaque activité est ventilée en 5 zones %HRR (Karvonen) — colonnes `hr_z1_time` … `hr_z5_time` (secondes) :

- Z1 : <60% HRR (récup) · Z2 : 60-70% (endurance) · Z3 : 70-80% (tempo) · Z4 : 80-90% (seuil) · Z5 : ≥90% (VO2max).
- Bornes en dur dans `processing/analyzer._HR_ZONE_BOUNDS`. La fonction `calculate_hr_zones(hr_stream, time_stream, hr_rest, hr_max)` consomme les streams Strava `heartrate` + `time`.
- Les pauses Strava (saut > 5 s entre deux samples) ne sont pas comptabilisées (constante `_HR_ZONE_PAUSE_GAP_SEC`).
- Convention DB : `NULL` = non calculé (sera traité par le backfill) ; `0.0` = calculé mais aucune seconde dans cette zone.

À l'ingestion (`sync_activities`), si `STRAVA_HR_REST` + `STRAVA_HR_MAX` sont configurés, un appel `GET /activities/{id}/streams` est fait par activité avec `avg_heart_rate` non null. Pour rattraper l'historique : bouton « 📥 Backfill zones HR » du dashboard ou `backfill_hr_zones(client)` — idempotent (filtre `hr_z1_time IS NULL`), 1 requête Strava par activité, attention aux rate limits (100 req / 15 min, 1000 / jour).

### OAuth Strava

`StravaClient.from_tokens_file()` est le point d'entrée standard côté code. Il :

- lit `data/.strava_tokens.json` (jamais commité),
- déclenche un refresh automatique si `expires_at <= now + 60s`,
- repersiste les tokens.

Le flow interactif initial (`strava_oauth_flow.py`) n'est lancé qu'une fois — ensuite, le refresh token suffit à la vie de l'app.

Gestion des erreurs API :

- `StravaAuthError` pour 401 / token absent.
- 429 → backoff via `Retry-After` puis retry (déjà géré dans `fetch_activities`).

### Coach LLM — `domestique_ai/llm/`

Coach conversationnel via Ollama (modèle par défaut `gemma4:31b-cloud`, override `OLLAMA_MODEL`). Branché sur le dashboard dans l'onglet « Coach ».

Architecture :

```text
ollama_client.py    # wrapper SDK ollama : chat(messages, tools, think=True) → dict
tools.py            # TOOL_SCHEMAS (JSON) + TOOLS (fonctions Python) + dispatch()
coach.py            # SYSTEM_PROMPT + run_turn() : boucle tool-calling (max 5 itérations)
objectives.py       # load_objective() / save_objective() — YAML data/objective.yaml
conversations.py    # persistance SQLite (table conversations) + new_session_id()
```

Règle d'or : **le LLM n'invente jamais de chiffre**. Le `SYSTEM_PROMPT` impose d'appeler un tool avant toute affirmation chiffrée (CTL, TSB, zones, distance, etc.). Les 6 tools exposent les données calculées par notre code Python.

Pour ajouter un tool :

1. Écrire la fonction Python dans `tools.py` (signature explicite, retourne un dict JSON-sérialisable).
2. Ajouter son schéma JSON dans `TOOL_SCHEMAS` (description claire, paramètres typés).
3. L'enregistrer dans le dict `TOOLS`. `dispatch()` route automatiquement.
4. Tester sur DB tmp dans `tests/test_tools.py` (pas de réseau, pas de LLM).

Mode `thinking` activé sur le 1ᵉʳ tour de tool-calling (fiabilise la décision d'appeler les tools sur gemma3/4), désactivé sur les tours suivants pour gagner du temps. Les deltas de raisonnement sont streamés au client et affichés dans l'expander « 🧠 Raisonnement » de la page Coach (debug).

Persistance : chaque message (user / assistant / tool) est stocké en JSON brut dans la table `conversations` (clé `session_id`, ordre par `id`). Reprise d'une session via le sélecteur de la page Coach.

Objectif : `data/objective.yaml` (gitignoré, template `data/objective.yaml.example`). Lu par le tool `get_objective`. Champs : `type` (cyclosportive/course/cyclo/maintenance), `date`, `distance_km`, `elevation_m`, `target_ftp`, `notes`. Override du chemin via `DOMESTIQUE_AI_OBJECTIVE_PATH` (utile pour les tests).

## Conventions

- **Ruff** : `line-length = 100`, ignore `E501`. Règles activées : `E, F, I, UP, B, SIM` (voir `pyproject.toml`).
- **Imports** : `from __future__ import annotations` en tête de chaque module Python.
- **Fixtures de test** : utiliser `tmp_path` + `init_db(tmp_path/"x.db")` pour isoler la base. Neutraliser les vars HR via `monkeypatch.delenv("STRAVA_HR_REST", ...)` quand un test cible explicitement la branche TSS power (sinon la config locale du dev peut faire basculer le calcul).
