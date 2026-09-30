# AGENTS.md — `domestique_ai/api/`

Couche HTTP (FastAPI), scheduler de tâches de fond, notifications, export
calendrier et authentification/identité.

Guide racine (invariants globaux, DB, conventions) : `AGENTS.md`.
Couches voisines : `ingestion/AGENTS.md`, `processing/AGENTS.md`, `llm/AGENTS.md`.

## Édition d'activité (toutes sources)

- **Endpoint** — `PATCH /api/activities/{external_id}` (modèle `ActivityUpdate`) : édition partielle des 4 champs athlète — `name` (nom affiché), `sport_type` (type), `notes` (commentaire libre, colonne `notes`) et `rpe` (effort ressenti 1-10, colonne `rpe`). `exclude_unset` : seuls les champs fournis changent ; `null` efface ; chaîne vide/blanche normalisée en `NULL` (`_clean_text`). Renvoie l'`ActivitySummary` à jour.
- **Ouvert à toutes les sources** (contrairement à la suppression) : l'édition ne touche que des champs jamais régénérés par l'ingestion — `save_garmin_activity()` ne réécrit jamais une ligne Garmin existante (skip si `garmin_id` déjà présent). Seul le backfill one-off `_BACKFILL_COLUMNS` re-fetch `name` (flag déjà posé en pratique).
- **DB** : colonnes `notes TEXT` / `rpe INTEGER` nullables (migration `_ensure_column` + `_ACTIVITY_COLUMNS`), écrites par `update_activity_fields()` (whitelist `_EDITABLE_ACTIVITY_COLUMNS`). `fetch_activities_from_db()` les expose ; `ActivitySummary`/`ActivityCreate` portent `notes`/`rpe`.
- **UI** — bouton crayon sur la page détail (`ActivityDetail.tsx`) : l'en-tête bascule en formulaire inline (nom, type via `components/sports.ts`, RPE 1-10, commentaire) + carte « Notes / ressenti » en lecture + pastille RPE. Masqué en vue coach (`viewing`, non-GET refusé par `get_athlete_context`).

## Fil de conversation du coach (fil unique)

L'UI coach affiche **un seul fil** continu, toutes sessions internes fusionnées.

- **`GET /api/coach/messages`** — page du fil ordonnée par `id` croissant, toutes
  sessions confondues : `limit` (défaut 30, cap 200), `before=<id>` (remonte le
  fil), `after=<id>` (redescend), `anchor=<id>` (fenêtre centrée, saut depuis la
  recherche). Réponse `CoachThreadPage { messages, has_more_before,
  has_more_after }` ; chaque message porte `id` (`conversations.id`), `role`,
  `content`, `thinking`, `tool_calls`.
- **`GET /api/coach/search?q=&limit=`** — recherche sémantique
  (`get_relevant_memory`, types message/summary/fact). Les hits `message` portent
  `message_id` (= `conversations.id`) pour se repositionner via `?anchor=`.
- **`POST /api/coach/chat`** : sans `session_id` (cas normal), le serveur résout
  la session interne courante via `current_or_new_session()` et **rotate** (ouvre
  un nouveau chunk) après `SESSION_IDLE_FINALIZE_MINUTES` d'inactivité — la
  session précédente est finalisée en tâche de fond (best-effort). Un
  `session_id` explicite reste honoré tel quel.
- Endpoints historiques conservés (tests/clients) : `GET /api/coach/sessions`,
  `GET /api/coach/sessions/{id}/messages`, `DELETE /api/coach/sessions/{id}`,
  `POST /api/coach/sessions/{id}/finalize`. La **génération de titre** n'est plus
  déclenchée (plus de sélecteur côté UI).
- Implémentation DB : `domestique_ai/llm/conversations.py` (`load_thread_page`,
  `current_or_new_session`). Tests : `tests/test_coach_thread.py`.

## Auto-sync Garmin (scheduler APScheduler)

Un `BackgroundScheduler` APScheduler tourne dans le process FastAPI et déclenche le sync Garmin à intervalle régulier — par défaut **toutes les 30 minutes**. Démarré au `lifespan` startup, arrêté proprement au shutdown.

