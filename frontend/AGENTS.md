# AGENTS.md — `frontend/`

PWA React (Vite + TypeScript + Tailwind + recharts + react-leaflet) servie par
FastAPI via `StaticFiles` en prod.

Guide racine (invariants globaux, conventions) : `AGENTS.md`.
Endpoints consommés : `domestique_ai/api/AGENTS.md`. Données/coach :
`processing/AGENTS.md`, `llm/AGENTS.md`.

## Stack & dev

- React 18 + Vite + TypeScript + Tailwind + recharts + react-leaflet.
- **Service worker généré par `vite-plugin-pwa`** (`registerType: "autoUpdate"`,
  stratégie Workbox `generateSW`) : precache révisionné automatiquement à
  chaque build, `navigateFallback` sur `/index.html`, `cleanupOutdatedCaches`.
  Plus de `public/sw.js` à maintenir ni de cache à bumper à la main. Le SW n'est
  **pas** actif en dev (`devOptions.enabled: false`) ; il se teste via
  `npm run build` puis FastAPI sur 8501.
- Mise à jour transparente : à l'activation d'une nouvelle version, le SW prend
  la main et le client recharge la page. `src/main.tsx` diffère le reload tant
  que l'onglet est visible (évite de couper une saisie) et l'applique au
  prochain passage en arrière-plan.
- **Cache runtime** : whitelist stricte dans `vite.config.ts` (`/api/metrics`,
  `/api/activities` en `NetworkFirst`, 3 s). Tout le reste — `/api/coach/*`,
  `/api/morning`, `/api/objective`, `/api/profile`, `/api/availability`, auth —
  n'est intercepté par aucune route et part directement sur le réseau (jamais
  mis en cache).
- **Headers HTTP** : `domestique_ai/api/main.py` (`CacheControlMiddleware`) sert
  les `/assets/*` en `immutable` et l'app shell / HTML en `no-cache`.
- Dev : `cd frontend && npm run dev` → Vite écoute sur **5173** et proxy `/api`
  vers `http://localhost:8501`. Build : `npm run build` (FastAPI sert ensuite le
  bundle sur le port 8501).

## Thème clair / sombre / auto

L'UI supporte trois modes : **clair**, **sombre** et **auto** (suit
`prefers-color-scheme`). Le thème est piloté par la classe `dark` sur `<html>`
(Tailwind `darkMode: "class"`), posée par :

- un **script inline anti-flash** dans `index.html` (avant le premier paint) ;
- `hooks/useTheme.tsx` (`ThemeProvider` monté dans `main.tsx`, hook `useTheme`)
  qui persiste le choix dans `localStorage` (`domestique-theme`) et suit l'OS en
  mode auto. Le sélecteur (Clair / Sombre / Auto) est la section « Apparence »
  de `pages/Profil.tsx`.

