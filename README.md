# DomestiqueAI 🚴‍♂️🤖

![Python](https://img.shields.io/badge/python-3.12-slim.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Dernier commit](https://img.shields.io/github/last-commit/arnaudstdr/domestique-ai)
![Stars](https://img.shields.io/github/stars/arnaudstdr/domestique-ai?style=social)

L'assistant intelligent pour cyclistes. Analyse automatique de la charge
d'entraînement, état de forme, vue détaillée de chaque sortie (carte GPS,
courbes FC / altitude / puissance, export GPX) et **coach conversationnel
LLM** qui analyse vos sorties à la demande sans inventer un seul chiffre.

## Architecture

```text
domestique_ai/
├── config.py                # chemins de données, FTP, profil HR, secrets via .env
├── ingestion/
│   ├── strava.py            # client OAuth2 + persistance SQLite (refresh, streams)
│   └── strava_oauth_flow.py # flow d'auth interactif initial
├── processing/
│   ├── analyzer.py          # TSS / hr-TSS, CTL/ATL/TSB, zones HR (Karvonen)
│   └── gpx.py               # export GPX 1.1 depuis les streams Strava
├── llm/
│   ├── ollama_client.py     # wrapper SDK Ollama (chat + tool calling)
│   ├── tools.py             # 6 tools exposés au LLM (état charge, activités, etc.)
│   ├── coach.py             # boucle agentique du coach (system prompt + tool loop)
│   ├── objectives.py        # gestion de l'objectif YAML (data/objective.yaml)
│   └── conversations.py     # persistance des sessions de chat (SQLite)
└── app/
    └── dashboard.py         # UI Streamlit (2 onglets : Tableau de bord + Coach)
```

Source de vérité : SQLite local (`data/strava_activities.db`). Une seule table
`activities` avec `strava_id` UNIQUE — ingestion idempotente.

## Calcul de la charge d'entraînement

Deux modes au choix, automatiquement sélectionnés selon les données disponibles :

- **TSS puissance** — via `STRAVA_FTP` et la puissance moyenne de l'activité.
- **hr-TSS (TRIMP normalisé)** — via la HR moyenne, `STRAVA_HR_REST` et
  `STRAVA_HR_MAX`. TRIMP exponentiel de Banister, normalisé pour qu'1 h passé à
  `STRAVA_LTHR_PCT` (88 % de la HRR par défaut) vaille 100 points : même
  échelle que le TSS power.

**Priorité** : si HR + HRrepos + HRmax sont configurés, hr-TSS prend le pas
sur la puissance — pratique sans FTP fiable. Bouton « 🔁 Recalculer la
charge » dans le dashboard pour rejouer le score sur tout l'historique après
changement de profil.

### Zones HR (%HRR Karvonen)

Chaque activité avec stream HR est ventilée en 5 zones :
Z1 (<60 %) · Z2 (60-70 %) · Z3 (70-80 %) · Z4 (80-90 %) · Z5 (≥90 %).
Bouton « 📥 Backfill zones HR » dans la barre latérale pour rattraper
l'historique (1 appel API Strava par activité, idempotent).

## Vue détail d'une activité

Cliquer sur une ligne du tableau d'activités ouvre une vue détaillée façon
Strava :

- **Carte GPS** de la trace (pydeck PathLayer, fallback `st.map`).
- **Courbes** FC, altitude, puissance dans le temps.
- **Export GPX** (1.1 + extensions Garmin TrackPoint pour HR/cadence/puissance)
  importable dans Garmin Connect, Komoot, Zwift, RideWithGPS, etc.
- **🤖 Analyser cette sortie** : bouton qui interroge le coach LLM. Le modèle
  appelle `get_activity_details`, `get_training_load_state` et `get_objective`
  pour évaluer si la séance a été *productive* ou *contre-productive* vu le
  TSB courant et l'objectif, puis recommande la suite.

Les streams sont fetchés à la demande (cache Streamlit 1 h, pas de bloat DB).

## Coach LLM (onglet Coach)

Coach conversationnel basé sur **Ollama** (modèle par défaut
`gemma4:31b-cloud`, override via `OLLAMA_MODEL`). Tool calling avec 6 tools :

| Tool | Usage |
|---|---|
| `get_training_load_state` | CTL/ATL/TSB du jour + zone interprétative |
| `get_recent_activities` | Activités sur N derniers jours |
| `get_zone_distribution` | Répartition cumulée par zone HR |
| `get_objective` | Objectif d'entraînement courant (YAML) |
| `get_activity_details` | Détail d'une activité par `strava_id` |
| `propose_workout` | Squelette de séance (récup, endurance, tempo, seuil, VO2max) |

**Règle d'or** : le LLM n'invente jamais de chiffre. Le system prompt impose
un appel de tool avant toute affirmation chiffrée.

Sessions persistées en SQLite (table `conversations`) — reprise via le
selectbox du dashboard. Mode `thinking` activé : le raisonnement est exposé
dans un expander pour le debug.

### Objectif d'entraînement

Optionnel : `data/objective.yaml` (gitignoré, template
`data/objective.yaml.example`).

```yaml
type: cyclosportive          # cyclosportive / course / cyclo / maintenance
date: 2026-09-15
distance_km: 150
elevation_m: 2800
target_ftp: 270              # optionnel
notes: "Étape du Tour, départ Megève"
```

## Installation

```bash
git clone https://github.com/arnaudstdr/domestique-ai.git
cd domestique-ai
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # remplissez les valeurs
```

## Configuration Strava OAuth

1. Créer une app sur <https://www.strava.com/settings/api> (note : `client_id`
   et `client_secret`).