- **Configuration** : `DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES` : période en minutes (défaut 30). `0` désactive complètement l'auto-sync.
- **Anti-chevauchement** : sync manuel (`POST /api/garmin/sync`) et auto-sync passent tous les deux par `_claim_sync()` dans `routers/garmin.py`. Tant qu'une sync est en cours (`status == "syncing"`), tout claim concurrent retourne `False` (skip silencieux loggé côté scheduler). `coalesce=True, max_instances=1` côté APScheduler en plus, ceinture + bretelles.
- **Logs et erreurs** : le job enveloppe `trigger_sync_blocking` dans un `try/except` global — un job APScheduler qui lève marque le job comme erroné et peut arrêter le scheduler, ce qu'on ne veut surtout pas. Toute exception inattendue est loggée mais n'interrompt pas la cadence.
- **Ciblage** : le cache token étant désormais **par athlète** (`garmin_token_dir_for(ctx)`), le job boucle sur **tous les athlètes ayant connecté leur compte** (`token_cache_present`) — plus seulement le bootstrap. Un athlète en échec n'interrompt pas les autres.

## Notifications push (Pushover) — palier 4 du coach proactif

`domestique_ai/notifications.py` expose deux fonctions best-effort :

- `send_pushover(title, message, priority=None)` : POST sur `api.pushover.net`. No-op silencieux si `PUSHOVER_USER_KEY` ou `PUSHOVER_APP_TOKEN` manque. Toute exception (réseau, 4xx) est loggée en warning et retournée comme `False`.
- `notify_sync_completed(inserted)` : appelée à la fin de `_run_sync` dans le router garmin si `inserted > 0`. No-op sur sync à vide (anti-spam). Pluriel/singulier géré.

Le hook dans `_run_sync` (router garmin) enveloppe l'appel dans un `try/except` : une notif qui échoue ne doit jamais altérer l'état du sync ni masquer le log de succès.

**Configuration** :
- `PUSHOVER_USER_KEY` + `PUSHOVER_APP_TOKEN` : obligatoires pour activer.
- `PUSHOVER_DEVICE` : optionnel, cible un device précis.
- `PUSHOVER_PRIORITY_DEFAULT` : optionnel, priorité par défaut (clampée -2..2).

