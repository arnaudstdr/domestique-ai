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
  les `/assets/*` en `immutable`, l'app shell / HTML en `no-cache` et les
  `/api/*` en `no-store` (réponses privées).
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
message à la session interne courante (rotation invisible après inactivité). Un
**disclaimer LLM partagé** (`components/LlmDisclaimer.tsx` — « Le coach est une
IA : il peut faire des erreurs… ») est affiché en tête de la barre de saisie
fixe du coach, sous la description quand le mode « Coach IA » est sélectionné
sur `pages/Plan.tsx`, et en note sous le brief quotidien du Dashboard. La
recherche (`GET /api/coach/search?q=`) se fait dans un **bottom sheet**
(`components/CoachSearchSheet.tsx`) ouvert par le **bouton flottant** en bas à
droite au-dessus de la barre de saisie : champ en **live + debounce 300 ms**
(garde anti-réponse obsolète), fermeture par Escape / backdrop / X. Cliquer un
hit `message` ferme le sheet et recharge une fenêtre centrée (`?anchor=<id>`) en
surlignant la bulle ; un hit `fact`/`summary` (sans message) redirige vers
`/profil`. Le lien « Mémoire du coach » (`Brain`) vit désormais dans l'en-tête de
ce sheet (plus de carte de recherche en haut de page).

