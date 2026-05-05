# Déploiement sur Raspberry Pi 5 (via Tailscale)

App Streamlit conteneurisée, accessible depuis tous tes appareils du tailnet.

## Prérequis sur le RPi

- Raspberry Pi OS 64-bit (ARM64)
- Docker + Docker Compose plugin :
  ```bash
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER   # puis se reconnecter
  ```
- Tailscale déjà installé et connecté (`tailscale status` doit afficher le RPi).

## Étape 1 — Récupérer le projet

```bash
git clone https://github.com/arnaudstdr/domestique-ai.git
cd domestique-ai
```

## Étape 2 — Préparer la configuration et les données

Depuis ta machine de dev, copier les fichiers locaux vers le RPi (remplacer `<rpi>` par le hostname Tailscale ou l'IP du RPi) :

```bash
# Secrets / config
scp .env <rpi>:~/domestique-ai/.env

# Données persistantes (DB, tokens Strava, objectif)
scp data/strava_activities.db <rpi>:~/domestique-ai/data/
scp data/.strava_tokens.json   <rpi>:~/domestique-ai/data/
scp data/objective.yaml        <rpi>:~/domestique-ai/data/
```

> Les tokens Strava se rafraîchissent automatiquement côté app — pas besoin de rejouer le flow OAuth sur le RPi.

## Étape 3 — Build et démarrage

Sur le RPi :

```bash
cd ~/domestique-ai
docker compose up -d --build
docker compose logs -f          # vérifier que Streamlit démarre
```

Le premier build prend quelques minutes (compilation pandas/pyarrow en ARM64).

## Étape 4 — Accès depuis le tailnet

Depuis n'importe quel appareil connecté au même tailnet (laptop, téléphone, tablette) :

```
http://<rpi-tailnet-hostname>:8501
```

Le hostname est celui affiché par `tailscale status` côté RPi (ex. `raspberrypi.tail-scale.ts.net` ou simplement `raspberrypi`).

## Maintenance

```bash
# Mettre à jour l'app après un git pull
git pull && docker compose up -d --build

# Logs
docker compose logs -f --tail=200

# Stop / restart
docker compose stop
docker compose restart

# Nettoyage complet (garde le volume data/)
docker compose down
```

## Sauvegarde

Tout l'état persistant tient dans `./data/` (DB SQLite + tokens Strava + objectif). Un simple `tar czf backup.tgz data/` suffit.

## Notes

- **Ollama** : la coach LLM utilise `gemma4:31b-cloud` via Ollama Cloud — aucun service à héberger sur le RPi, juste une connexion Internet.
- **Pas de TLS** : Tailscale chiffre déjà bout-en-bout entre tes appareils. Inutile de coller un reverse proxy devant pour un usage perso.
- **Pas d'exposition publique** : le port `8501` n'est joignable que depuis ton tailnet (et le LAN du RPi). Pour exposer en clear sur Internet, ajouter un Tailscale Funnel — non recommandé ici (pas d'auth applicative).