**Extension future** : pour ajouter de nouveaux types de notifs (alerte overtraining qui change d'état, séance suggérée du matin), créer une fonction `notify_<event>()` dans le même module qui appelle `send_pushover` avec son propre formattage. Garder le principe : best-effort, jamais bloquant, et anti-spam via comparaison à un état précédent persisté si pertinent.

## Formulaire de retour testeurs (`POST /api/feedback`)

Retours laissés depuis la page `/feedback` (bouton d'en-tête, à côté de
l'avatar). Data **plateforme** (cross-tenant), non scopée par athlète.

- **Endpoint** — `POST /api/feedback` (authentifié, `get_current_user`) :
  `category ∈ {bug, idea, remark, other}`, `message` (1-4000), `page` /
  `app_version` optionnels. Rate-limité par utilisateur (10/h, `ratelimit`).
  Renvoie 201 `{id, created_at}`.
- **Persistance** — table `feedback` de `platform.db` : snapshot auteur
  (`user_id`, `public_id`, `role`, `author_email`) + `user_agent` capturé
  serveur. Helpers `insert_feedback` / `list_feedback` dans `platform_db.py`.
- **Notification email** — `mailer.send_feedback_notification(to, feedback)`
  vers `DOMESTIQUE_AI_FEEDBACK_EMAIL` (`config.get_feedback_notify_email()`),
  **best-effort** (try/except dans le handler, jamais bloquant). Aucune adresse
  configurée → pas d'envoi, le retour reste persisté. Envoi inline (pas de job
  scheduler).
- **Consultation & traitement** — la page admin
  (`GET /api/admin/feedback`, cf. « Rôles & administration ») liste les retours
  cross-tenant et permet de changer leur statut via
  `PATCH /api/admin/feedback/{id}` : `status ∈ {new, acknowledged, done,
  rejected}` (`platform_db.FEEDBACK_STATUSES`, validé en Python — la colonne
  `feedback.status` n'a pas de `CHECK`). Un export CSV (pattern `plan.py`) reste
  possible plus tard.
- Tests : `tests/test_feedback_api.py`, `tests/test_admin_api.py`.

## Rôles & administration

Trois rôles au niveau API : `coach`, `athlete` (créables en self-service par
inscription/invitation — `VALID_ROLES`) et `admin` (jamais auto-attribuable —
`ALL_ROLES`). Le rôle est porté par `users.role` (`platform_db.py`) ; le CHECK
SQLite autorise les trois.

- **Création / attribution d'`admin`** — uniquement hors-ligne (jamais via
  l'UI) : `auth_cli create-user --role admin --email …` crée un compte admin à
  part (helper `platform_db.create_account`, qui accepte `ALL_ROLES` là où
  `create_user` est borné à `VALID_ROLES`), et `auth_cli set-role admin --user
  <public_id>` promeut un compte existant (`set_user_role`). Ni
  `POST /api/auth/signup` ni les invitations n'acceptent `admin`. La migration
  `_migrate_users_role_check()` reconstruit la table `users` (SQLite ne sait pas
  ALTER un CHECK) au premier `init_platform_db` sur une base existante ;
  idempotente.
- **Panneau** — package `api/routers/admin/` (`__init__.py` assemble le routeur
  `prefix="/api/admin"`, `dependencies=[Depends(require_admin)]` ; sous-modules
  `users.py`, `feedback.py`, `settings.py`, helpers partagés `_common.py`).
  Endpoints comptes : `GET /users`, `GET /users/{public_id}` (fiche détaillée
  **sans secret** : verrouillage, activité, liens), `POST /users/{id}/role`,
  `POST /users/{id}/reset-2fa` (secret + codes purgés, ré-enrôlement forcé),
  `POST /users/{id}/unlock` (débloque un compte verrouillé), `POST
  /users/{id}/verify-email`, `POST /users/{id}/password-reset` (envoie le lien
  email — 400 sans email), `GET /users/{id}/sessions` (sans `token_hash`),
  `POST /users/{id}/logout` (révoque toutes les sessions), `DELETE /users/{id}`
  (compte + espace disque `remove_athlete_space` ; bootstrap refusé). Retours :
  `GET /feedback`, `PATCH /feedback/{id}`. Invitations : `GET /invitations`
  (toutes provenances, enrichies émetteur/acceptant), `POST /invitations`
  (crée une invitation `athlete`|`coach` émise par l'admin, renvoie le lien à
  usage unique `/accept-invite?token=…` ; action auditée `invitation_create`),
  `DELETE /invitations/{id}` (révoque une invitation `pending`). Une invitation
  émise par l'admin produit un **compte isolé** (émetteur non-coach → pas de lien
  `coach_athlete`), et son acceptation fonctionne même quand l'inscription
  publique est désactivée (`/accept-invite` est exempté du gate). Observabilité :
  `GET /stats` (comptes par rôle, invitations/feedback par statut, sessions
  actives, Garmin connectés, espaces athlètes + orphelins, taille de
  `platform.db`) et `GET /status` (version, `scheduler.jobs_snapshot()`,
  `garmin.sync_overview()`, dernier `healthcheck.last_ping()`,
  fuseau/horaires). Maintenance : `POST /athlete-spaces/purge-orphans` supprime
  les dossiers `data/athletes/<id>` sans compte correspondant
  (`orphan_athlete_space_ids` + `remove_athlete_space`, action auditée
  `purge_orphan_spaces`). Réglages : `GET|PUT /settings`.
  L'admin est **isolé** : il n'hérite pas des droits coach
  (`require_coach`/`get_athlete_context` inchangés), et son rôle (comme celui du
  bootstrap) ne peut pas être modifié via l'endpoint de rôle (403).
- **Journal d'audit** — table `admin_audit` de `platform.db` (acteur, action,
  cible, détails JSON, date). Chaque mutation admin est tracée
  (`platform_db.record_admin_audit`, appelé via `admin/_common.audit`) :
  `role_change`, `reset_2fa`, `unlock_account`, `verify_email`,
  `password_reset`, `logout_all`, `delete_account`, `feedback_status`,
  `settings_update`, `invitation_create`, `invitation_revoke`. FK `SET NULL` +
  snapshot `public_id` → l'historique survit à
  la suppression d'un compte (l'audit de suppression est écrit **avant** le
  DELETE). Jamais de secret dans les détails. Consultable via `GET /audit` :
  `limit` (défaut 50, cap 200), pagination par **curseur** `before_id`, filtres
  `action` (répétable) et `period` (`24h|7d|30d|all`, seuil calculé serveur),
  recherche `q` (acteur **ou** cible : nom affiché, email ou `public_id`) —
  plus récent d'abord. Chaque entrée porte `actor_label`/`target_label`
  (nom/email résolus ; `None` si le compte n'existe plus, le front retombe sur
  le snapshot `public_id`).
