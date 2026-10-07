# AGENTS.md — `domestique_ai/processing/`

Cœur métier déterministe : calcul de charge, zones HR, tendances, plan
(builder/validator/reprise), compliance, comparateur d'activités.

Guide racine (invariants globaux, DB, conventions) : `AGENTS.md`.
Couches voisines : `ingestion/AGENTS.md`, `llm/AGENTS.md`, `api/AGENTS.md`.

## Calcul de la charge — le cœur métier

`processing/analyzer.compute_training_load()` choisit la métrique selon les données disponibles :

1. **hr-TSS prioritaire** si `avg_hr` + `STRAVA_HR_REST` + `STRAVA_HR_MAX` sont présents.
   TRIMP exponentiel de Banister, normalisé pour qu'1h à `STRAVA_LTHR_PCT` (0.88 par défaut) de la HRR vaille **exactement 100 points**.
   C'est cet ancrage qui rend le score interchangeable avec un TSS basé puissance — ne pas le casser.
2. **TSS power** sinon, si `avg_power` + `STRAVA_FTP` sont présents.
3. **0.0** sinon (activité sans donnée exploitable).

Conséquences pratiques :

- Modifier le profil HR (`STRAVA_HR_REST`/`STRAVA_HR_MAX`) ou FTP (`STRAVA_FTP`) **ne recalcule pas** les scores en direct, mais `PUT /api/profile` planifie automatiquement `recalculate_training_loads()` en tâche de fond dès que l'un de ces champs (ou `sex`/`lthr_pct`) change. L'endpoint `POST /api/metrics/recalculate` reste disponible pour rejouer tout manuellement (scripts/tests).
- CTL/ATL/TSB sont des EMA (constantes 42j / 7j) calculées sur la grille de **toutes les dates** entre la première et la dernière activité (les jours sans activité comptent comme TSS=0). Voir `calculate_ctl_atl_tsb()`.

## Zones HR (temps par zone)

Chaque activité est ventilée en 5 zones %HRR (Karvonen) — colonnes `hr_z1_time` … `hr_z5_time` (secondes) :

- Z1 : <60% HRR (récup) · Z2 : 60-70% (endurance) · Z3 : 70-80% (tempo) · Z4 : 80-90% (seuil) · Z5 : ≥90% (VO2max).
- Bornes en dur dans `processing/analyzer._HR_ZONE_BOUNDS`. La fonction `calculate_hr_zones(hr_stream, time_stream, hr_rest, hr_max)` consomme les séries `heartrate` + `time` extraites des streams d'ingestion.
- Les pauses d'enregistrement (saut > 5 s entre deux samples) ne sont pas comptabilisées (constante `_HR_ZONE_PAUSE_GAP_SEC`).
- Convention DB : `NULL` = non calculé ; `0.0` = calculé mais aucune seconde dans cette zone.
- `hr_zone_bpm_ranges(hr_rest, hr_max)` convertit ces bornes en bpm (Karvonen) — utilisé par le tool `get_profile` pour verbaliser les zones.

**Quelle source à l'ingestion** : zones Garmin prioritaires pour le vélo, repli sur `calculate_hr_zones()` sinon — détail dans `ingestion/AGENTS.md`.

## Tendances longues + projection FTP (`processing/trends.py`)

Agrégats saisonniers exposés via `GET /api/metrics/trends?period={3m|6m|1y|all}` et `GET /api/metrics/ftp-projection`. Page front dédiée : `/tendances`.