Les couleurs sont des **tokens sémantiques** définis dans `src/index.css`
(`:root` = thème clair, `.dark` = override sombre) : `fg` / `fg-soft` (texte),
`border` / `overlay` / `sunken` (liserés et surfaces translucides, à utiliser
avec un modificateur d'alpha), `accent-ink` (texte posé sur un fond `accent`),
plus `surface` / `card` / `muted` / `accent` / `ctl` / `atl` / `tsb` / `tsb_neg`.
**Ne pas remettre de `text-gray-*`, `border-white/*` ou `bg-white/*`** dans les
composants : passer par ces tokens. Les charts recharts lisent les tokens à la
volée via `chartTheme.ts` (`themeColor`, accesseurs) — les couleurs restent
centralisées dans ce module.

⚠️ Le nom et l'empreinte du precache sont gérés par le build : ne pas
réintroduire de `public/sw.js` ni de version de cache à bumper manuellement.
Nouvelle whitelist d'API à cacher → l'ajouter dans `workbox.runtimeCaching`
de `vite.config.ts`.

## Streaming SSE du coach

Le coach LLM streame via **SSE** (`/api/coach/chat`) — `run_turn_stream()` yield
les events `thinking` / `tool_call` / `tool_result` / `token` au fur et à mesure,
consommés par `sse-starlette` côté serveur et par `consumeSseStream()` côté
client. Les deltas de raisonnement sont affichés dans l'expander
« 🧠 Raisonnement » de la page Coach (debug).

**Fil unique** : la page `pages/Coach.tsx` n'a plus de sélecteur de sessions —
elle affiche un fil continu, toutes sessions fusionnées. Au montage elle charge
les **30 derniers messages** (`GET /api/coach/messages`, `PAGE_SIZE = 30`), puis
remonte par **scroll infini** (`IntersectionObserver` en haut → `?before=<id>`,
position de scroll préservée ; sentinelle basse `?after=<id>` pour le cas
« saut recherche »). L'envoi poste `session_id: null` : le serveur rattache le
message à la session interne courante (rotation invisible après inactivité). La
recherche (`GET /api/coach/search?q=`) se fait dans un **bottom sheet**
(`components/CoachSearchSheet.tsx`) ouvert par le **bouton flottant** en bas à
droite au-dessus de la barre de saisie : champ en **live + debounce 300 ms**
(garde anti-réponse obsolète), fermeture par Escape / backdrop / X. Cliquer un
hit `message` ferme le sheet et recharge une fenêtre centrée (`?anchor=<id>`) en
surlignant la bulle ; un hit `fact`/`summary` (sans message) redirige vers
`/profil`. Le lien « Mémoire du coach » (`Brain`) vit désormais dans l'en-tête de
ce sheet (plus de carte de recherche en haut de page).

## Écrans & composants notables

- **Dashboard** — `DailyBriefCard` en hero. Refonte visuelle : **anneau TSB**
  (`TsbGauge`, SVG animé, couleur par zone), **halo d'ambiance** teinté par
  l'état, **avatar coach** (`CoachAvatar`, halo pulsant), barre séance
  (durée + TSS estimé), **mini-barres sommeil** (`SleepBars`, repère baseline),
  ligne `coach_tip`, et **surface d'alerte unique** (primaire visible +
  secondaires dépliables — la carte « Signaux d'alerte » séparée a été
  supprimée). Animations gated `prefers-reduced-motion`.
  - **Enrichissements « premier coup d'œil »** : la barre semaine du hero
    affiche l'**adhérence** (`week_adherence_pct`) + les statuts
    fait/partiel/manqué/repos coach (`WeekStatuses`) ; la surface d'alerte, quand
    elle est active, affiche les **indicateurs overtraining chiffrés**
    (`indicators` de `GET /api/metrics/overtraining` : TSB chronique, monotonie,
    strain, saut de volume) — masqués en journée normale.
  - **`RecoveryCard`** — rangée 2 colonnes (`sm:grid-cols-2`) avec
    `ObjectiveCard`, empilée sur mobile. Récupération : readiness + bande
    (`ReadinessBadge`, composant partagé extrait de `pages/Morning.tsx`), HRV et
    FC repos avec delta % vs baseline 14 j, score/durée de sommeil. Source
    `GET /api/morning?days=14` (⚠️ endpoint **hors cache Workbox** → skeleton).
  - **`ObjectiveCard`** — objectif courant (`GET /api/objective`) + projection
    FTP (`GET /api/metrics/ftp-projection`) : type, **J-x**, FTP cible vs
    projetée (barre de progression), W/kg. État vide → lien vers `/plan`.
  - Les deux cartes utilisent `StatStrip` (`columns="3-responsive"` / `2`) pour
    rester lisibles du mobile au desktop.
  - **« Dernière sortie »** — juste après le brief (boucle *prévu → réalisé*) :
    `ActivityCard` réutilisée telle quelle pour la dernière activité
    (`GET /api/activities?page=1&page_size=20`, tri date desc ; endpoint caché
    Workbox). Titrée « Sortie du jour » si la plus récente date d'aujourd'hui,
    sinon « Dernière sortie » ; badge « N séances aujourd'hui » en cas de double
    séance. La carte est masquée s'il n'y a aucune activité.
- **Identité partagée** — le nom affiché dans la salutation vient du contexte
  `MeProvider` (`hooks/useMe.tsx`) : un seul appel `/me` partagé, plus de fetch
  par page.
- **Activités** — en-tête de page avec les cartes inline `ActivityCreateForm.tsx`
  / `TcxImportForm.tsx` (saisie manuelle / import TCX). Page détail
  `ActivityDetail.tsx` : bouton crayon → formulaire inline (nom, type via
  `components/sports.ts`, RPE 1-10, commentaire) + carte « Notes / ressenti » et
  pastille RPE ; carte « Poids/puissance » (puissance moy. / poids actuel) ;
  `RoutePreview` affiche le tracé encodé (`map_polyline`).