- **Réglages plateforme** — table `platform_settings` (key/value) de
  `platform.db`, éditée à chaud par l'admin via `GET|PUT /settings` :
  `signup_enabled` (surcharge `DOMESTIQUE_AI_SIGNUP_ENABLED`, résolu par
  `effective_signup_enabled()`), `maintenance_mode` (bool) et
  `broadcast_message` (≤ 500 car., `NULL` = pas de bandeau). `PUT` accepte un
  patch partiel (`exclude_unset`) et journalise les champs modifiés.
- **Annonce / bandeau** — `GET /api/announcement` (router
  `api/routers/announcement.py`, authentifié, lisible par **tout** compte) :
  `{maintenance_mode, message}` depuis `platform_db.get_announcement()`. Non
  bloquant : l'UI affiche un bandeau (`AnnouncementBanner`) plutôt qu'un gate.
- **Middleware** — aucune exception ajoutée : `/api/admin/*` exige un Bearer, et
  un admin à mot de passe reste soumis à l'enrôlement 2FA (le bootstrap, lui,
  reste exempté break-glass).
- **UI** — page `/admin` (`frontend/src/pages/Admin.tsx`), lien d'en-tête
  `ShieldCheck` visible seulement si `me.role === "admin"`. Chaque compte est
  dépliable (`components/AdminUserRow.tsx`) : fiche sécurité/activité/liens +
  actions (déverrouiller, vérifier email, reset mdp, déconnexion globale,
  suppression).
- Tests : `tests/test_admin_api.py`, `tests/test_platform_db.py` (rôle, réglages,
  audit, sessions, migration), `tests/test_auth_cli.py` (`create-user`,
  `set-role`).

## Heartbeat Healthchecks.io (dead man's switch)

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

Le job ping est indépendant du job sync — on peut activer l'un sans l'autre (ex. `DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES=0` + URL Healthchecks définie → seul le heartbeat tourne).

## Photo de profil (avatar)

Métadonnée d'identité du compte (pas du profil athlète YAML) : colonne
`avatar TEXT` sur `users` de `platform.db` (migration `_ensure_column`),
stockant une **data URL** `data:image/<type>;base64,…`, `NULL` par défaut.
Écrite par `set_user_avatar()` et exposée par `_user_dict` (donc
`get_current_user`).

- **Upload** : `PUT /api/auth/me/avatar` (multipart) — l'image est
  redimensionnée **côté navigateur** (`frontend/src/lib/image.ts`,
  `resizeImageToSquare` : recadrage carré centré 256 px → JPEG q0.85) avant
  envoi. Pas de Pillow côté serveur : le router valide la taille (≤ 500 Ko) et
  les **magic bytes** (JPEG/PNG/GIF/WebP), jamais le Content-Type client.
  `DELETE /api/auth/me/avatar` efface.
- **Exposition** : `MeResponse.avatar_url` (en-tête, `MeProvider`) et
  `AthleteSummary.avatar_url` (liste du roster coach, `GET /api/auth/athletes`).
  Affichage direct en `<img src>` (pas d'endpoint image ni montage statique —
  une balise `<img>` ne peut pas porter le Bearer).
- **UI** : section « Photo de profil » en tête de `/profil` (`Profil.tsx`,
  `AvatarSection`) ; l'en-tête (`App.tsx`) remplace l'icône `UserRound` par la
  miniature quand une photo existe ; `Roster.tsx` affiche l'avatar (ou les
  initiales) de chaque athlète.
- **Édition = compte courant** : les routes `/api/auth/*` ignorent le
  paramètre `?athlete=` (`withAthlete` les exclut) — un coach en consultation
  n'édite jamais la photo de l'athlète.

## Inscription publique, vérification d'email, lien coach & mot de passe oublié