- **Résolution adaptative** de la courbe CTL/ATL/TSB selon la période : jour pour `3m`, semaine pour `6m` et `1y`, mois pour `all`. On garde la **dernière valeur du bucket** (cohérent avec un EMA cumulatif).
- **Agrégats mensuels** : distance, dénivelé, durée, séances, TSS. Couvre tous les mois entre la 1re activité de la période et aujourd'hui (mois sans activité = 0). Le comparatif N-1 (`distance_km_n1`, `tss_n1`) est tiré du même calcul sur l'année précédente — `0.0` si le mois N-1 est couvert par l'historique mais sans activité (la courbe N-1 reste continue), `null` seulement s'il tombe hors de l'historique de l'athlète.
- **Distribution Z1-Z5 par mois** : pourcentage du temps total HR ventilé. Une activité dont toutes les colonnes `hr_zX_time` sont `NULL` n'est pas comptée (sinon on diluerait la part). Les mois sans aucune ventilation renvoient `null` sur les `zN_pct`.
- **Projection FTP** : `+1 % de FTP par +5 points de CTL net sur 28 jours, plafonné à ±5 %`. La FTP courante vient de `config.get_ftp()` (profile YAML > `STRAVA_FTP` > 250 W par défaut). Si `delta_ctl_28d` n'est pas calculable (historique vide), `projected_ftp` est `None` et `delta_pct` reste à 0.
- **Confiance qualitative** (`low/medium/high`) :
  - `high` quand ≥ 60 j d'historique CTL ET part Z4-Z5 ∈ [4 %, 25 %] sur les 28 derniers jours (stimulus seuil/VO2max plausible).
  - `medium` quand ≥ 28 j d'historique.
  - `low` sinon.
- **Exposition coach LLM** : tool `get_training_trends(period, weeks, include_ftp_projection)` (`llm/tools.py`) — mêmes agrégats que la page, pour répondre aux questions d'évolution sans inventer de chiffres.

## Plan déterministe — builder, validator, reprise graduée

Alternative déterministe au générateur LLM (`llm/AGENTS.md`). Exposé via `POST /api/plan/llm` (streamé SSE, semaine par semaine) dont la validation est commune.

**Validation déterministe** : `validate_and_correct()` applique 4 garde-fous par semaine, dans l'ordre :

- **Disponibilité** : suppression des séances hors jours dispo, plafonnement des durées au `max_duration_min` du jour.
- **Repos hebdomadaire** : au plus 6 séances/sem (priorité de coupe : recovery > tempo > intervals > endurance).
- **Polarisation 80/20** : si la part Z4-Z5 dépasse 25 % du temps actif hebdo (plafond relevé à 40 % pour un `racer`), conversion des `intervals` les plus courts en `tempo` jusqu'à respect.
- **Plafond TSS hebdo** : `_ctl_progression_cap(CTL, week_idx)` = `max(20, CTL) + 5 × week_idx) × 7`. Au-dessus, raccourcissement de l'endurance la plus longue (plancher 45 min — comportement best-effort si l'input est extrême).

Chaque correction émet une chaîne descriptive dans `adjustments`, ce qui permet à l'UI d'afficher un badge « ajusté » sur la semaine impactée.

Il y a en réalité **6 garde-fous** : aux 4 ci-dessus s'ajoutent la **cadence d'intensité par type** (`_enforce_intensity_cadence`) — sur les semaines de charge (≥ 3 séances, hors récup/taper), le plan doit contenir l'intensité attendue par le type (intervalles chaque semaine pour course/cyclosportive/maintenance ; intervalles 1 sem sur 2 et tempo sinon pour cyclo/forme). Conversion de l'endurance la plus longue — hors jour long — uniquement si le plafond TSS le permet — et la **sortie longue sur le jour dédié** (`_enforce_long_ride`) : la plus longue endurance des semaines de charge est placée sur `long_endurance_day` et portée à ≥ 90 min si le plafond le permet. Le plafond TSS respecte aussi le plancher configurable `DOMESTIQUE_AI_PLAN_MIN_CTL` (défaut 20 — relever à 30-40 pour des semaines plus consistantes à la reprise).

**Reprise graduée (`processing/athlete_state.py`)** — la source de faits du coach. L'intensité n'est plus jamais imposée quand l'athlète est déconditionné :