- **Santé** — page `/sante`, composant `pages/Morning.tsx` (onglet `HeartPulse`
  dans `BottomNav`). Le **domaine backend reste nommé `morning`** (voir
  `ingestion/AGENTS.md`) : ne pas « aligner » le front sur le nom UI.
- **Tendances** — page `/tendances` (agrégats `GET /api/metrics/trends` +
  projection FTP).
- **Feedback** — page `/feedback` (bouton `MessageSquare` dans l'en-tête, à côté
  de l'avatar ; masqué en consultation coach) → `POST /api/feedback`. Select
  catégorie + textarea ; `page` = pathname courant, `app_version` =
  `__APP_VERSION__` (injectée par `define` dans `vite.config.ts`). Endpoint hors
  cache Workbox.
- **Mémoire du coach** — `components/MemoryPanel.tsx` (section « Mémoire du
  coach » dans `/profil`, lien depuis la page Coach).
- **Abonnement calendrier** — `components/CalendarSubscribe.tsx` (page Plan sous
  les boutons ZIP/.ics, repliable — fermée par défaut) : URL copiable, boutons
  Apple (`webcal://`) / Google Calendar et QR code. Masqué en consultation coach.
- **Profil / avatar** — section « Photo de profil » en tête de `/profil`
  (`Profil.tsx`, `AvatarSection`) ; l'en-tête (`App.tsx`) remplace l'icône
  `UserRound` par la miniature ; `Roster.tsx` affiche l'avatar (ou les initiales)
  de chaque athlète.
- **Inscription / mot de passe** — pages publiques `Signup.tsx` (`/signup`,
  masquée dans Login tant que `api.auth.config().signup_enabled` est faux),
  `VerifyEmail.tsx` (`/verify-email?token=`), `ForgotPassword.tsx`
  (`/forgot-password`), `ResetPassword.tsx` (`/reset-password?token=`).
  `AcceptInvite.tsx` gère `?token=` **et** `?coach=` avec deux parcours : « créer
  un compte » ou « j'ai déjà un compte » (login + TOTP puis
  `acceptInviteLink`, sans doublon). Bandeau `EmailVerificationBanner.tsx` monté
  dans `AuthedShell` tant que `me.email_verified` est faux. `Roster.tsx` affiche
  aussi `ReusableInviteSection` (lien coach réutilisable, copie + régénération).
- **Suppression de compte** — `DangerZoneSection` en bas de `/profil` : rappel
  irréversible, confirmation par saisie de `SUPPRIMER`, mot de passe (si
  `me.has_password`) + code TOTP (si `me.totp_enabled`), puis
  `api.auth.deleteAccount` → `clearApiToken` → `/login`.
- **Admin** — page `/admin` (`pages/Admin.tsx`), lien d'en-tête `ShieldCheck`
  affiché uniquement si `me.role === "admin"` (`AuthedShell`). Sections :
  réglages plateforme (toggle `signup_enabled`, toggle `maintenance_mode`,
  message diffusé), comptes, invitations (toutes
  provenances, révocation des `pending`), retours testeurs
  cross-tenant (filtre par statut + changement de statut
  `new`/`acknowledged`/`done`/`rejected`), et journal d'audit (dernières actions
  admin, libellés FR), et un encart **Plateforme** (stats + statut ops :
  scheduler, sync Garmin, healthcheck, version, fuseau). Chaque compte est
  **dépliable**
  (`components/AdminUserRow.tsx`) : fiche (verrouillage, email vérifié, 2FA,
  Garmin, nb d'activités, sessions actives, liens coach↔athlète) + actions
  (changer le rôle, réinitialiser la 2FA, déverrouiller, vérifier l'email,
  envoyer un reset mot de passe, déconnecter partout, supprimer — ce dernier
  confirmé par saisie de l'email). L'admin n'a **aucun** autre écran (pas de
  roster/impersonation). `withAthlete` exclut `/api/admin/*`. Le réglage
  maintenance/message alimente `components/AnnouncementBanner.tsx` (monté dans
  `AuthedShell`, bandeau non bloquant pour tous).

## Avatar — redimensionnement client

L'image est redimensionnée **côté navigateur** avant envoi
(`frontend/src/lib/image.ts`, `resizeImageToSquare` : recadrage carré centré
256 px → JPEG q0.85) vers `PUT /api/auth/me/avatar`. Pas de traitement image
côté serveur (validation taille + magic bytes uniquement).
