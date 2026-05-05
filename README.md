# DomestiqueAI 🚴‍♂️🤖

![Python](https://img.shields.io/badge/python-3.12-slim.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Dernier commit](https://img.shields.io/github/last-commit/arnaudstdr/domestique-ai)
![Stars](https://img.shields.io/github/stars/arnaudstdr/domestique-ai?style=social)

L'assistant intelligent pour cyclistes. Analyse automatique de la charge d'entraînement, état de forme, et recommandations personnalisées basées sur vos données Strava.

## Architecture

```text
domestique_ai/
├── config.py              # chemins de données, FTP, profil HR, secrets via .env
├── ingestion/
│   ├── strava.py          # client OAuth2 + persistance SQLite (pagination, refresh token)
│   └── strava_oauth_flow.py  # flow d'auth interactif
├── processing/
│   └── analyzer.py        # calculs TSS / hr-TSS (TRIMP), CTL, ATL, TSB
├── llm/
│   └── assistant.py       # wrapper Mistral Large (isolé, roadmap)
└── app/
    └── dashboard.py       # UI Streamlit (sync, filtres date, métriques de forme)
```

## Calcul de la charge d'entraînement

Deux modes au choix, automatiquement sélectionnés selon les données disponibles :

- **TSS puissance** — via `STRAVA_FTP` et la puissance moyenne de l'activité.
- **hr-TSS (TRIMP normalisé)** — via la HR moyenne, `STRAVA_HR_REST` et `STRAVA_HR_MAX`.
  TRIMP exponentiel de Banister, normalisé pour que 1h passé à `STRAVA_LTHR_PCT`
  (88% de la HRR par défaut) vaille 100 points : même échelle que le TSS power.

**Priorité** : si HR + HRrepos + HRmax sont configurés, hr-TSS prend le pas sur la puissance.
Pratique sans FTP fiable. Bouton « 🔁 Recalculer la charge » dans le dashboard pour
rejouer le score sur tout l'historique après changement de profil.

## Installation

```bash
git clone https://github.com/arnaudstdr/domestique-ai.git
cd domestique-ai
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # remplissez les valeurs
```

## Configuration Strava OAuth

1. Créer une app sur <https://www.strava.com/settings/api> (note : `client_id` et `client_secret`).
2. Renseigner `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` et au moins l'un de :
   - `STRAVA_FTP` (TSS basé puissance), et/ou
   - `STRAVA_HR_REST` + `STRAVA_HR_MAX` (hr-TSS, prioritaire si renseignés).
3. Lancer le flow d'autorisation :

   ```bash
   python -m domestique_ai.ingestion.strava_oauth_flow
   ```

4. Ouvrir l'URL affichée, autoriser, copier le `code` depuis l'URL de redirection, le coller.
5. Les tokens sont persistés dans `data/.strava_tokens.json` (jamais commité), avec rafraîchissement automatique.

## Utilisation

```bash
streamlit run domestique_ai/app/dashboard.py
```

Le dashboard affiche :

- les métriques courantes **CTL** (forme), **ATL** (fatigue), **TSB** (fraîcheur) avec zone de forme,
- les courbes d'évolution sur la plage de dates choisie,
- le détail des activités synchronisées,
- un bouton de synchronisation Strava dans la barre latérale.

## Tests

```bash
pytest
ruff check .
```

## Roadmap

- [ ] Brancher `MistralAssistant` au dashboard pour résumer la forme et proposer des plans.
- [ ] Support Garmin (FIT files).
- [ ] Génération automatique de plans d'entraînement personnalisés.
- [ ] Détection de signaux de surentraînement (HRV, FC repos).

## Licence

MIT — voir [LICENSE](LICENSE).
