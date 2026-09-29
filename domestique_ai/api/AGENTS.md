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
  sous les boutons ZIP/.ics, et page Réglages) affiche l'URL copiable, les
  boutons Apple (`webcal://`) / Google Calendar et un QR code. Le flux est
  régénérable depuis l'UI ; masqué en consultation coach (rotation self-only).
- **URL d'abonnement** : `https://<hôte>/api/plan/feed.ics?key=<token>` — à
  ajouter comme « Calendrier d'abonnement » dans Calendrier Apple/Google. Le
  client poll la même URL : la fenêtre évolue après chaque revue hebdo sans
  doublons (UID stables).

Tests : `tests/test_ics_export.py` (multi-VEVENT, UID stables, DESCRIPTION,
exigence de clé, token par athlète, endpoint `subscription` + rotation, fenêtre
2 semaines, flux désactivé sans clé) et `tests/test_platform_db.py` (helpers
`get_or_create_feed_token` / `get_user_by_feed_token` / `rotate` / `clear`).