- `is_deconditioned(ctl, ctl_trend, chronic_tsb, threshold)` : règle composite — `CTL < threshold` (réutilise `DOMESTIQUE_AI_PLAN_MIN_CTL`), **ou** CTL en baisse (7j vs 14j, sortie de coupure), **ou** TSB chronique 7j ≤ −20 (aligné sur `overtraining`).
- `intensity_ceiling(week_idx, ...)` : plafond d'intensité par semaine de reprise — semaine 0 = `base` (Z1-Z2 seulement), semaines de rampe suivantes = `tempo`, puis `full` (cadence normale) une fois la rampe franchie. Longueur de rampe selon le **niveau** de l'athlète (`beginner` 3, `intermediate`/`ex_competitor` 2, `advanced`/`racer` 1).
- Le **builder** rabote les slots selon le plafond (`plan_builder`), le **validator** ne force plus d'intensité quand `ceiling != full` (`_enforce_intensity_cadence`), et le **prompt LLM** reçoit le bloc `format_state_block` (CTL/ATL/TSB + trajectoire + niveau + compliance + récup) + la consigne de phase pour raisonner sur des faits.
- Le niveau vient du profil athlète (`Profile.level` : `beginner|intermediate|advanced|ex_competitor|racer`, getter `config.get_level`, champ `AthleteContext.level`) — un `ex_competitor` qui reprend garde une rampe mais revient plus vite à l'intensité qu'un débutant, sans jamais sauter les garde-fous.
- **Coaching renforcé (`racer` = compétiteur en activité)** — leviers dérivés du niveau via `athlete_state` (`polarization_cap_for_level`, `tss_cap_multiplier_for_level`, `extra_intervals_for_level`) : plafond de polarisation relevé (25 % → 40 %), plafond TSS hebdo majoré (×1.25), et une séance d'intervalles supplémentaire visée sur les semaines à intervalles (`_enforce_extra_intervals` dans le validator, convertit des `tempo` si le plafond le permet). Les autres niveaux gardent les garde-fous nus (0.25 / ×1.0 / +0).
- `build_coach_state(ctx, today, ...)` agrège l'état réel (best-effort, ne lève jamais) pour alimenter le prompt, la revue hebdo et, à terme, le check du matin sur les mêmes faits.

**Périodisation pilotée par le type d'objectif** — `processing/plan_builder._OBJECTIVE_FLAVORS` : `target_event_type` actionne 3 leviers (fenêtre de taper, fréquence des intervalles, surpondération de l'endurance longue) :

- `course` / `cyclosportive` : taper 2 sem, intervalles chaque semaine, endurance neutre.
- `cyclo` : taper 1 sem, intervalles 1 sem sur 2, endurance ×1.2 (volume avant tout).
- `forme` (retour en forme / base, sans échéance de course) : **pas de taper**, volume Z2 prioritaire, intensité réintroduite progressivement selon l'état réel (tempo puis intervalles), pas de décharge finale.
- `maintenance` : pas de taper, intervalles chaque semaine, endurance ×0.95 (routine allégée).

