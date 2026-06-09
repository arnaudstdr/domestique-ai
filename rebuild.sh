#!/usr/bin/env bash
#
# rebuild.sh — reconstruit l'image API sans cache, relance la stack en
# arrière-plan, puis suit les logs du conteneur de l'API.
#
# Usage : ./rebuild.sh
set -euo pipefail

# Service ciblé dans docker-compose.yml (pas de container_name fixe : le nom
# réel est généré par Compose, on le résout donc dynamiquement plus bas).
API_SERVICE="api"

# Détecte la commande Compose disponible (plugin v2 ou binaire v1).
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "Erreur : ni 'docker compose' ni 'docker-compose' n'est disponible." >&2
  exit 1
fi

echo ">> Build de l'image (--no-cache)..."
"${COMPOSE[@]}" build --no-cache

echo ">> Démarrage de la stack en arrière-plan..."
"${COMPOSE[@]}" up -d

echo ">> Résolution du conteneur du service '${API_SERVICE}'..."
API_CONTAINER="$("${COMPOSE[@]}" ps -q "${API_SERVICE}")"

if [ -z "${API_CONTAINER}" ]; then
  echo "Erreur : impossible de trouver le conteneur du service '${API_SERVICE}'." >&2
  exit 1
fi

echo ">> Suivi des logs du conteneur API (${API_CONTAINER}). Ctrl-C pour quitter."
exec docker logs -f "${API_CONTAINER}"