**Découvrabilité** : sur fil vide, des *chips* de questions suggérées (évolution,
FTP, zones bpm, type de sortie, séance du jour) envoient la question directement ;
les liens entrants `?prompt=...` préremplissent la saisie et donnent le focus au
textarea (mécanisme à utiliser depuis d'autres pages, ex. cartes du Dashboard).

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
  La carte **« Sources de données »** (remplace l'ancienne carte Google Health)
  affiche l'état Garmin et Google Health (`GET /api/morning/sources`), les
  actions « Sync santé » (Garmin) / « Sync maintenant » (Google), le lien
  Gérer → `/profil`, et le sélecteur de provider quand les deux sont connectés
  (`PUT /api/morning/sources/provider`). Le badge de provenance des cartes
  avancées/sommeil vient de `MorningEntry.source` (Garmin / Google Health /
  Manuel), et les colonnes bonus `garmin_*` (score sommeil, readiness, body
  battery) s'affichent en métriques secondaires quand présentes. Aucune action
  d'écriture n'est proposée en consultation coach (`useViewing`).
- **Tendances** — page `/tendances` (agrégats `GET /api/metrics/trends` +
  projection FTP).
- **Montées** — page `/montees` (`pages/Climbs.tsx`, lien depuis Tendances) :
  montées détectées (`GET /api/climbs`), renommage inline (`PUT
  /api/climbs/{id}` — self-only), stats meilleur/moyen temps, VAM, par année.
  État vide → rappel du CLI de backfill
  (`python -m domestique_ai.ingestion.backfill_streams --all`).
- **Feedback** — page `/feedback` (bouton `Megaphone` dans l'en-tête, à côté
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
- **Tuto d'onboarding** — `components/OnboardingTour.tsx` (driver.js ≥ 1.9,
  monté dans `AuthedShell` après `ConsentGate`) : carte flottante **non modale**
  en 3 étapes — profil (`#profil-infos-perso`) → connexion Garmin
  (`#profil-garmin`) → 1re synchro santé — la carte de cette dernière déclenche
  elle-même `api.garmin.healthSync(7)`. Pas d'ancrage ni de surbrillance :
  l'overlay driver.js est invisible ET `pointer-events: none`, et le composant
  retire la classe `driver-active` du `<body>` après `drive()` (sinon
  `.driver-active * { pointer-events: none }` neutralise tout le contenu et le
  listener `keydown` piège Tab) pour que la page reste utilisable ; le popover
  est replacé en bas au-dessus de la nav (z-index
  1190, sous les toasts). L'étape ne passe à la suivante qu'une fois l'action
  **constatée côté API** (`GET /api/profile`, `/api/garmin/status`,
  `/api/morning/sources`, revérifiés sur signal, focus, navigation et toutes les
  6 s) ; le bouton « Suivant » est désactivé tant que ce n'est pas fait.
  Écrans ciblés : athlètes et coachs (jamais admin/bootstrap ni en consultation
  coach), après 2FA + consentements. Seuls « terminé » / « passé » sont
  persistés via `POST /api/auth/me/onboarding` (cf. `domestique_ai/api/AGENTS.md`).
  Relance : bouton « Revoir le guide de démarrage » dans la section Compte de
  `/profil` ou événement `domestique:onboarding-start`. Les pages qui réalisent
  une action guidée émettent un signal via `lib/onboarding.ts`
  (`emitOnboardingSignal` : `profile-saved` dans `Profil.tsx`, `garmin-connected`
  dans `GarminSection`, `health-synced` dans `Morning.tsx`) — la marche à suivre
  pour ajouter une étape est celle du module, pas du polling ad hoc.
- **Login** — `pages/Login.tsx` : fond animé CSS-only (nappes `animate-aurora-*`
  + profil altimétrique `animate-route-draw`, keyframes dans `tailwind.config.js`,
  désactivés par `prefers-reduced-motion`), carte « verre » (`backdrop-blur`) et
  entrée `.stagger` ; logo affiché en `/favicon.svg` (SVG). Quand
  `api.auth.config().signup_enabled` est faux, un lien `mailto:` pré-rempli
  (sujet + corps, email déjà saisi inclus) vers `LEGAL_CONTACT_EMAIL`
  (`src/legal/index.ts`) s'affiche sous la carte pour demander une invitation.
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
  message diffusé), comptes, invitations (création d'un lien à usage unique
  `athlète`|`coach` à copier, toutes provenances, révocation des `pending` ;
  le lien est accepté même si `signup_enabled` est faux), retours testeurs
  cross-tenant (filtre par statut + changement de statut
  `new`/`acknowledged`/`done`/`rejected` ; onglets `components/FilterTab.tsx`,
  partagés avec le journal d'audit), et un encart **Plateforme** (stats + statut
  ops : scheduler, sync Garmin, healthcheck, version, fuseau ; compteur
  d'espaces athlètes orphelins + bouton de purge). Encart **Usage Ollama**
  (`components/AdminOllamaUsage.tsx`) : sélecteur de période (7/30/90 j), tuiles
  appels/tokens/latence/coût, bar chart par type d'appel (recharts +
  `chartTheme`), répartition par type d'appel et par athlète, derniers appels,
  et **deux barres de quota Cloud estimé** (fenêtres *session 5 h* et *hebdo
  7 j*, chacune avec % + compte à rebours de reset + projection, puis
  recommandation de forfait). Les tarifs, le quota hebdo et les JSON
  `llm_model_weights`/`llm_model_prices` s'éditent dans « Réglages plateforme »
  (`NumberSetting` / `JsonSetting`, commit au blur). Le **journal d'audit**
  (`components/AdminAuditLog.tsx`) est une timeline verticale groupée par jour
  (rail + nœud coloré/icône par famille d'action, « Aujourd'hui » / « Hier » /
  date, temps relatif avec horodatage absolu en tooltip, sous-titre humanisé
  par action à partir des `details` + acteur/cible résolus en nom/email,
  détails JSON repliables) : filtres par famille (Comptes/Sécurité/Invitations/
  Retours/Plateforme) et période (24 h/7 j/30 j/tout), recherche serveur acteur
  ou cible, pagination par curseur (« Afficher plus », `before_id`) et bouton
  refresh. Chaque compte est
  **dépliable**
  (`components/AdminUserRow.tsx`) : fiche (verrouillage, email vérifié, 2FA,
  Garmin, nb d'activités, sessions actives, liens coach↔athlète) + actions
  (changer le rôle, réinitialiser la 2FA, déverrouiller, vérifier l'email,
  envoyer un reset mot de passe, déconnecter partout, supprimer — ce dernier
  confirmé par saisie de l'email). L'admin n'a **aucun** autre écran (pas de
  roster/impersonation). `withAthlete` exclut `/api/admin/*`. Le réglage
  maintenance/message alimente `components/AnnouncementBanner.tsx` (monté dans
  `AuthedShell`, bandeau non bloquant pour tous).

## Pages légales, consentements & SEO

- **Pages publiques** — `/mentions-legales`, `/cgu`, `/confidentialite` déclarées
  en top-level dans `App.tsx` (avant le catch-all `/*` : sinon un visiteur
  anonyme est redirigé vers `/login`). Contenu Markdown dans `src/legal/*.md`
  importé en `?raw`, rendu par `components/LegalLayout.tsx` (react-markdown +
  remark-gfm, styles `.legal-prose` dans `index.css`). La version affichée
  (`LEGAL_VERSION` de `src/legal/index.ts`) doit rester alignée avec
  `domestique_ai/legal.py`.
- **Liens** — `components/LegalLinks.tsx` sous les cartes des pages publiques
  (login, signup, accept-invite, forgot/reset, verify-email, reconnect) et dans
  la section profil.
- **Consentements** — `components/ConsentCheckboxes.tsx` (cases décochées,
  liens ouverts en nouvel onglet) sur `Signup.tsx` et `AcceptInvite.tsx` (mode
  création). `components/ConsentGate.tsx`, monté dans `AuthedShell`, affiche un
  portail bloquant tant que `me.terms_accepted_at` ou `me.health_consent_at`
  est nul (comptes antérieurs à la conformité). Retrait/ré-consentement santé
  et export RGPD dans `components/ProfileLegalSection.tsx` (section « Données
  personnelles & consentements » de `/profil`).
- **SEO / tête de page** — `hooks/usePageMeta.ts` (titre, description, meta
  robots ; restaurés à la navigation). `robots.txt` et `sitemap.xml` dans
  `public/`. `pages/NotFound.tsx` rendue par le catch-all de la coquille
  authentifiée ; côté FastAPI, `SPAStaticFiles` renvoie un vrai 404 pour les
  fichiers manquants (pas le shell HTML).
- **Polices auto-hébergées** — woff2 dans `src/assets/fonts/` (latin +
  latin-ext, OFL) référencés par `@font-face` en tête de `index.css` : aucune
  requête navigateur vers Google Fonts. Vite les émet dans
  `/assets/*-<hash>.woff2` (cache immutable) et `vite.config.ts` les précache
  (`globPatterns` inclut `woff2`).

## Avatar — redimensionnement client

L'image est redimensionnée **côté navigateur** avant envoi
(`frontend/src/lib/image.ts`, `resizeImageToSquare` : recadrage carré centré
256 px → JPEG q0.85) vers `PUT /api/auth/me/avatar`. Pas de traitement image
côté serveur (validation taille + magic bytes uniquement).
