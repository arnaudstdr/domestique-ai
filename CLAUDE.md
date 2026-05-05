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

# Dashboard
streamlit run domestique_ai/app/dashboard.py
```

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

### `llm/assistant.py` — non branché

Le wrapper Mistral existe mais **n'est pas appelé par le dashboard**. Toute intégration LLM à venir doit lire le contexte depuis `fetch_activities_from_db()` / `calculate_ctl_atl_tsb()` plutôt que re-requêter Strava.

## Conventions

- **Ruff** : `line-length = 100`, ignore `E501`. Règles activées : `E, F, I, UP, B, SIM` (voir `pyproject.toml`).
- **Imports** : `from __future__ import annotations` en tête de chaque module Python.
- **Fixtures de test** : utiliser `tmp_path` + `init_db(tmp_path/"x.db")` pour isoler la base. Neutraliser les vars HR via `monkeypatch.delenv("STRAVA_HR_REST", ...)` quand un test cible explicitement la branche TSS power (sinon la config locale du dev peut faire basculer le calcul).
