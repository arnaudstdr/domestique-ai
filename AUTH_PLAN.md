# AUTH_PLAN.md — Auth email/mot de passe + 2FA (TOTP)

> Suivi d'avancement. Cocher au fur et à mesure. Réf. : `COACH_APP_DESIGN.md` §5.

## Objectif

Remplacer le parcours « token API collé / liens magiques » par une vraie
authentification **email + mot de passe + TOTP**, pour le coach **et** les
athlètes, sans perdre les données du Raspberry Pi.

## Décisions actées

- 2FA : **TOTP** (apps Authenticator) **+ recovery codes**. Pas de passkeys (V1).
- **2FA obligatoire** pour tous les comptes.
- Session : on **garde** le token opaque hashé + Bearer en `localStorage`.
- Identifiant : **email** (colonne unique).
- Invitation conservée comme **onboarding** (cercle fermé) ; le lien magique ne
  sert plus à se reconnecter.
- Le token legacy `DOMESTIQUE_AI_API_TOKEN` reste **break-glass** (bootstrap).

## Architecture actuelle (rappel)

- `data/platform.db` (`domestique_ai/platform_db.py`) : `users`, `sessions`,
  `invitations`, `reconnect_tokens`, `coach_athlete`. **Pas de password/email.**
- `BearerAuthMiddleware` (`api/auth.py`) : session token ou token legacy →
  bootstrap coach.
- Données : bootstrap → `data/strava_activities.db` (legacy) ; autres →
  `data/athletes/<public_id>/` (`athlete_context.py`).
- Le login n'émet qu'une **session** : rien à changer dans le moteur ni le
  scoping `?athlete=`.

## Lots d'implémentation

### Lot 1 — Schéma + migration (backend)
- [x] `_ensure_column()` dans `platform_db.py`
- [x] Colonnes `users` : `email` (unique partiel), `password_hash`,
      `totp_secret`, `totp_enabled`, `failed_attempts`, `locked_until`,
      `password_changed_at`
- [x] Table `recovery_codes` (codes hashés)
- [x] `create_session` : TTL (`DOMESTIQUE_AI_SESSION_TTL_DAYS`, défaut 30 j)
- [x] Helpers : `set_user_credentials`, `get_user_by_email`, `set_totp_secret`,
      `enable_totp`, `disable_totp`, recovery codes
      (`replace_recovery_codes` / `list_recovery_codes` /
      `mark_recovery_code_used` — la vérification argon2 vit dans `security.py`),
      lockout (`record_failed_login` / `clear_failed_login` / `user_is_locked`)
- [x] Tests `test_platform_db.py` (migration additive, email unique, TOTP,
      recovery codes, lockout, TTL) — 9 tests ajoutés, suite verte

### Lot 2 — Crypto + TOTP (`domestique_ai/security.py`)
- [x] `argon2-cffi` (+ `pyotp`, `qrcode`) dans `pyproject.toml`
- [x] `hash_password` / `verify_password` (+ `assert_password_strength`)
- [x] TOTP : `new_totp_secret`, `totp_uri`, `totp_qr_svg_data_uri`, `verify_totp`
- [x] Recovery codes : `generate/hash/verify_recovery_code`
- [x] Challenge 2FA stateless HMAC-signé (TTL 5 min) —
      `create_login_challenge` / `verify_login_challenge`
- [x] Tests `test_security.py` (9 tests) — suite verte (655)

### Lot 3 — Endpoints (`api/routers/auth.py`)
- [x] `POST /api/auth/login` → `{status:"totp_required", challenge}` ou session
- [x] `POST /api/auth/login/totp` → code TOTP **ou** recovery code → session
- [x] `POST /api/auth/totp/enroll` (secret + QR) / `POST /api/auth/totp/verify`
- [x] `POST /api/auth/totp/disable` (mot de passe requis)
- [x] `POST /api/auth/password`, `POST /api/auth/recovery-codes`
- [x] `POST /api/auth/setup-credentials` (compte sans identifiants — legacy/bootstrap)
- [x] `accept-invite` → `{invite_token, display_name, email, password}`
      (transaction unique : email dupliqué → 409, invitation non consommée)
- [x] `get_user_credentials` / `set_password` / `has_password` dans `platform_db`

### Lot 4 — Middleware (`api/auth.py`)
- [x] `_EXEMPT_API_PATHS` += `/api/auth/login`, `/api/auth/login/totp`
- [x] Enforcement 2FA : `has_password` + `totp_enabled=0` + non-bootstrap → 403
      `totp_setup_required`, sauf allowlist (`/me`, `/logout`,
      `/setup-credentials`, `/totp/enroll`, `/totp/verify`)
- [x] Break-glass token legacy préservé (bootstrap exonéré d'enforcement)
- [x] Grandfathering : un compte sans mot de passe (session historique) n'est
      jamais bloqué → pas de lockout collectif au déploiement
- [x] Tests `test_auth_password.py` (13 tests : login, lockout, enrôlement,
      recovery, changement mdp, désactivation, bootstrap) — suite verte (668)

### Lot 5 — Anti-bruteforce
- [ ] Backoff + lockout 15 min (~5 échecs), reset au succès

### Lot 6 — Frontend
- [ ] `Login.tsx` : 2 étapes (email/mdp → OTP / code de secours)
- [ ] `AcceptInvite.tsx` : assistant email/mdp → QR → codes de secours
- [ ] `Profil.tsx` : gérer 2FA, changer mdp, régénérer les codes
- [ ] `client.ts` / `types.ts` : méthodes + redirection `totp_setup_required`

### Lot 7 — Tests
- [ ] `test_platform_db.py` : migration additive, hash, email unique, lockout
- [ ] `test_auth_password.py` : login, challenge, recovery, enrôlement imposé
- [ ] `test_auth_api.py` : exempations, enforcement, break-glass
- [ ] Test migration : ancien schéma → colonnes présentes, données intactes

### Lot 8 — Déploiement RPi + docs
- [ ] CLI `python -m domestique_ai.auth_cli` (`set-credentials`, `enroll-totp`)
- [ ] `DEPLOY.md` : backup, migration, bootstrap coach
- [ ] `.env.example` : TTL session, `DOMESTIQUE_AI_SESSION_SECRET`

## Déploiement Raspberry Pi (zéro perte)

1. `tar czf data-backup-$(date +%F).tgz data/`
2. Déployer : migration additive/idempotente au boot (`init_platform_db()`).
   Ne touche ni `strava_activities.db` ni `data/athletes/`.
3. Bootstrap coach : `docker exec … python -m domestique_ai.auth_cli
   set-credentials --email …` puis `enroll-totp`.
4. Sessions existantes **grandfathered** (pas de lockout) ; enrôlement forcé aux
   nouveaux logins.
5. Rollback : restaurer `platform.db` (ancien code tolère les colonnes en plus).

## Risques & garde-fous

- **Lockout du propriétaire** → CLI locale + token legacy break-glass.
- **Déconnexion massive** → grandfathering des sessions.
- **Contournement 2FA** → token legacy réservé au bootstrap uniquement.
- **Énumération de comptes** → 401 générique sur `/login`.

## Ordre d'exécution

`1 → 2 → 3 → 4 → 5` (backend + tests verts) → `6` (front) → `7` (tests) →
`8` (déploiement). Estimation : **~5-7 j**.