Socle identité étendu (au-delà de l'entrée par invitation). Toute la logique DB
est dans `platform_db.py`, les endpoints dans `api/routers/auth.py`.

- **Inscription self-service** — `POST /api/auth/signup` (public, **exempté** du
  Bearer) : email + mot de passe + `role` (`athlete`|`coach`). Désactivée par
  défaut : `DOMESTIQUE_AI_SIGNUP_ENABLED` (403 sinon). Rate-limitée par IP
  (5/h). Provisionne l'espace athlète et émet une session. Le compte est créé
  **non vérifié** (`users.email_verified=0`) ; les comptes invités/legacy sont
  vérifiés d'office (`accept_invitation` pose `email_verified=1`).
  `GET /api/auth/config` (public) expose `{signup_enabled}` pour l'UI.
- **Lien d'invitation réutilisable du coach** — un coach s'inscrivant reçoit
  `invite_url=/accept-invite?coach=<code>`. Code opaque stocké **en clair**
  (`users.coach_invite_code`, précédent `feed_token`), **self-only** (jamais dans
  `_user_dict`), révocable : `GET /api/auth/coach-invite-link` /
  `POST /api/auth/coach-invite-link/rotate` (coach-only). Un athlète ouvre
  `?coach=<code>` : il crée son compte (`POST /api/auth/accept-invite` avec
  `coach_code`) **ou**, s'il a déjà un compte, se connecte puis
  `POST /api/auth/accept-invite/link` (authentifié, `role=athlete` requis) pour
  être **relié sans doublon** (`consume_invitation_for_link` /
  `link_coach_athlete`). Les invitations à usage unique (`?token=`) suivent le
  même endpoint.
- **Vérification d'email (souple, non bloquante)** — `POST /api/auth/verify-email`
  (public) consomme un token `email_verify` ; `POST /api/auth/resend-verification`
  (authentifié, autorisé pendant l'enrôlement 2FA) en renvoie un. Bandeau UI
  `EmailVerificationBanner` tant que `me.email_verified` est faux.
- **Mot de passe oublié** — `POST /api/auth/forgot-password` (public) : répond
  **toujours 200** (anti-énumération), envoie un lien si le compte existe.
  `POST /api/auth/reset-password` (public) : nouveau mot de passe, marque
  l'email vérifié, **révoque toutes les sessions** (`revoke_all_sessions`), ne
  touche pas au TOTP.
- **Tokens éphémères** — table `auth_tokens(user_id, purpose, token_hash,
  expires_at, consumed_at)`, `purpose ∈ {email_verify, password_reset}`,
  hashés HMAC comme les sessions, à usage unique, TTL via
  `get_email_verification_ttl_hours()` / `get_password_reset_ttl_minutes()`.
- **Envoi d'emails** — `domestique_ai/mailer.py` (SMTP stdlib, best-effort :
  no-op loggé si `SMTP_HOST` absent). Liens absolus via `get_app_base_url()`.
- **Rate-limiting** — `domestique_ai/ratelimit.py` (fenêtre glissante
  in-process) sur `signup` (IP), `forgot-password` (IP + email),
  `resend-verification` (user). 429 au dépassement. État par process (single
  worker uvicorn) ; `X-Forwarded-For` non géré (à faire derrière proxy).
- **Suppression de son compte** — `DELETE /api/auth/me` (authentifié,
  `DeleteAccountRequest`) : confirmation forte — mot de passe si le compte en a
  un + code TOTP/code de secours si la 2FA est active. Refuse le bootstrap
  (403). Efface la ligne plateforme (`delete_user` : sessions/invitations/tokens
  en cascade) **puis** le dossier de données via
  `athlete_context.remove_athlete_space(public_id)` (helper partagé avec la
  suppression par un coach, `roster.py`). `MeResponse.has_password` permet à l'UI
  de n'exiger le mot de passe que quand il existe.
- **Middleware** (`api/auth.py`) : `/api/auth/{config,signup,verify-email,
  forgot-password,reset-password}` sont dans `_EXEMPT_API_PATHS` ;
  `/api/auth/resend-verification` dans `_TOTP_SETUP_ALLOWED_PATHS` (compte frais
  pas encore conforme 2FA).

Tests : `tests/test_auth_api.py`, `tests/test_platform_db.py`,
`tests/test_ratelimit.py`, `tests/test_mailer.py`.

## Export iCalendar (`export/ics.py`)

`GET /api/plan/{plan_id}/export.ics` retourne le plan au format RFC 5545 importable dans Google Calendar, Apple Calendar et Outlook. Implémentation manuelle sans dépendance externe (~150 lignes : escaping, folding 75 octets, formats `DTSTART`/`DURATION`).

Points à retenir :
- **Floating local time** : les `DTSTART` n'ont ni `TZID` ni suffixe `Z` — le calendrier les interprète dans la timezone de l'utilisateur (« 18 h chez moi »).
- **Créneau par défaut 18 h** : configurable via le paramètre `default_hour` de `plan_to_ics()`. À terme on pourra le déduire des préférences `availability.yaml`.
- **UID stable** (`plan-<id>-<date>@domestique-ai`) : réimporter le fichier met à jour les événements existants au lieu de créer des doublons.
- **CRLF obligatoire** : Outlook refuse l'import si les lignes sont en LF seul (RFC 5545 § 3.1) — `plan_to_ics` produit toujours du CRLF.

23 tests dans `tests/test_ics_export.py` couvrent folding, escaping (`;`, `,`, `\n`), UID stable, CRLF, durations multi-formats.

## Flux d'abonnement iCalendar (webcal) — `GET /api/plan/feed.ics`

Le canal privilégié pour mettre les séances dans le calendrier de l'utilisateur
(Calendrier Apple/Google) : un **flux ICS à URL stable** que le client poll
directement (abonnement « webcal »). L'appareil interroge notre serveur — aucun
passage par le canal iCloud→APNs qui peut casser l'affichage côté Apple (bug
iOS 26.4, sync périmée, etc.). Le push CalDAV a été **retiré** au profit de ce
flux.