Le générateur LLM (`plan_generator`, voir `llm/AGENTS.md`) reçoit le même profil (fenêtre de taper + consigne d'intention dans le prompt via `_training_emphasis`).

**Tests** : 21 tests dans `tests/test_plan_validator.py` (chaque garde-fou isolément + cas combinés) + extensions `tests/test_plan_builder.py` (reprise = pas de Z4 semaine 1, cadence non forcée à CTL bas) et `tests/test_athlete_state.py` (règle composite + plafond gradué + bloc d'état).

## Compliance (`processing/compliance.py`)

Utilisée par la revue hebdomadaire (`llm/AGENTS.md`) : statut par séance
(fait / partiel / manqué / repos coach) et TSS planifié vs réalisé. Une décision
de repos du check du matin est traitée comme **repos coach**, pas comme une
séance manquée. Tests : `tests/test_compliance.py`.

## Alertes de dérive matinale (`processing/morning_metrics.py`)

`detect_morning_alerts()` compare la dernière valeur de chaque métrique à sa
baseline glissante 14 j ; une alerte est levée au-delà de
`DEFAULT_ALERT_THRESHOLD_PCT` (10 %) **dans le sens défavorable**
(`_ALERT_DIRECTION` : baisse mauvaise pour HRV/sommeil/SpO2, hausse mauvaise
pour FC repos/stress/respiration/température, 0 = jamais alerté pour
pas/calories/poids). La sévérité passe `critical` à ≥ 2× le seuil.

- `METRIC_LABELS` est la **source de vérité unique** du mapping
  métrique → libellé humain (aligné sur la page Santé : « Freq. resp. »,
  « FC repos », « HRV »…). Ne pas re-coder ces libellés ailleurs.
- `format_morning_alert(alert)` produit le message affiché
  (`« Freq. resp. ↑ +10.2% vs baseline (11.3 le 2026-10-01) »`), avec repli sur
  le nom brut si la métrique est inconnue. Utilisé par
  `llm/daily_brief.py` (carte Dashboard) et `api/routers/metrics.py`
  (`GET /api/metrics/overtraining`) — toute évolution du format passe par ce
  helper, jamais par un f-string dupliqué.
- `GET /api/morning` expose les alertes **structurées** (`MorningAlert` avec
  `metric` brut) : la métrique reste en snake_case dans le payload, seul le
  message destiné à l'UI est humanisé.

## Providers automatiques de métriques (Garmin / Google Health)

`processing/morning_metrics.py` est le **sink partagé** des deux providers :
écriture via `build_provider_morning_payload()` (scores locaux identiques,
préservation des scores manuels) puis `save_morning_entry()`, qui préserve
`source`, `garmin_*` et `sleep_stages_json` par `COALESCE` — **un provider
n'écrase jamais l'autre avec `None`** (un fetch partiel complète les métriques
existantes). La provenance de la ligne (`morning_metrics.source`) et la
préférence athlète (`get/set_health_provider`, `resolve_health_provider` —
`auto` = Garmin prioritaire, Google ne remplit que les trous) vivent ici aussi.
Détail des pipelines : `ingestion/AGENTS.md`.

## Suivi du poids + rapport poids/puissance (W/kg)

Le poids est une **métrique de la table `morning_metrics`** (`weight_kg REAL`,
nullable) — source de vérité unique, par athlète (base isolée). La table
historique orpheline `weight_history` n'est plus utilisée.

- **Saisie** : champ « Poids » du formulaire Santé (manuel) **et** ingestion
  auto Google Health (`DATA_TYPE_WEIGHT = "weight"`, `weightGrams / 1000`,
  même scope OAuth que le reste). Au sync, un poids saisi à la main est
  préservé si la balance ne fournit rien ce jour-là (`weight_kg` ajouté à la
  préservation des champs manuels à côté de `stress_score`/`notes`).
- **Pas d'alerte** de dérive sur le poids (`_ALERT_DIRECTION["weight_kg"] = 0`) :
  variabilité quotidienne normale.
- **Upsert ciblé** : `set_weight(date, kg)` (et `PUT /api/morning/weight`)
  n'écrit QUE `weight_kg` — contrairement à `save_morning_entry` qui écrase
  toutes les colonnes. Indispensable pour ne pas effacer HRV/sommeil d'un jour
  lors d'une saisie du poids depuis les réglages.
- **W/kg dérivé** (jamais stocké) : `latest_weight()` donne le dernier poids
  connu, `power_to_weight(puissance, poids)` le rapport. Exposé dans
  `GET /api/morning/weight` (`{weight_kg, date, ftp_w, wkg}`), la projection FTP
  (`current_wkg`/`projected_wkg`, poids courant — approximation v1, pas de poids
  historique par activité), le bloc d'état du coach et le tool
  `get_morning_trends` (`weight_kg` + `wkg`). UI : Réglages (« Infos perso »,
  champ + W/kg), Santé (formulaire + graphes), détail d'activité
  (« Poids/puissance », puissance moy. / poids actuel).

## Comparateur d'activités (`processing/similar.py`)

`GET /api/activities/{external_id}/similar` retourne les activités passées au profil similaire. Heuristique simple, sans appel API distante.

**Critères dans l'ordre** (tous les critères GPS sont « si disponible » : un hard filter n'est appliqué que quand la donnée existe des deux côtés, sinon on retombe sur distance + dénivelé) :

- `sport_bucket` (`processing/activity_classify.py`) : `outdoor` (Ride, GravelRide, MountainBikeRide, EBikeRide), `indoor` (VirtualRide), ou `other`. On ne compare jamais une sortie route à un home trainer.
- Distance à ±5 % près en relatif.
- Dénivelé à ±10 % près en relatif.
- Plancher distance 5 km / dénivelé 50 m pour éviter les divisions absurdes sur les très courtes activités.
- **Départ** (`start_lat`/`start_lng`) : distance Haversine ≤ 500 m (`_START_PROXIMITY_M`).
- **Tracé** (`map_polyline`) : Fréchet discret ≤ 500 m (`_TRACK_TOLERANCE_M`), tracés rééchantillonnés à 64 points (`_TRACK_RESAMPLE_POINTS`) pour un coût `O(n²)` borné.

Pré-filtre SQL sur l'index `idx_activities_distance_elev` (créé à la 1re requête) pour borner le scan, puis filtrage fin Python. Sur la DB courante (~quelques milliers de lignes), latence < 200 ms.

Retour : `{available, reference: {..., has_gps, has_track}, matches: [{external_id, date, duration_sec, training_load, start_distance_m, track_distance_m, tss_delta_pct, power_delta_pct, ...}], criteria}`. Les `*_delta_pct` sont calculés relativement à la référence (positif = candidate plus grand) ; `start_distance_m`/`track_distance_m` valent `None` quand la donnée manque d'un côté.

Les helpers géo (`haversine_m`, `decode_polyline`, `resample_polyline`, `discrete_frechet_m`) vivent dans `processing/geo.py` (purs, sans dépendance) et sont testés dans `tests/test_geo.py`.

⚠️ **Couverture GPS** : `map_polyline` n'est persistée que pour les activités **avec HR** (l'appel détails Garmin est conditionné à la HR) — les sorties sans HR et les activités manuelles/Strava legacy n'ont pas de tracé et passent donc le filtre tracé. `start_lat`/`start_lng` sont plus larges (payload liste Garmin + TCX).

