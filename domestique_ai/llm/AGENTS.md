# AGENTS.md — `domestique_ai/llm/`

Coach LLM : boucle conversationnelle, mémoire persistante, génération de plan,
et décisions proactives.

Guide racine (invariants globaux, DB, conventions) : `AGENTS.md`.
Couches voisines : `ingestion/AGENTS.md`, `processing/AGENTS.md`, `api/AGENTS.md`.

## Coach LLM

Coach conversationnel via Ollama (modèle par défaut `gemma4:31b-cloud`, override `OLLAMA_MODEL`). Branché sur le dashboard dans l'onglet « Coach ».

Architecture :

```text
ollama_client.py    # wrapper SDK ollama : chat(messages, tools, think=True) → dict
tools.py            # TOOL_SCHEMAS (JSON) + TOOLS (fonctions Python) + dispatch()
coach.py            # SYSTEM_PROMPT + run_turn() : boucle tool-calling (max 5 itérations)
objectives.py       # load_objective() / save_objective() — YAML data/objective.yaml
conversations.py    # persistance SQLite (table conversations) + new_session_id()
memory.py           # mémoire persistante : faits, résumés, RAG (cf. ci-dessous)
```

Règle d'or : **le LLM n'invente jamais de chiffre**. Le `SYSTEM_PROMPT` impose d'appeler un tool avant toute affirmation chiffrée (CTL, TSB, zones, distance, etc.). Les tools exposent les données calculées par notre code Python.

Positionnement : coach **cycliste et assistant santé**. Au-delà de l'entraînement vélo, il conseille sur la nutrition, le sommeil, la récupération et le renforcement, à partir de ses connaissances générales — ancrées sur les données réelles de l'athlète (`get_nutrition_context`, `get_activity_mix`) mais sans jamais présenter un repère général comme une mesure de l'athlète. Pas de disclaimer médical systématique (ton neutre). Activités hors vélo : `propose_workout(sport=...)` gère renfo/gainage, cross-training et mobilité (conseil ponctuel — **non planifié** dans le plan).

Pour ajouter un tool :

1. Écrire la fonction Python dans `tools.py` (signature explicite, retourne un dict JSON-sérialisable).
2. Ajouter son schéma JSON dans `TOOL_SCHEMAS` (description claire, paramètres typés).
3. L'enregistrer dans le dict `TOOLS`. `dispatch()` route automatiquement.
4. Tester sur DB tmp dans `tests/test_tools.py` (pas de réseau, pas de LLM).

