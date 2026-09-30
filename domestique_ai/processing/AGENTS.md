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

## Plan déterministe — builder, validator, reprise graduée

Alternative déterministe au générateur LLM (`llm/AGENTS.md`). Exposé via `POST /api/plan/llm` (streamé SSE, semaine par semaine) dont la validation est commune.

**Validation déterministe** : `validate_and_correct()` applique 4 garde-fous par semaine, dans l'ordre :

- **Disponibilité** : suppression des séances hors jours dispo, plafonnement des durées au `max_duration_min` du jour.
- **Repos hebdomadaire** : au plus 6 séances/sem (priorité de coupe : recovery > tempo > intervals > endurance).
- **Polarisation 80/20** : si la part Z4-Z5 dépasse 25 % du temps actif hebdo, conversion des `intervals` les plus courts en `tempo` jusqu'à respect.
- **Plafond TSS hebdo** : `_ctl_progression_cap(CTL, week_idx)` = `max(20, CTL) + 5 × week_idx) × 7`. Au-dessus, raccourcissement de l'endurance la plus longue (plancher 45 min — comportement best-effort si l'input est extrême).

Chaque correction émet une chaîne descriptive dans `adjustments`, ce qui permet à l'UI d'afficher un badge « ajusté » sur la semaine impactée.

Il y a en réalité **6 garde-fous** : aux 4 ci-dessus s'ajoutent la **cadence d'intensité par type** (`_enforce_intensity_cadence`) — sur les semaines de charge (≥ 3 séances, hors récup/taper), le plan doit contenir l'intensité attendue par le type (intervalles chaque semaine pour course/cyclosportive/maintenance ; intervalles 1 sem sur 2 et tempo sinon pour cyclo/forme). Conversion de l'endurance la plus longue — hors jour long — uniquement si le plafond TSS le permet — et la **sortie longue sur le jour dédié** (`_enforce_long_ride`) : la plus longue endurance des semaines de charge est placée sur `long_endurance_day` et portée à ≥ 90 min si le plafond le permet. Le plafond TSS respecte aussi le plancher configurable `DOMESTIQUE_AI_PLAN_MIN_CTL` (défaut 20 — relever à 30-40 pour des semaines plus consistantes à la reprise).

**Reprise graduée (`processing/athlete_state.py`)** — la source de faits du coach. L'intensité n'est plus jamais imposée quand l'athlète est déconditionné :

- `is_deconditioned(ctl, ctl_trend, chronic_tsb, threshold)` : règle composite — `CTL < threshold` (réutilise `DOMESTIQUE_AI_PLAN_MIN_CTL`), **ou** CTL en baisse (7j vs 14j, sortie de coupure), **ou** TSB chronique 7j ≤ −20 (aligné sur `overtraining`).
- `intensity_ceiling(week_idx, ...)` : plafond d'intensité par semaine de reprise — semaine 0 = `base` (Z1-Z2 seulement), semaines de rampe suivantes = `tempo`, puis `full` (cadence normale) une fois la rampe franchie. Longueur de rampe selon le **niveau** de l'athlète (`beginner` 3, `intermediate`/`ex_competitor` 2, `advanced` 1).
- Le **builder** rabote les slots selon le plafond (`plan_builder`), le **validator** ne force plus d'intensité quand `ceiling != full` (`_enforce_intensity_cadence`), et le **prompt LLM** reçoit le bloc `format_state_block` (CTL/ATL/TSB + trajectoire + niveau + compliance + récup) + la consigne de phase pour raisonner sur des faits.
- Le niveau vient du profil athlète (`Profile.level` : `beginner|intermediate|advanced|ex_competitor`, getter `config.get_level`, champ `AthleteContext.level`) — un `ex_competitor` qui reprend garde une rampe mais revient plus vite à l'intensité qu'un débutant, sans jamais sauter les garde-fous.
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

- `sport_bucket` : `outdoor` (Ride, GravelRide, MountainBikeRide, EBikeRide), `indoor` (VirtualRide), ou `other`. On ne compare jamais une sortie route à un home trainer.
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