**Exposition coach LLM** : tool `find_similar_activities(external_id, limit=10)`, déclaré dans `tools.py`. Permet au coach de répondre à « ce col, je l'ai monté combien de fois ? » sans inventer de chiffres.

**Tests** : `tests/test_similar_activities.py` couvre tolérances, exclusion indoor/outdoor, delta_pct, tri, limit, plancher distance, cast de durée REAL, et les filtres départ/tracé (même départ+tracé → matche ; départ ou tracé éloigné → exclu ; candidat/référence sans GPS → fallback).

## Types de sortie & mix (`processing/activity_classify.py`, `activity_stats.py`)

Classification **pure** partagée (`activity_classify.py`) : `sport_bucket(sport_type)` → `indoor`/`outdoor`/`other`, et `infer_kind_from_zones(z_times, avg_hr)` → `recovery|endurance|tempo|intervals` (ou `None` si zones absentes). Utilisée par `today.py` (dernière séance), `similar.py`, `compliance.py` et `activity_stats.py` — ne pas dupliquer ces heuristiques.

`activity_stats.get_activity_mix_stats(days, group_by, include_monthly)` agrège séances, durée, distance, D+ et charge par bucket :

- `group_by="sport"` (défaut) → `by_sport` (compatibiité tool existant) ; `"kind"` → `by_type` (kind inféré des zones HR, `unknown` si non ventilé) ; `"indoor"` → `by_type` (indoor/outdoor/other).
- `include_monthly=True` ajoute `monthly` (mois × type) borné à la fenêtre `days` — sert aux questions « est-ce que mes sorties longues progressent ? ».

