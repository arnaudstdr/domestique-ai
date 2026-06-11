# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

### Température météo (avg/min/max par activité)

Colonnes `avg_temp` / `min_temp` / `max_temp` (REAL nullable, °C) calculées à partir du stream Strava `temp` lors de la sync. La réduction est faite par `summarize_temp_stream()` (filtre les valeurs aberrantes hors `-50 °C < t < 60 °C`, garde les zéros légitimes).

- **À l'ingestion** : récupération batchée avec HR (clés `heartrate,time,temp` en un seul appel `fetch_streams_full`) — pas de surcoût API par rapport à l'ingestion HR seule. Si HR n'est pas configuré, les streams ne sont pas téléchargés à la sync.
- **Backfill** : bouton « 🌡️ Backfill temp. » du Dashboard ou `backfill_temperature(client)` — idempotent (filtre `avg_temp IS NULL`), 1 requête Strava par activité, mêmes rate limits que le backfill HR. Les activités sans capteur température (home trainer typique) restent à NULL après backfill.
- **Convention DB** : `NULL` = stream pas encore lu OU activité sans capteur ; pour distinguer les deux, regarder si le backfill a déjà été lancé pour cette activité (`avg_heart_rate IS NOT NULL` est un proxy raisonnable pour « activité outdoor avec capteurs »).
- **Exposition** : `ActivitySummary` et le tool LLM `get_activity_details` retournent `avg_temp_c` / `min_temp_c` / `max_temp_c` quand disponibles, ce qui permet au coach d'expliquer une dérive HR par la chaleur.

### Auto-sync Strava (scheduler APScheduler)

Un `BackgroundScheduler` APScheduler tourne dans le process FastAPI et déclenche `sync_activities()` à intervalle régulier — par défaut **toutes les 30 minutes**. Démarré au `lifespan` startup, arrêté proprement au shutdown.

- **Configuration** :
  - `DOMESTIQUE_AI_AUTO_SYNC_MINUTES` : période en minutes (défaut 30). `0` désactive complètement l'auto-sync.
  - `DOMESTIQUE_AI_AUTO_SYNC_FIRST_RUN_DELAY_MIN` : délai avant le 1er run (défaut 2 min — laisse l'API se stabiliser).
- **Anti-chevauchement** : sync manuel (`POST /api/strava/sync`) et auto-sync passent tous les deux par `_claim_sync()` dans `routers/strava.py`. Tant qu'une sync est en cours (`status == "syncing"`), tout claim concurrent retourne `False` (skip silencieux loggé côté scheduler). `coalesce=True, max_instances=1` côté APScheduler en plus, ceinture + bretelles.
- **Logs et erreurs** : `_auto_sync_job` enveloppe `trigger_sync_blocking` dans un `try/except` global — un job APScheduler qui lève marque le job comme erroné et peut arrêter le scheduler, ce qu'on ne veut surtout pas. Toute exception inattendue est loggée mais n'interrompt pas la cadence.
- **Rate limit** : 30 min × 24 = 48 sync/jour. À vide ≈ 1-2 req Strava chacune, soit ~100 req/jour dans le pire cas — large sous le quota (1000/jour, 100/15 min).
- **Sync incrémentale** : `sync_activities()` dérive automatiquement `after` depuis `MAX(date)` de la table `activities` (moins 1 h de marge). Sans ça, chaque sync re-paginait tout l'historique Strava (~5-15 appels HTTP, jusqu'à 6 min sur Pi 5 quand la base est conséquente). Un appel explicite `sync_activities(client, after=0)` force le re-fetch complet si besoin.

### Notifications push (Pushover) — palier 4 du coach proactif

`domestique_ai/notifications.py` expose deux fonctions best-effort :

- `send_pushover(title, message, priority=None)` : POST sur `api.pushover.net`. No-op silencieux si `PUSHOVER_USER_KEY` ou `PUSHOVER_APP_TOKEN` manque. Toute exception (réseau, 4xx) est loggée en warning et retournée comme `False`.
- `notify_sync_completed(inserted)` : appelée à la fin de `_run_sync` dans le router strava si `inserted > 0`. No-op sur sync à vide (anti-spam). Pluriel/singulier géré.

Le hook dans `_run_sync` (router strava) enveloppe l'appel dans un `try/except` : une notif qui échoue ne doit jamais altérer l'état du sync ni masquer le log de succès.

**Configuration** :
- `PUSHOVER_USER_KEY` + `PUSHOVER_APP_TOKEN` : obligatoires pour activer.
- `PUSHOVER_DEVICE` : optionnel, cible un device précis.
- `PUSHOVER_PRIORITY_DEFAULT` : optionnel, priorité par défaut (clampée -2..2).

