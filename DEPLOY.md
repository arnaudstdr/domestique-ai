# Déploiement sur Raspberry Pi 5 (via Tailscale)

PWA FastAPI + React conteneurisée, accessible depuis tous tes appareils du tailnet.

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

### 2.1 — Générer les secrets

L'API est protégée par une authentification **email + mot de passe + 2FA**
(TOTP) par compte. Le token `DOMESTIQUE_AI_API_TOKEN` est conservé comme
**break-glass** : il résout vers le coach propriétaire si tu es verrouillé
dehors, et protège aussi les endpoints en cas d'auth désactivée.

```bash
# Sur la machine de dev — générer les secrets
openssl rand -hex 32   # DOMESTIQUE_AI_API_TOKEN
openssl rand -hex 32   # DOMESTIQUE_AI_SESSION_SECRET
```

Ajouter dans `.env` (côté dev **ET** côté RPi) :

```
DOMESTIQUE_AI_API_TOKEN=<hex>
DOMESTIQUE_AI_SESSION_SECRET=<hex>
```

Le token API reste **obligatoire en prod** (le port `8501` est joignable depuis
le LAN du RPi, cf. `network_mode: host`). Sans lui, l'auth est désactivée.


### 2.2 — Copier `.env` et les données

Depuis ta machine de dev, copier les fichiers locaux vers le RPi (remplacer `<rpi>` par le hostname Tailscale ou l'IP du RPi) :

```bash
# Secrets / config (inclut DOMESTIQUE_AI_API_TOKEN)
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
docker compose logs -f          # vérifier que FastAPI démarre (port 8501)
```

Le premier build prend quelques minutes (compilation pandas/pyarrow en ARM64).

### 3.1 — Créer le compte coach (bootstrap)

La base `platform.db` est migrée automatiquement et **sans perte** au démarrage
(colonnes additives). Le coach propriétaire n'a pas d'identifiants par défaut :
crée-les via la CLI locale (dans le conteneur), puis active la 2FA.

```bash
# Depuis le RPi
cd ~/domestique-ai

# 1) Email + mot de passe (saisie sans écho)
docker compose exec app python -m domestique_ai.auth_cli set-credentials \
    --email moi@exemple.com

# 2) Active la 2FA : affiche un QR code + la clé manuelle + les codes de secours
docker compose exec app python -m domestique_ai.auth_cli enroll-totp

# En cas de pépin : lister les comptes ou réinitialiser la 2FA
docker compose exec app python -m domestique_ai.auth_cli list-users
docker compose exec app python -m domestique_ai.auth_cli reset-2fa
```

Les athlètes, eux, passent par le lien d'invitation du roster : ils créent leur
email + mot de passe et activent la 2FA eux-mêmes. Les sessions historiques
(déjà connectées) ne sont pas déconnectées — elles définissent leurs identifiants
à la prochaine connexion.

## Étape 4 — Accès depuis le tailnet

Depuis n'importe quel appareil connecté au même tailnet (laptop, téléphone, tablette) :

```
http://<rpi-tailnet-hostname>:8501
```

Le hostname est celui affiché par `tailscale status` côté RPi (ex. `raspberrypi.tail-scale.ts.net` ou simplement `raspberrypi`).

Au premier chargement, la PWA redirige vers `/login` : saisis ton **email**, ton
**mot de passe**, puis le **code à 6 chiffres** de ton application
d'authentification. La session est ensuite stockée en `localStorage` du
navigateur. Le token `DOMESTIQUE_AI_API_TOKEN` reste utilisable en dépannage
(break-glass) si tu perds tes identifiants.

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
- **Pas d'exposition publique** : le port `8501` n'est joignable que depuis ton tailnet (et le LAN du RPi). L'app dispose désormais d'une auth par compte (mot de passe + 2FA) ; pour une exposition publique, ajouter tout de même un Tailscale Funnel + TLS.

## Calendrier Apple — flux d'abonnement iCalendar (webcal)

Le canal pour afficher les séances du plan dans Calendrier Apple/Google : un
**flux ICS à URL stable** que l'appareil interroge directement (abonnement
« webcal »). Aucun passage par le canal iCloud→appareils — robuste même quand
la sync CalDAV d'iOS se comporte mal. La fenêtre servie est la **semaine en
cours + la semaine à venir**, et elle évolue après chaque revue hebdo.

1. Générer une clé et l'ajouter au `.env` :
   ```
   DOMESTIQUE_AI_CALENDAR_FEED_KEY=<openssl rand -hex 24>
   ```
2. Redémarrer le conteneur.
3. Sur l'iPhone, **Réglages > Calendrier > Comptes > Ajouter un compte >
   Autre > Calendrier d'abonnement**, puis coller :
   ```
   https://ai-stack.tail68aa7e.ts.net/api/plan/feed.ics?key=<CLÉ>
   ```
   (ou le hostname tailnet de ton RPi). Pour cibler un athlète du roster :
   `&athlete=<public_id>`.

Le calendrier se met à jour tout seul après chaque revue hebdo. La description
de chaque séance (structure par zones + TSS) est visible en ouvrant
l'événement. Le flux est désactivé (404) sans clé.

Le calendrier se met à jour tout seul après chaque revue hebdo (fenêtre = la
semaine à venir). La description de chaque séance (structure par zones + TSS)
est visible en ouvrant l'événement. Le flux est désactivé (404) sans clé.