- **Contenu** : les séances des **2 semaines à venir** (fenêtre
  `rolling_weeks_window(today, weeks=2)` = semaine en cours + semaine suivante)
  du plan actif, décisions du check du matin appliquées via
  `select_upcoming_workouts`. Sérialisées par `plan_to_subscription_ics` (UID
  stable `domestique-ai-<date>@domestique-ai`, `DTEND` explicite, heures UTC via
  `get_scheduler_timezone`). La `DESCRIPTION` porte la structure par zones +
  TSS + notes.
- **Auth (token par athlète)** : le chemin est exempté du middleware Bearer
  (`auth.py _EXEMPT_API_PATHS`) car les clients calendrier ne peuvent pas envoyer
  de header Authorization. L'URL porte un **token propre à l'athlète** (colonne
  `users.feed_token` de `platform.db`, généré à la demande via
  `get_or_create_feed_token`, exposé par `GET /api/plan/subscription`). Le token
  identifie directement l'athlète : pas de clé globale exposée, pas de
  `?athlete=`. `POST /api/plan/subscription/rotate` régénère/révoque le token du
  **compte courant**.
- **Compat clé globale** : `GET /api/plan/feed.ics?key=<DOMESTIQUE_AI_CALENDAR_FEED_KEY>
  [&athlete=<public_id>]` reste supporté pour les abonnements existants. Le flux
  n'exige plus la clé globale : le mode token par athlète fonctionne sans elle.
- **UI** : la carte `frontend/src/components/CalendarSubscribe.tsx` (page Plan,
  sous les boutons ZIP/.ics, repliable — fermée par défaut) affiche l'URL
  copiable, les boutons Apple (`webcal://`) / Google Calendar et un QR code. Le
  flux est régénérable depuis l'UI ; masqué en consultation coach (rotation
  self-only).
- **URL d'abonnement** : `https://<hôte>/api/plan/feed.ics?key=<token>` — à
  ajouter comme « Calendrier d'abonnement » dans Calendrier Apple/Google. Le
  client poll la même URL : la fenêtre évolue après chaque revue hebdo sans
  doublons (UID stables).

Tests : `tests/test_ics_export.py` (multi-VEVENT, UID stables, DESCRIPTION,
exigence de clé, token par athlète, endpoint `subscription` + rotation, fenêtre
2 semaines, flux désactivé sans clé) et `tests/test_platform_db.py` (helpers
`get_or_create_feed_token` / `get_user_by_feed_token` / `rotate` / `clear`).