**Extension future** : pour ajouter de nouveaux types de notifs (alerte overtraining qui change d'état, séance suggérée du matin), créer une fonction `notify_<event>()` dans le même module qui appelle `send_pushover` avec son propre formattage. Garder le principe : best-effort, jamais bloquant, et anti-spam via comparaison à un état précédent persisté si pertinent.

### Heartbeat Healthchecks.io (dead man's switch)

`domestique_ai/healthcheck.py` expose `ping_healthcheck()` — un GET best-effort sur l'URL Healthchecks.io. Le scheduler (`api/scheduler.py`) ajoute un 2e job APScheduler `healthcheck_ping` qui appelle cette fonction toutes les 5 min (configurable). Le 1er ping est lancé immédiatement au démarrage (`next_run_time=now`) pour que Healthchecks détecte tout de suite que l'app est UP.

**Pourquoi externe** : un watchdog interne au process FastAPI ne peut pas détecter sa propre mort. Healthchecks.io fonctionne en mode "dead man's switch" — c'est leur infra qui te notifie si nos pings s'arrêtent (app crash, Pi éteint, réseau coupé, peu importe la cause). Le canal de notif (Pushover, email, Slack…) se configure dans **leur** UI, pas chez nous.

**Workflow de setup** :
1. Créer un compte sur healthchecks.io.
2. Créer un nouveau check, période 5 min, grace 5 min.
3. Dans le menu Integrations du check, lier Pushover (token user + token app).
4. Copier l'URL de ping (format `https://hc-ping.com/<uuid>`) dans `HEALTHCHECKS_PING_URL` du `.env`.
5. Redémarrer le conteneur. Le check passe en "up" sous 30 s.

**Configuration** :
- `HEALTHCHECKS_PING_URL` : obligatoire pour activer. Sinon job désactivé silencieusement.
- `HEALTHCHECKS_PING_INTERVAL_MIN` : optionnel (défaut 5). Doit correspondre à la "Period" configurée côté Healthchecks.io.

Le job ping est indépendant du job sync — on peut activer l'un sans l'autre (ex. `DOMESTIQUE_AI_AUTO_SYNC_MINUTES=0` + URL Healthchecks définie → seul le heartbeat tourne).

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

### Coach proactif — paliers 1 et 2 (`llm/daily_brief.py`)

Coach qui s'exprime sans être interpellé, en deux étages d'intrusion croissante.

**Palier 1 — Briefing quotidien.** `GET /api/coach/daily-brief` agrège :

- TSB courant + zone (Frais / Optimal / Fatigué / Surentraîné).
- Séance suggérée du jour (via `propose_workout_today`).
- Alerte la plus saillante (priorité TSB chronique / strain > monotony / saut volume > dérive matinale).
- Phrase de synthèse générée par LLM (~25 mots, JSON strict, mode `chat_structured_sync`) avec **fallback déterministe** si Ollama injoignable.

Cache en mémoire avec clé `(date_iso, round(tsb/5), sha1(alerts_sorted))` — un seul appel LLM par jour et par état même si le Dashboard est rouvert. Le cache des jours antérieurs est purgé au passage d'une nouvelle journée. Composant frontal : `DailyBriefCard` en tête du Dashboard avec skeleton loader (le brief peut prendre quelques secondes au premier chargement).

**Palier 2 — Injection contextuelle dans le chat.** `build_initial_messages()` dans `llm/coach.py` ajoute désormais un **message system additionnel** quand `history` est vide (nouvelle session) avec : date, TSB, séance du jour, alerte saillante. Le coach démarre informé sans avoir à appeler ses tools sur la 1re question banale (« comment ça va ? »). Sur les tours suivants, ce contexte n'est **pas** réinjecté — il vit déjà dans la conversation, inutile de gonfler le prompt. Si le builder de contexte échoue (DB vide, Ollama KO), on continue sans contexte plutôt que de bloquer le chat.

**Tests** : 21 tests dans `tests/test_daily_brief.py` (sélection alerte, fallback summary, cache + invalidation par jour, build_coach_context avec/sans alerte/repos) + 4 tests d'injection dans `test_coach.py` (présence/absence selon history, robustesse au crash du builder).

### Génération de plan par LLM (`llm/plan_generator.py` + `processing/plan_validator.py`)

Alternative au builder déterministe (`processing/plan_builder.py`). Exposé via `POST /api/plan/llm` (streamé SSE, semaine par semaine).

Architecture en deux étages :

1. **Génération LLM contrainte** : pour chaque semaine, le LLM ne produit que les choix de haut niveau (`kind`, `duration_min`, `notes`) au format JSON strict validé par Pydantic. Le code reconstruit `structure`/`target_zone`/`estimated_tss` via les helpers du builder déterministe (`_structure_for`, `_TARGET_ZONE`, `_TSS_PER_MIN`). Le LLM ne peut donc pas inventer une structure aberrante (genre 20 min de Z5 d'affilée).
2. **Validation déterministe** : `validate_and_correct()` applique 4 garde-fous par semaine, dans l'ordre :
   - **Disponibilité** : suppression des séances hors jours dispo, plafonnement des durées au `max_duration_min` du jour.
   - **Repos hebdomadaire** : au plus 6 séances/sem (priorité de coupe : recovery > tempo > intervals > endurance).
   - **Polarisation 80/20** : si la part Z4-Z5 dépasse 25 % du temps actif hebdo, conversion des `intervals` les plus courts en `tempo` jusqu'à respect.
   - **Plafond TSS hebdo** : `_ctl_progression_cap(CTL, week_idx)` = `max(20, CTL) + 5 × week_idx) × 7`. Au-dessus, raccourcissement de l'endurance la plus longue (plancher 45 min — comportement best-effort si l'input est extrême).

Chaque correction émet une chaîne descriptive dans `adjustments`, ce qui permet à l'UI d'afficher un badge « ajusté » sur la semaine impactée.

**Fallback** : si la sortie LLM est invalide après 2 tentatives (Ollama injoignable, JSON mal formé, schéma rejeté, workouts vides), la semaine bascule sur le builder déterministe — les autres semaines peuvent rester côté LLM. Le frontend reçoit le `source: "llm" | "fallback"` par semaine.

**Tests** : 22 tests dans `tests/test_plan_generator.py` (mock `chat_structured`, scénarios LLM/fallback/retry) + 21 tests dans `tests/test_plan_validator.py` (chaque garde-fou isolément + cas combinés).

### Comparateur d'activités (`processing/similar.py`)

`GET /api/activities/{strava_id}/similar` retourne les activités passées au profil similaire. Heuristique simple, sans appel Strava ni GPS de départ.

**Signature** : `(sport_bucket, distance, elevation_gain)`.

- `sport_bucket` : `outdoor` (Ride, GravelRide, MountainBikeRide, EBikeRide), `indoor` (VirtualRide), ou `other`. On ne compare jamais une sortie route à un home trainer.
- Distance à ±5 % près en relatif.
- Dénivelé à ±10 % près en relatif.
- Plancher distance 5 km / dénivelé 50 m pour éviter les divisions absurdes sur les très courtes activités.

Pré-filtre SQL sur l'index `idx_activities_distance_elev` (créé à la 1re requête) pour borner le scan, puis filtrage fin Python. Sur la DB courante (~quelques milliers de lignes), latence < 200 ms.

Retour : `{available, reference, matches: [{strava_id, date, duration_sec, training_load, tss_delta_pct, power_delta_pct, ...}], criteria}`. Les `*_delta_pct` sont calculés relativement à la référence (positif = candidate plus grand).

**Exposition coach LLM** : tool `find_similar_activities(strava_id, limit=10)`, déclaré dans `tools.py`. Permet au coach de répondre à « ce col, je l'ai monté combien de fois ? » sans inventer de chiffres.

**Tests** : 16 tests dans `tests/test_similar_activities.py` couvrent tolérances, exclusion indoor/outdoor, delta_pct, tri, limit, plancher distance.

Si l'usage révèle des faux positifs (deux profils différents au même bucket), on ajoutera `start_lat` / `start_lng` à `activities` (migration douce + backfill `fetch_activity_summary`) pour affiner via Haversine.

### Export iCalendar (`export/ics.py`)

`GET /api/plan/{plan_id}/export.ics` retourne le plan au format RFC 5545 importable dans Google Calendar, Apple Calendar et Outlook. Implémentation manuelle sans dépendance externe (~150 lignes : escaping, folding 75 octets, formats `DTSTART`/`DURATION`).

Points à retenir :
- **Floating local time** : les `DTSTART` n'ont ni `TZID` ni suffixe `Z` — le calendrier les interprète dans la timezone de l'utilisateur (« 18 h chez moi »).
- **Créneau par défaut 18 h** : configurable via le paramètre `default_hour` de `plan_to_ics()`. À terme on pourra le déduire des préférences `availability.yaml`.
- **UID stable** (`plan-<id>-<date>@domestique-ai`) : réimporter le fichier met à jour les événements existants au lieu de créer des doublons.
- **CRLF obligatoire** : Outlook refuse l'import si les lignes sont en LF seul (RFC 5545 § 3.1) — `plan_to_ics` produit toujours du CRLF.

23 tests dans `tests/test_ics_export.py` couvrent folding, escaping (`;`, `,`, `\n`), UID stable, CRLF, durations multi-formats.

## Conventions

- **Ruff** : `line-length = 100`, ignore `E501`. Règles activées : `E, F, I, UP, B, SIM` (voir `pyproject.toml`).
- **Imports** : `from __future__ import annotations` en tête de chaque module Python.
- **Fixtures de test** : utiliser `tmp_path` + `init_db(tmp_path/"x.db")` pour isoler la base. Neutraliser les vars HR via `monkeypatch.delenv("STRAVA_HR_REST", ...)` quand un test cible explicitement la branche TSS power (sinon la config locale du dev peut faire basculer le calcul).

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