**Exposition coach LLM** : tool `get_activity_mix(days, group_by, include_monthly)` (`llm/tools.py`) — l'ancien comportement (sport) reste la valeur par défaut. Tests : `tests/test_activity_stats.py` + extensions `tests/test_tools.py`.

## Montées (`processing/climbs.py`)

Détection des cols/bosses récurrents à partir des **streams persistés** (backfill Garmin aligné / TCX) — aucune donnée réseau.

- **Détection** (`detect_climbs`, pur) : lissage d'altitude (moyenne glissante ±2), pente par segment, points « en montée » à pente ≥ 2 % (hystérésis), interruptions < 150 m fusionnées, puis filtre **pente moyenne ≥ 3 %, D+ ≥ 40 m, longueur ≥ 800 m**. Sortie : longueur, D+, pente moy/max, durée, VAM, FC/puissance moy, coordonnées départ/arrivée, **tracé encodé** (`map_polyline`, ≤ 120 points, `encode_polyline` de `processing/geo.py`).
- **Appariement** (`rebuild_climbs`, idempotent) : même montée si départ **et** arrivée ≤ 250 m + longueur ± 30 % ; les **noms des segments survivent** au rebuild. Réécrit `climb_efforts` puis les compteurs (`efforts_count`, `first_seen`, `last_seen`). Au passage, un segment existant sans tracé (migration) est **complété** depuis une détection GPS (`traces_backfilled`) — jamais écrasé, le nom n'est pas touché. Un changement de seuils nécessite un rebuild.
- **Tables** : `climb_segments` (nom nullable, géométrie, stats, `map_polyline`) + `climb_efforts` (UNIQUE `segment_id`+`activity_id`), créées par `init_db` ; `delete_activity` purge les efforts de l'activité et rafraîchit les compteurs (les segments nommés survivent).
- **Nommage** : aucun géocodage → l'utilisateur nomme depuis la page « Montées » (`PUT /api/climbs/{id}`) ; le coach liste les montées sans nom (`unnamed_segments`). Le tracé sert à identifier la montée (carte dépliable côté front) ; il est **retiré du payload du tool coach** (`climb_report`).
- **Exposition** : tool `get_climb_stats(name, limit)` ; API `GET /api/climbs`, `GET/PUT /api/climbs/{id}` (`api/AGENTS.md`). Données **dérivées** → exclues de l'export RGPD, comme `activity_streams`.
- **CLI** : `python -m domestique_ai.ingestion.backfill_streams --all` (backfill + rebuild automatique ; `--no-rebuild` pour dissocier ; `--rebuild-only` pour rejouer le rebuild seul, sans réseau — migration de schéma / backfill des tracés). Tests : `tests/test_climbs.py`, `tests/test_garmin_aligned.py`, `tests/test_climbs_api.py`.

## Records de puissance (`processing/records.py`)

Best efforts et découplage Pw:HR calculés **à la volée** depuis les streams persistés (aucune table dédiée).

- **Best efforts** (`best_effort_watts`) : moyenne de puissance maximale sur fenêtre glissante (5 s, 1 min, 5 min, 20 min, 1 h), pondérée par le temps réel entre échantillons (pas fixe ~5 s) ; fenêtre retenue seulement si elle couvre ≥ 95 % de la durée. Séries « pause » > 30 s plafonnées.
- **Découplage** (`decoupling_pct`) : `(EF1 − EF2) / EF1` où `EF = puissance/FC` par moitié — positif = FC qui dérive à puissance égale ; exige ≥ 20 min et FC des deux moitiés. Exposé par `get_activity_details` (`decoupling_pct`, best-effort si streams présents).
- **Rapport** (`records_report`) : un record par durée standard + tendance seuil (meilleur 20 min par année), ou top efforts d'une durée ciblée (`duration_min`) avec meilleur par année. Périodes `3m/6m/1y/all`.
- **Exposition** : tool `get_best_efforts(period, duration_min)`. Puissance absente des streams → `available: false` avec raison. Tests : `tests/test_records.py`.