2. Renseigner `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` et au moins l'un de :
   - `STRAVA_FTP` (TSS basé puissance), et/ou
   - `STRAVA_HR_REST` + `STRAVA_HR_MAX` (hr-TSS, prioritaire si renseignés).
3. Lancer le flow d'autorisation (une seule fois) :

   ```bash
   python -m domestique_ai.ingestion.strava_oauth_flow
   ```

4. Ouvrir l'URL affichée, autoriser, copier le `code` depuis l'URL de
   redirection, le coller.
5. Les tokens sont persistés dans `data/.strava_tokens.json` (jamais commité),
   avec rafraîchissement automatique.

## Configuration Ollama (coach LLM)

Le coach utilise [Ollama](https://ollama.com). Démarrer le serveur localement
ou pointer vers un endpoint distant via `OLLAMA_HOST`. Modèle par défaut :
`gemma4:31b-cloud` (override via `OLLAMA_MODEL`).

```bash
# Local
ollama pull gemma4:31b-cloud
ollama serve
```

## Utilisation

```bash
streamlit run domestique_ai/app/dashboard.py
```

Le dashboard expose deux onglets :

- **📊 Tableau de bord** : métriques courantes CTL / ATL / TSB + zone de
  forme, courbes d'évolution, répartition par zone HR, historique du poids,
  et **tableau d'activités cliquable** (vue détail au clic, voir plus haut).
- **🤖 Coach** : chat conversationnel avec le LLM, sessions multiples,
  raisonnement et tool calls visibles en expanders.

Boutons utilitaires en barre latérale : 🔄 Synchroniser Strava, 🔁
Recalculer la charge, 📥 Backfill HR max, 📥 Backfill zones HR.

## Déploiement

Image Docker prête pour Raspberry Pi (mode `network=host` pour joindre un
Ollama sur le même réseau). Voir [DEPLOY.md](DEPLOY.md).

```bash
docker compose up -d
```

## Tests

```bash
pytest
ruff check .
```

77 tests couvrent : calcul de charge, zones HR, ingestion Strava (mocks),
génération GPX, conversations, objectifs, tools du coach.

## Roadmap

- [ ] Détection automatique de signaux de surentraînement (HRV, FC repos).
- [ ] Génération de plans d'entraînement personnalisés par le coach.
- [ ] Support import direct Garmin (FIT files locaux, sans passer par Strava).
- [ ] Comparaison entre activités similaires (même parcours).

## Licence

MIT — voir [LICENSE](LICENSE).
