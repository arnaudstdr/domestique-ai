#!/usr/bin/env bash
#
# rebuild.sh — reconstruit l'image API sans cache, relance la stack en
# arrière-plan, puis suit les logs du conteneur de l'API.
#
# Pour le dev local sur macOS, on charge explicitement docker-compose.yml +
# docker-compose.override.yml (réseau bridge + port 8501 publié). Le fichier
# override est optionnel : s'il est absent (ex. sur le Pi), on retombe sur la
# config de base.
#
# Usage : ./rebuild.sh
set -euo pipefail

# Service ciblé dans docker-compose.yml (container_name fixe plus bas).
APP_SERVICE="app"

# Détecte la commande Compose disponible (plugin v2 ou binaire v1).
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Erreur : ni 'docker compose' ni 'docker-compose' n'est disponible." >&2
  exit 1
fi

# Charge l'override dev local s'il existe, sinon la config de base seule.
COMPOSE_FILES=(-f docker-compose.yml)
if [ -f docker-compose.override.yml ]; then
  COMPOSE_FILES+=(-f docker-compose.override.yml)
  echo ">> Override dev local détecté (docker-compose.override.yml)."
fi

echo ">> Démarrage de la stack en arrière-plan..."
"${COMPOSE[@]}" "${COMPOSE_FILES[@]}" up -d --build

echo ">> Résolution du conteneur du service '${APP_SERVICE}'..."
APP_CONTAINER="$("${COMPOSE[@]}" "${COMPOSE_FILES[@]}" ps -q "${APP_SERVICE}")"

if [ -z "${APP_CONTAINER}" ]; then
  echo "Erreur : impossible de trouver le conteneur du service '${APP_SERVICE}'." >&2
  exit 1
fi

echo ">> Suivi des logs du conteneur (${APP_CONTAINER}). Ctrl-C pour quitter."
exec docker logs -f "${APP_CONTAINER}"
