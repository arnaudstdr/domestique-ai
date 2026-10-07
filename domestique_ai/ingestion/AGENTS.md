# AGENTS.md — `domestique_ai/ingestion/`

Ingestion des activités (Garmin, TCX, saisie manuelle) et des métriques de
récupération (Google Health, Garmin Santé), et couche de persistance SQLite.

Guide racine (invariants globaux, DB, conventions) : `AGENTS.md`.
Couches aval : `processing/AGENTS.md`, `llm/AGENTS.md`, `api/AGENTS.md`.

> Le schéma de la table `activities` et les helpers de persistance vivent dans
> ce sous-paquet (`ingestion/db.py`) mais leurs invariants sont cross-cutting :
> voir « Points structurants » du `AGENTS.md` racine (source de vérité,
> `insert_activity()`, `_ensure_column()`, absence de cycle d'imports).

`init_db()` crée aussi les tables de la mémoire du coach (`coach_memory`,
`session_summaries`, `memory_vectors`, `session_finalize_state`) — leur
sémantique est décrite dans `llm/AGENTS.md` (mémoire persistante). En
particulier, `session_finalize_state` borne les tentatives de finalisation
(une session dont le résumé échoue n'est plus resoumise en boucle).

## Ingestion Garmin Connect (source d'activités)

Les activités sont ingérées depuis l'**API non officielle Garmin Connect** (module `garminconnect`) via `ingestion/garmin.py`. Le compteur Edge / la montre synchronisent vers Garmin Connect, et `sync_activities_garmin()` rapatrie les activités dans la même table `activities` — toute la pipeline aval (TSS, CTL/ATL/TSB, zones HR, tendances, coach LLM) fonctionne sans changement.

- **Connexion par athlète** (isolation multi-tenant) : chaque athlète connecte **son** compte Garmin depuis les Réglages (`POST /api/garmin/connect` → email/mot de passe, puis `POST /api/garmin/connect/mfa` si MFA). Les credentials sont stockés par athlète en DB plateforme (`users.garmin_email`/`garmin_password`, jamais exposés par `_user_dict`) et les tokens dans `data/athletes/<public_id>/.garmin_tokens` (`config.garmin_token_dir_for(ctx)`). **Cache MFA en mémoire process** (`_pending_mfa`, TTL 5 min) — l'état SSO du SDK n'est pas sérialisable ; suppose 1 worker uvicorn.
- **Fallback bootstrap** : `GARMIN_EMAIL`/`GARMIN_PASSWORD`/`GARMIN_TOKEN_DIR` du `.env` ne concernent que le compte bootstrap (propriétaire) et son `data/.garmin_tokens` legacy ; le seed CLI `python -m domestique_ai.export.garmin_connect` reste dispo pour lui.
- **Endpoints** : `POST /api/garmin/sync` (sync manuel en tâche de fond), `GET /api/garmin/sync-status`, `GET /api/garmin/status` (état de connexion, scopé athlète), `POST /api/garmin/connect`, `POST /api/garmin/connect/mfa`, `POST /api/garmin/disconnect`.
- **Sync incrémentale** : la fenêtre par défaut démarre 1 j avant la dernière activité Garmin connue (ou 3 ans d'historique au 1er sync). Mapping `typeKey` Garmin → `sport_type` (nomenclature historique type Strava, `_SPORT_MAP`) pour conserver les buckets indoor/outdoor du comparateur.
- **Espace athlète supprimé pendant la sync** : `init_db()` (qui fait le `mkdir` du dossier `data/athletes/<public_id>/`) est appelé **avant** `_last_garmin_activity_date()` dans `sync_activities_garmin` — une base neuve échouait sinon au premier accès. Si le dossier disparaît en cours de route (compte supprimé, purge d'orphelins, incident volume), `save_garmin_activity()` / `_last_garmin_activity_date()` lèvent `AthleteSpaceRemovedError` au lieu d'un `sqlite3.OperationalError` opaque ; le router (`_run_sync`) l'attrape en **warning** (pas d'alerte Sentry) et abandonne sans recréer de dossier orphelin (« abortir proprement »). Cf. `api/AGENTS.md` — auto-sync Garmin.
- **⚠️ Endpoints non officiels** : peuvent changer sans préavis.

## Zones HR à l'ingestion

À l'ingestion (`sync_activities_garmin`), la source des zones dépend du sport :

- **Vélo** (`Ride`, `VirtualRide`, `GravelRide`, `MountainBikeRide`, `EBikeRide` — set `_CYCLING_SPORT_TYPES`) : les valeurs **Garmin** sont prioritaires via `client.get_activity_hr_in_timezones(id)` (`/activity/{id}/hrTimeInZones`, parsing défensif `parse_hr_time_in_zones()`). L'athlète a aligné ses zones Garmin sur notre référentiel. **Repli** sur le calcul local `calculate_hr_zones()` si Garmin ne renvoie rien exploitable.
- **Autres sports** : calcul local `calculate_hr_zones()` (leurs zones Garmin ne sont pas alignées).

Le calcul local consomme les séries HR/temps de `get_activity_details` extraites par `parse_details_streams()` (parsing défensif, trois orientations : moderne `metricDescriptors` + `activityDetailMetrics` — shape réelle observée 09/2026 —, et legacy `metricsEntries` par métrique ou par échantillon). Cet appel détails reste nécessaire de toute façon pour la **température**. Quand Garmin et le local sont tous deux disponibles (vélo + `STRAVA_HR_REST`/`HR_MAX`), un warning est logué si les totaux divergent de plus de `_ZONE_DELTA_WARN_PCT` (validation de correspondance).

⚠️ Les zones Garmin ne sont **pas rétroactives** : seules les **nouvelles** activités utilisent la source Garmin ; l'historique déjà ingéré (calcul local) n'est pas backfillé. Les lignes restées à `NULL` (parsing cassé avant la correction 09/2026) restent rattrapables par le backfill one-off (voir « Champs enrichis » ci-dessous).

Définition des zones et algorithme (`calculate_hr_zones`, bornes) : voir `processing/AGENTS.md`.

## Champs enrichis + streams + météo Garmin (09/2026)

- **Champs enrichis** : au sync, le payload liste Garmin fournit aussi `activityName`, `calories`, `maxPower`, cadence moy/max (unités hétérogènes bike rpm / run pas-min stockées telles quelles), vitesse moy/max (m/s), `elevationLoss`, point de départ (`start_lat`/`start_lng` — consommés par le comparateur `processing/similar.py` pour exiger un départ proche, Haversine ≤ 500 m). Colonnes dédiées (`name`, `calories`, `max_power`, `cadence_avg`, `cadence_max`, `speed_avg`, `speed_max`, `elevation_loss`, `start_lat`, `start_lng`), migrées par `_ensure_column`.
- **Streams** : `GET /api/activities/{external_id}/streams` sert les courbes + la carte de la page détail, depuis **deux sources** : (1) activités Garmin → fetch live `get_activity_details(id, maxpoly=1000)`, parsing `parse_details_series()` (HR, temps, temp, **power, altitude, speed, distance, latlng**), cache mémoire 1 h (les streams Garmin ne sont PAS persistés par le sync) ; (2) activités importées TCX → lecture de la table `activity_streams` (streams persistés à l'import). Activités Strava legacy ou manuelles → 404 explicite.
- **Streams persistés (base des montées)** : en complément du live, le backfill one-off `python -m domestique_ai.ingestion.backfill_streams --all` télécharge les détails des activités Garmin **sans streams** et persiste des séries **index-aligned** (`parse_details_aligned()` — trous `None` conservés, contrairement à `parse_details_series()`) compactées à ~1 point/5 s (`compact_aligned_series()`, max 4000 pts) dans `activity_streams`. Flag `garmin_streams_backfill_done` (`sync_meta`) posé après un passage sans erreur ; sélection « activités sans streams » → **reprenable**. Alimente la détection de montées (`processing/climbs.py`). `--rebuild-only` rejoue le rebuild des montées **sans réseau** (migration de schéma / backfill des tracés sur les segments existants) et n'exige pas de token Garmin.
- **Tracé GPS des cartes de liste** : persisté dans `map_polyline` **au sync normal** pour les activités avec HR (l'appel `get_activity_details` est déjà en main pour zones/temp — `geoPolylineDTO.polyline` → downsample ≤ 300 pts → `encode_polyline()`, si `hasPolyline` et distance ≥ 1 km). Les activités sans HR n'ont pas de tracé sur la carte (détails non appelés), **ni de comparaison par tracé dans le comparateur** (`processing/similar.py` décode + rééchantillonne `map_polyline` pour un Fréchet discret). Sans ce remplissage, toute sortie synchronisée après le backfill one-off resterait à `NULL` et afficherait « — » sur `RoutePreview`.
- **Météo** : `GET /api/activities/{external_id}/weather` → `get_activity_weather` Garmin (temp/apparent/dew point **en °F, convertis °C** par `parse_activity_weather()`, humidité, vent, descriptif, station METAR). Best-effort : erreur Garmin → `available: false`, jamais de 5xx.
- **Backfill one-off** : `backfill_garmin_fields()` re-fetch la liste sur la fenêtre des lignes Garmin en base et `UPDATE` les champs enrichis ; pour les activités avec GPS, un appel détails récupère en plus le tracé (`geoPolylineDTO.polyline` → downsample ≤ 300 pts → **polyline encodée Google** via `encode_polyline()`, consommée par `RoutePreview` sur les cartes de liste) et rattrape zones HR + temp des lignes encore à `NULL`. Déclenché automatiquement au premier sync après déploiement — flag `garmin_fields_backfill_done` dans la table `sync_meta` (posé seulement en cas de succès, retry au sync suivant sinon). Jamais bloquant pour la sync.
- **Durée entière (`duration`)** : la colonne est déclarée `INTEGER` et le sync Garmin insère désormais `int(duration)` (plus la valeur brute REAL du payload). Une migration douce dans `init_db` normalise en `INTEGER` les lignes héritées (`UPDATE … WHERE typeof(duration) != 'integer'`, no-op une fois corrigée). Côté lecture, `processing/similar.py` caste aussi défensivement `duration_sec` en `int` (défense en profondeur pour les bases non encore migrées).

## Température météo (avg/min/max par activité)

Colonnes `avg_temp` / `min_temp` / `max_temp` (REAL nullable, °C) calculées à partir du stream de température renvoyé par `get_activity_details` Garmin. La réduction est faite par `summarize_temp_stream()` (dans `ingestion/db.py` ; filtre les valeurs aberrantes hors `-50 °C < t < 60 °C`, garde les zéros légitimes).

- **À l'ingestion** : récupérée en même temps que les zones HR (même appel de détails) — pas de surcoût réseau par rapport à l'ingestion HR seule. Si HR n'est pas configuré, les détails ne sont pas téléchargés à la sync.
- **Convention DB** : `NULL` = détails pas encore lus OU activité sans capteur température (home trainer typiquement) ; pour distinguer les deux, `avg_heart_rate IS NOT NULL` est un proxy raisonnable pour « activité avec capteurs ».
- **Exposition** : `ActivitySummary` et le tool LLM `get_activity_details` retournent `avg_temp_c` / `min_temp_c` / `max_temp_c` quand disponibles, ce qui permet au coach d'expliquer une dérive HR par la chaleur.

## Ajout manuel + import TCX (sources `manual` / `tcx`)

Deux voies d'ajout hors Garmin, exposées dans l'en-tête de la page **Activités** (cartes inline `ActivityCreateForm.tsx` / `TcxImportForm.tsx`, pattern de la saisie matinale) :

- **Saisie manuelle** — `POST /api/activities` (modèle `ActivityCreate`) : date, sport, durée, distance, D+, FC moy, puissance moy, nom. Le TSS est calculé côté serveur par `compute_training_load()` (hr-TSS prioritaire, sinon TSS puissance). `source='manual'`, aucun stream → la page détail n'affiche que les métriques.
- **Import TCX** — `POST /api/activities/import/tcx` (multipart `list[UploadFile]`, `python-multipart` déjà en dépendance) : un ou plusieurs fichiers, un fichier pouvant contenir plusieurs `<Activity>`. Parser maison `ingestion/tcx.py` (`parse_tcx()`, stdlib `xml.etree` + matching par local-name car les namespaces varient) → agrégats (durée, distance, D+/D−, FC, watts, cadence, calories, tracé polyline) + streams. Zones HR recalculées par `calculate_hr_zones()` (séries HR/temps alignées) si `STRAVA_HR_REST`/`MAX` configurés. `source='tcx'`, streams persistés dans `activity_streams`. Dédup par sha1 du contenu (`source_uid = "<sha1>:<index>"`) : réimporter le même fichier → `skipped`. Un fichier en erreur n'interrompt pas les autres (résultat détaillé par fichier). Limites anti-DoS : `DOMESTIQUE_AI_TCX_MAX_FILE_MB` (défaut 5 Mo/fichier — rejet `status="error"` par fichier) et `DOMESTIQUE_AI_TCX_MAX_FILES` (défaut 20 — HTTP 413 au-delà) ; le middleware global borne le corps entrant (`DOMESTIQUE_AI_MAX_REQUEST_BODY_MB`, défaut 64, cf. `api/AGENTS.md`).
- **Suppression** — `DELETE /api/activities/{external_id}` : réservée aux `source IN ('manual','tcx')` (403 sinon — une ligne Garmin/Strava serait recréée au prochain sync). Bouton corbeille sur la page détail.

L'édition des activités (toutes sources) est décrite dans `api/AGENTS.md`.

## Google Health API — données bracelet (Fitbit / Pixel Watch)

L'intégration lit les métriques de récupération depuis la **Google Health API**
(successeur cloud de la Fitbit Web API). Elle alimente automatiquement la page
« Santé » (anciennement « Matin », route `/sante`) : HRV, FC repos, sommeil +
stades, SpO2, fréquence respiratoire, température cutanée, pas et calories
actives.

> **Nommage UI vs domaine** : dans l'interface, la page s'appelle « Santé »
> (route `/sante`, composant `frontend/src/pages/Morning.tsx`, onglet `HeartPulse`
> dans `BottomNav`). Le **backend et le domaine restent nommés `morning`** —
> `/api/morning`, `api/routers/morning.py`, schémas `Morning*`, client TS
> `api.morning` — volontairement non renommés (gros refactor sans valeur
> utilisateur). Ne pas « aligner » le backend sur le nom UI sans accord.

Deux scores sont recalculés localement :

- **Sleep score** (0-100) : durée, efficacité, qualité (deep/REM), continuité.
- **Readiness score** (0-100) : HRV et FC repos vs baseline 14 j + sommeil.

La saisie manuelle reste possible ; un `sleep_score` saisi à la main n'est pas
écrasé par le score calculé (`sleep_score_computed=0`).

**Isolation par athlète** : les tokens sont stockés **par athlète**
(`data/athletes/<public_id>/.google_health_tokens.json`, cf.
`config.google_health_tokens_path_for(ctx)` — `data/.google_health_tokens.json`
pour le bootstrap). Le `state` OAuth est **signé HMAC** (`_sign_state` /
`_verify_state` dans le router) et encode le `public_id` : le callback Google
(redirection navigateur, hors Bearer) retrouve ainsi l'athlète destinataire sans
stockage serveur. Les credentials OAuth de l'app
(`GOOGLE_HEALTH_CLIENT_ID`/`_SECRET`) restent, eux, globaux.

**Fichiers clés** :

- `domestique_ai/ingestion/google_health.py` — client OAuth2 + API + mapping.
- `domestique_ai/api/routers/google_health.py` — endpoints auth/callback/sync.
- `domestique_ai/processing/morning_metrics.py` — scores calculés.

**Configuration** (`.env`) :

```bash
GOOGLE_HEALTH_CLIENT_ID=...
GOOGLE_HEALTH_CLIENT_SECRET=...
GOOGLE_HEALTH_REDIRECT_URI=http://localhost:8501/api/google-health/callback
DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES=360
```

**Setup Google Cloud** :

1. Créer un projet et activer l'API **Google Health API**.
2. Configurer l'écran de consentement OAuth (type **External**).
3. Ajouter les scopes restreints :
   - `googlehealth.profile.readonly`
   - `googlehealth.settings.readonly`
   - `googlehealth.activity_and_fitness.readonly`
   - `googlehealth.health_metrics_and_measurements.readonly`
   - `googlehealth.sleep.readonly`
4. Créer des credentials OAuth 2.0 de type **Web application** avec les
   redirect URIs autorisés (localhost + production).
5. Lancer le flow depuis la page `/sante` ou via
   `GET /api/google-health/auth`.
6. Soumettre à la **review de vérification Google** pour les scopes restreints.
   En attendant, ajouter ton compte comme test user pour développer.

**Auto-sync** : un job APScheduler supplémentaire récupère les 7 derniers jours
toutes les 6 heures par défaut. Il est indépendant du sync Garmin.

## Garmin Health — métriques de récupération via la montre Garmin

Alternative à Google Health, **même sink** (`morning_metrics`) et **mêmes
scores locaux** (parité de comportement) : `ingestion/garmin_health.py` lit la
santé depuis le compte Garmin déjà connecté (activités) et alimente la page
Santé. Repli sur les valeurs embarquées dans `dailySleepDTO` (`avgSleepHRV`,
`avgSpO2`, `avgRespirationValue`) si un endpoint dédié ne répond pas.

- **Mapping** : sommeil + stades + `sleep_stages_json` (hypnogramme) via
  `get_sleep_data` (clé = date de réveil, comme Google) ; HRV via
  `get_hrv_data` ; FC repos / pas / calories / body battery via `get_stats` ;
  SpO2 via `get_spo2_data` ; respiration via `get_respiration_data` ; poids via
  `get_body_composition` (range) ; readiness natif via
  `get_morning_training_readiness`. La température cutanée n'est pas exposée de
  façon fiable → laissée `None`.
- **Colonnes bonus** (valeurs natives Garmin, hors calculs, affichées en info
  secondaire) : `garmin_sleep_score`, `garmin_readiness_score`,
  `garmin_body_battery_min/max`.
- **Fenêtre** : 7 j par défaut, **30 j max** (`MAX_SYNC_DAYS`) — API non
  officielle, appels séquentiels (~6/jour + 1 range poids).
- **Endpoints** : `POST /api/garmin/health/sync?days=` (synchrone, 404 si
  Garmin non connecté, 502 sinon) ; statut agrégé dans
  `GET /api/morning/sources`. Statut durable dans `sync_meta` de la base
  athlète (`garmin_health_last_sync_at` / `garmin_health_last_error`).
- **Auto-sync** : job `garmin_health_auto_sync` (défaut 6 h,
  `DOMESTIQUE_AI_GARMIN_HEALTH_AUTO_SYNC_MINUTES=0` pour couper), ciblé sur
  les athlètes avec tokens Garmin, plus pré-syncs dans le check du matin et la
  revue hebdo (symétrie Google Health).

### Provenance et préférence de provider (Garmin vs Google Health)

- `morning_metrics.source` trace le provider automatique de la ligne
  (`garmin` | `google_health` | `NULL` = saisie manuelle/historique).
- Préférence par athlète dans `sync_meta` (`health_provider` ∈
  `auto|garmin|google_health`, helpers `get/set_health_provider` +
  `resolve_health_provider` dans `processing/morning_metrics.py`) :
  **`auto` = Garmin prioritaire**, Google Health ne remplit que les jours sans
  données Garmin (`source` absente ou `google_health`) ; `garmin` coupe la sync
  auto Google des métriques ; `google_health` court-circuite la sync Garmin.
- **Aucun provider n'écrase l'autre avec `None`** : le payload d'écriture
  commun (`build_provider_morning_payload`) complète les métriques absentes du
  fetch par la valeur existante, et la SQL préserve `source`, `garmin_*` et
  `sleep_stages_json` via `COALESCE`.
- Un jour Garmin **sans métrique de récupération** (pesage seul) passe par
  `set_weight()` (upsert ciblé) : la ligne garde la provenance de l'autre
  provider, qui continue de la remplir.
- Scores manuels (`sleep_score_computed=0`, `stress_score_computed != 1`)
  toujours préservés, quel que soit le provider.
