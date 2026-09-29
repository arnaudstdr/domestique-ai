# AGENTS.md — `frontend/`

PWA React (Vite + TypeScript + Tailwind + recharts + react-leaflet) servie par
FastAPI via `StaticFiles` en prod.

Guide racine (invariants globaux, conventions) : `AGENTS.md`.
Endpoints consommés : `domestique_ai/api/AGENTS.md`. Données/coach :
`processing/AGENTS.md`, `llm/AGENTS.md`.

## Stack & dev

- React 18 + Vite + TypeScript + Tailwind + recharts + react-leaflet.
- Service worker manuel dans `public/sw.js` (NetworkFirst sur `/api/`).
- Dev : `cd frontend && npm run dev` → Vite écoute sur **5173** et proxy `/api`
  vers `http://localhost:8501`. Build : `npm run build` (FastAPI sert ensuite le
  bundle sur le port 8501).

## Streaming SSE du coach

Le coach LLM streame via **SSE** (`/api/coach/chat`) — `run_turn_stream()` yield
les events `thinking` / `tool_call` / `tool_result` / `token` au fur et à mesure,
consommés par `sse-starlette` côté serveur et par `consumeSseStream()` côté
client. Les deltas de raisonnement sont affichés dans l'expander
« 🧠 Raisonnement » de la page Coach (debug).

## Écrans & composants notables

- **Dashboard** — `DailyBriefCard` en hero. Refonte visuelle : **anneau TSB**
  (`TsbGauge`, SVG animé, couleur par zone), **halo d'ambiance** teinté par
  l'état, **avatar coach** (`CoachAvatar`, halo pulsant), barre séance
  (durée + TSS estimé), **mini-barres sommeil** (`SleepBars`, repère baseline),
  ligne `coach_tip`, et **surface d'alerte unique** (primaire visible +
  secondaires dépliables — la carte « Signaux d'alerte » séparée a été
  supprimée). Animations gated `prefers-reduced-motion`.
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
- **Mémoire du coach** — `components/MemoryPanel.tsx` (section « Mémoire du
  coach » dans `/profil`, lien depuis la page Coach).
- **Abonnement calendrier** — `components/CalendarSubscribe.tsx` (page Plan sous
  les boutons ZIP/.ics, et page Réglages) : URL copiable, boutons Apple
  (`webcal://`) / Google Calendar et QR code. Masqué en consultation coach.
- **Profil / avatar** — section « Photo de profil » en tête de `/profil`
  (`Profil.tsx`, `AvatarSection`) ; l'en-tête (`App.tsx`) remplace l'icône
  `UserRound` par la miniature ; `Roster.tsx` affiche l'avatar (ou les initiales)
  de chaque athlète.

## Avatar — redimensionnement client

L'image est redimensionnée **côté navigateur** avant envoi
(`frontend/src/lib/image.ts`, `resizeImageToSquare` : recadrage carré centré
256 px → JPEG q0.85) vers `PUT /api/auth/me/avatar`. Pas de traitement image
côté serveur (validation taille + magic bytes uniquement).