Mode `thinking` activé sur le 1ᵉʳ tour de tool-calling (fiabilise la décision d'appeler les tools sur gemma3/4), désactivé sur les tours suivants pour gagner du temps. Les deltas de raisonnement sont streamés au client et affichés dans l'expander « 🧠 Raisonnement » de la page Coach (debug).

Persistance : chaque message (user / assistant / tool) est stocké en JSON brut dans la table `conversations` (clé `session_id`, ordre par `id`). L'UI présente **un fil unique** fusionnant toutes les sessions ; en interne le fil est découpé en **sessions invisibles** : `current_or_new_session()` rattache le message à la session courante et en ouvre une nouvelle si le fil est inactif depuis `SESSION_IDLE_FINALIZE_MINUTES` (rotation transparente). `load_thread_page()` pagine le fil toutes sessions confondues (`before` / `after` / `anchor`).

**Titres de session : supprimés.** La génération de titre (`generate_session_title`) et le backfill au démarrage (`_backfill_session_titles`) ont été **retirés** — plus de sélecteur côté UI, c'était de la conso Ollama gaspillée à chaque reboot. `list_sessions()` reste (endpoint legacy `GET /api/coach/sessions`, `title` toujours `None`) ; la table `session_titles` n'est plus lue ni écrite.

## Observabilité des appels Ollama (`llm/usage.py`)

Chaque appel au SDK Ollama est tracé (best-effort) dans la table `llm_calls` de `platform.db` — alimente la vue « Usage Ollama » du panneau admin (tokens, latence, coût, erreurs).

- **Point unique** : `llm/ollama_client.py` lit les métriques du SDK (`prompt_eval_count`, `prompt_eval_cached_count`, `eval_count`, `total_duration`, `load_duration`, `eval_duration`) et appelle `usage.record_llm_call()` en `finally` (statut `ok`/`error`, `error_type`). Ne pas ajouter d'appel Ollama qui court-circuite ce wrapper, sous peine de trou dans l'observabilité.
- **`label`** : chaque entrypoint (`stream_chat`, `chat_structured`/`_sync`, `embed_texts`/`_sync`) accepte un `label` (constantes `usage.COACH_CHAT`, `PLAN_WEEK`, `SESSION_SUMMARY`, `FACTS_EXTRACT`, `DAILY_BRIEF`, `WORKOUT_TODAY`, `DECISION_REASON`, `WEEKLY_REVIEW_REASON`, `EMBED_*`). Toujours le renseigner au call-site — c'est le « pourquoi » agrégé dans l'admin.
- **Attribution athlète** : `ContextVar` `usage._llm_actor`, posé via `usage.llm_attribution(public_id)` au niveau du **middleware** ASGI (chemin requête, `api/main.py`) et des **boucles du scheduler** (`api/scheduler.py`). ⚠️ Ne **pas** le poser dans `get_athlete_context` (dépendance *sync* → threadpool : le contextvar serait perdu).
- **Invariant** : `record_llm_call` ne lève **jamais** (une panne d'observabilité ne doit pas casser un appel LLM) ; l'absence de contexte → `actor` `NULL` (normal).

**Config** : clé Cloud via `OLLAMA_API_KEY` (`config.get_ollama_api_key()`, passée au client). Requise pour les modèles `-cloud`, inutile en local. Le palier **Free** Ollama couvre `gemma4:31b-cloud`. Aucune API Ollama n'expose le % de quota (feature requests ouvertes) : le pourcentage affiché côté admin est une **estimation pondérée calibrée**, jamais une lecture.

## Objectif de l'athlète

Objectif : `data/objective.yaml` (gitignoré, template `data/objective.yaml.example`). Lu par le tool `get_objective`. Champs : `type` (cyclosportive/course/cyclo/maintenance), `date`, `distance_km`, `elevation_m`, `target_ftp`, `notes`. Override du chemin via `DOMESTIQUE_AI_OBJECTIVE_PATH` (utile pour les tests).

## Mémoire persistante du coach (`llm/memory.py`)

Le coach garde une mémoire **entre les sessions**, à 3 étages, dans le SQLite de l'athlète (`ctx.db_path`) :

- **Faits durables** (`coach_memory`) : préférences, contraintes/blessures, objectifs, accords, perso. Toujours injectés dans le prompt système. Population par l'outil `remember_fact` (explicite) **et** extraction auto en fin de session. Dédup par similarité (cosine > 0.9 → mise à jour).
- **Résumés épisodiques** (`session_summaries`) : un résumé par session. **Résumé roulant** tous les `SESSION_SUMMARY_EVERY_MESSAGES` messages (défaut 8) ; **finalisation** (résumé final + extraction de faits) quand une session est inactive > `SESSION_IDLE_FINALIZE_MINUTES` (défaut 45, job APScheduler `finalize_sessions`), **ou** automatiquement à la rotation du fil (nouveau chunk ouvert par `POST /api/coach/chat`), ou sur appel explicite `POST /api/coach/sessions/{id}/finalize` (conservé pour tests/clients). Le garde-fou `last_summarized_message_id` évite les régénérations.
- **RAG** (`memory_vectors`) : index de retrieval unifié (messages, résumés, faits). Embeddings via Ollama (`OLLAMA_EMBED_MODEL`, défaut `nomic-embed-text` — `ollama pull nomic-embed-text`), cosine brute-force numpy (numpy déjà tiré par pandas). `build_memory_block(query)` assemble faits + 5 derniers résumés + top-4 passages pertinents, avec budget de contexte.

Intégration : `build_initial_messages()` injecte le bloc mémoire en message `system` **à chaque tour** ; `run_turn_stream()` le calcule une fois par tour. L'historique verbatim de session est plafonné à `MAX_HISTORY_MESSAGES` (24) — le résumé roulant prend le relais. Outils exposés : `remember_fact` (écriture), `search_conversations` (lecture RAG).

**Économie d'embeddings** : `get_relevant_memory` court-circuite l'appel si `memory_vectors` n'a aucune ligne du type demandé (athlète sans historique), et les embeddings de requête sont mémoïsés (LRU `(model, query)`, déterministes — `clear_embedding_cache()` pour les tests). L'indexation et l'extraction de faits passent en **lot** : `index_messages_batch` pour le tour user+assistant (un appel), `remember_facts_batch` pour les faits extraits (un appel pour N faits, dédup cosine conservée y compris entre faits du lot).

CRUD + UI : `GET/POST /api/coach/memory`, `PUT/DELETE /api/coach/memory/{id}`, composant `frontend/src/components/MemoryPanel.tsx` (section « Mémoire du coach » dans `/profil`, lien depuis la page Coach). `DELETE /api/coach/sessions/{id}` purge résumés + vecteurs ; les **faits durables survivent** (`source_session_id` nullifié). Backfill one-off de l'historique via flag `memory_backfill_done` (`sync_meta`), déclenché par le job scheduler. Tests : `tests/test_memory.py`, `tests/test_memory_api.py`, extensions `test_coach.py` / `test_scheduler.py`.

## Coach proactif — paliers 1 et 2 (`llm/daily_brief.py`)

Coach qui s'exprime sans être interpellé, en deux étages d'intrusion croissante.

**Palier 1 — Briefing quotidien.** `GET /api/coach/daily-brief` agrège :

- TSB courant + zone (Frais / Optimal / Fatigué / Surentraîné) + CTL/ATL (repris des signaux de `propose_workout_today`, recalculés sur jour off).
- Séance suggérée du jour (via `propose_workout_today`). La clé du cache
  `today_suggestions` inclut l'override de disponibilité (`available_min`) :
  une suggestion calculée pour 60 min n'est jamais servie pour une demande à
  120 min.
- Alerte la plus saillante (priorité TSB chronique / strain > monotony / saut volume > dérive matinale). Les dérives matinales sont formatées côté `processing/morning_metrics.format_morning_alert` (libellés humains de `METRIC_LABELS`) — jamais de nom de colonne brut affiché à l'athlète.
- **Enrichissements hero** : `sleep_history` (7 j, `StepPoint` `{date, hours}`), `week_tss_planned`/`week_tss_done` (compliance de la semaine courante via `compute_week_compliance`) — accompagnés de `week_adherence_pct` et des statuts `week_done`/`week_partial`/`week_missed`/`week_skipped` (repos coach) ; l'adhérence reste `None` si aucune séance n'est planifiée cette semaine (pas de « 0 % » trompeur) — et `coach_tip` (2ᵉ phrase actionnable).
- Phrase de synthèse **+ conseil** générés par LLM (~25 mots / ~15 mots, JSON strict `{summary, tip}`, mode `chat_structured_sync`) avec **fallbacks déterministes** (`_build_fallback_summary` / `_build_fallback_tip`) si Ollama injoignable — un `coach_tip` n'est jamais vide.

Cache **persistant** (table `daily_brief_cache` du SQLite athlète, `llm/brief_cache.py`) avec clé `(date_iso, bucket TSB, sha1(alertes))` — un seul appel LLM par jour et par état, **y compris après un redémarrage du process** (l'ancien cache mémoire régénérait au boot) ; les jours antérieurs sont purgés à chaque écriture.

Composant frontal : `DailyBriefCard` en tête du Dashboard (hero). Refonte visuelle : **anneau TSB** (`TsbGauge`, SVG animé, couleur par zone), **halo d'ambiance** teinté par l'état, **avatar coach** (`CoachAvatar`, halo pulsant), barre séance (durée + TSS estimé), **mini-barres sommeil** (`SleepBars`, repère baseline), ligne `coach_tip`, et **surface d'alerte unique** (primaire visible + secondaires dépliables — la carte « Signaux d'alerte » séparée a été supprimée). Animations gated `prefers-reduced-motion`. Le nom affiché dans la salutation vient du contexte `MeProvider` (`hooks/useMe.tsx`) — un seul appel `/me` partagé, plus de fetch par page.

**Palier 2 — Injection contextuelle dans le chat.** `build_initial_messages()` dans `llm/coach.py` ajoute désormais un **message system additionnel** quand `history` est vide (nouvelle session) avec : date, TSB, séance du jour, alerte saillante. Le coach démarre informé sans avoir à appeler ses tools sur la 1re question banale (« comment ça va ? »). Sur les tours suivants, ce contexte n'est **pas** réinjecté — il vit déjà dans la conversation, inutile de gonfler le prompt. Si le builder de contexte échoue (DB vide, Ollama KO), on continue sans contexte plutôt que de bloquer le chat.

**Tests** : `tests/test_daily_brief.py` (sélection alerte, fallback summary + tip, cache + invalidation par jour, champs hero ctl/atl/week_tss/sleep_history, build_coach_context avec/sans alerte/repos) + 4 tests d'injection dans `test_coach.py` (présence/absence selon history, robustesse au crash du builder).

## Génération de plan par LLM (`llm/plan_generator.py`)

Génération LLM contrainte du plan, en complément du builder déterministe
(`processing/plan_builder.py`). Exposé via `POST /api/plan/llm` (streamé SSE,
semaine par semaine).

**Génération LLM contrainte** : pour chaque semaine, le LLM ne produit que les choix de haut niveau (`kind`, `duration_min`, `notes`) au format JSON strict validé par Pydantic. Le code reconstruit `structure`/`target_zone`/`estimated_tss` via les helpers du builder déterministe (`_structure_for`, `_TARGET_ZONE`, `_TSS_PER_MIN`). Le LLM ne peut donc pas inventer une structure aberrante (genre 20 min de Z5 d'affilée).

La **validation déterministe** (`validate_and_correct()`, garde-fous, reprise
graduée, périodisation) est décrite dans `processing/AGENTS.md` — c'est elle qui
borne la sortie du LLM.

**Génération LLM enrichie** : `GenerationContext` (plan_generator) porte
désormais TSB, readiness médiane, dérive HRV et compliance de la semaine écoulée
+ **ATL, trajectoire CTL (7j vs 14j), TSB chronique, niveau (`level`) et
`coach_state`** (l'agrégat `athlete_state.build_coach_state`) — injectés dans
`_build_user_prompt` (bloc « État réel ») pour que le LLM **raisonne sur des
faits**. `ceiling_for(week_idx)` décide du plafond d'intensité de chaque semaine
(reprise → base/tempo → normal). Un `racer` (compétiteur en activité) reçoit en
outre une consigne de prompt (volume/intensité soutenus, jusqu'à 2 séances Z4-Z5)
hors reprise (`_level_guidance`). Le tool LLM `review_week` expose le rapport de
semaine en lecture (le coach explique un ajustement sans inventer de chiffres).
`compose_upcoming_week` expose la composition d'une seule semaine (réutilisée
par la revue hebdo).

**Niveau connu du coach conversationnel** : le niveau de l'athlète
(`beginner|intermediate|advanced|ex_competitor|racer`) est injecté dans le bloc
de contexte initial (`daily_brief.build_coach_context`, 1ᵉʳ tour de session) **et**
dans le bloc mémoire (`memory.build_memory_block`, à chaque tour) via
`athlete_state.level_label`, pour que le coach adapte ton et prudence.

**Fallback** : si la sortie LLM est invalide après 2 tentatives (Ollama injoignable, JSON mal formé, schéma rejeté, workouts vides), la semaine bascule sur le builder déterministe — les autres semaines peuvent rester côté LLM. Le frontend reçoit le `source: "llm" | "fallback"` par semaine.

**Tests** : 22 tests dans `tests/test_plan_generator.py` (mock `chat_structured`, scénarios LLM/fallback/retry) + `test_plan_generator.py` (prompt état réel, ceiling reprise).

## Plan adaptatif — check du matin + revue hebdomadaire

Le plan n'est plus un artefact fixe de 4 semaines : il **roule** et s'adapte aux
données réelles via deux boucles, toutes deux avec fallback déterministe
(le LLM ne décide jamais hors bornes, il ne fait que rédiger les raisons).

**Check du matin (quotidien)** — `llm/daily_decision.py` + job
`scheduler._daily_morning_check_job` (CronTrigger, défaut **08:00 local** via
`DOMESTIQUE_AI_DAILY_CHECK_HOUR`/`MINUTE` et `DOMESTIQUE_AI_SCHEDULER_TZ`,
`-1` = off). Le job fait d'abord un **pre-sync Google Health** (hier→aujourd'hui,
idempotent) + **pre-sync Garmin** pour garantir des données fraîches, puis
`evaluate_daily_decision()` :

- Signaux : entrée `morning_metrics` du jour (HRV, sommeil, readiness), baseline
  14 j, alertes morning + overtraining, TSB, séance prévue du plan.
- Décision par règles : **rest** si alerte critical / readiness < 30 / sommeil < 5 h ;
  **adjust** si readiness < 50 / sommeil < 6,5 h / qualité de sommeil basse
  (`sleep_score < 60` sous 7 h) / TSB < −10 / dérive morning ;
  **go** sinon. Le sommeil est un signal renforcé (seuil d'allègement remonté
  à 6h30, qualité via `sleep_score`), et la dérive sommeil vs baseline 14 j est
  déjà couverte par les alertes morning (≥ 10 % → allègement, ≥ 20 % → repos).
  `adjust` = downgrade kind (intervals→tempo→endurance) + durée −25 %.
- **Répercussion dans le plan** : décision ≠ go persistée dans `plan_decisions`
  (`llm/plan_storage.save_day_decision`), cache `today_suggestions` invalidé.
  Le Plan affiche « REPOS (coach) » ou « allégée » ; la compliance la traite
  comme repos coach (pas une séance manquée). Override manuel via
  `POST /api/plan/decision`. Aucune notification Pushover pour ce check.
- **Coût LLM** : la décision `go` ne déclenche **aucun** appel LLM (la raison
  déterministe suffit, elle n'est pas persistée) ; le LLM ne rédige que
  `adjust`/`rest`, avec schéma JSON passé au SDK (`chat_structured(schema=…)` →
  `format=` contraint le décodage). Une décision **déjà persistée identique**
  n'est pas réécrite par les recalculs `use_llm=False` du dashboard (la raison
  raffinée par le job du matin est préservée).

**Revue hebdomadaire** — `llm/weekly_review.py` + job `scheduler._weekly_review_job`
(CronTrigger, défaut **dimanche 18h** local via `DOMESTIQUE_AI_WEEKLY_REVIEW_DAY`/
`HOUR`, `0` = off) + bouton « Adapter le plan » / `POST /api/plan/weekly-review` :

1. Pre-sync Google Health 7 j + rapport `collect_week_report()` : compliance de la
   semaine écoulée (`processing/compliance.py` : fait/partiel/manqué/repos coach,
   TSS planifié vs réalisé), tendances matin 14 j, alertes overtraining, TSB.
2. Décision `_fallback_decision()` : **reduce** si ≥ 2 manquées / adhérence < 50 % /
   readiness < 50 / sommeil < 6 h / TSB < −15 / alerte chronique ; **progress** si
   semaine conforme (facteur 1.05) ; **maintain** sinon.
3. **Fenêtre glissante** : le coach **re-compose la semaine à venir uniquement**
   (à partir du prochain lundi, ancrée sur l'état réel du jour) via le LLM du
   `plan_generator` (`compose_upcoming_week` → `_compose_one_week`), borné par
   `validate_and_correct()` (les faits — CTL/ATL/TSB, compliance, niveau — sont
   injectés dans le prompt, l'intensité est plafonnée en reprise). Le facteur
   volume déterministe borne la semaine (reduce), `progress`/`maintain` laissent
   le LLM libre dans les bornes. Le **reste du plan actif est conservé** et sera
   réévalué aux revues suivantes (le plan « roule » semaine après semaine). Puis
   `save_plan` en **nouvelle version** (`parent_plan_id` + `adapt_reason`,
   l'ancien passe en `superseded`). Fallback déterministe si Ollama injoignable
   (`use_llm=False` ou échec LLM).
4. Idempotence : flag `weekly_review_last_week` dans `sync_meta` (une revue par
   semaine ISO). Pushover « Plan adapté » si re-plan effectué.
   **Coût LLM** : la raison n'est rédigée que si un re-plan va effectivement
   être tenté — les sorties anticipées (« aucun plan actif », « objectif atteint
   ou dépassé ») ne paient aucun appel (schéma JSON contraint passé au SDK).

**Versionnage** : `training_plans` a désormais `status` (`active`/`superseded`),
`parent_plan_id`, `start_date`, `adapt_reason`. Le « plan actif » est résolu par
statut (`llm/plan_storage.load_active_plan`), fallback « plus récent couvrant la
date » pour la compat. `GET /api/plan/active`, `GET /api/plan/{id}/versions`,
`GET /api/plan/{id}/decisions`. `Workout` porte un `uid` stable (uuid court,
rétro-compatible via `from_dict`).

**Tests** : `test_daily_decision.py` (8), `test_weekly_review.py` (9) + extensions
`test_plan_generator.py` (prompt état réel, ceiling reprise) et `test_profile.py`
(champ `level`).
