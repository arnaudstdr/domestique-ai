# Makefile — raccourcis Docker Compose.
#
# Sur macOS, docker-compose.override.yml (non versionné, cf. .gitignore)
# repasse en réseau bridge + publie 8501:8501. Sur le Pi, le fichier est
# absent : on retombe sur la config de base (network_mode: host).
#
# `make rebuild` reconstruit l'image (up -d --build) puis suit les logs de
# l'API. Ici comme dans l'ancien rebuild.sh, l'override est chargé s'il existe.

COMPOSE := $(shell if docker compose version >/dev/null 2>&1; then echo docker compose; else echo docker-compose; fi)
COMPOSE_FILES := -f docker-compose.yml $(if $(wildcard docker-compose.override.yml),-f docker-compose.override.yml)

.PHONY: all up down restart logs rebuild

# Cible par défaut : down puis up -d.
all: down up

up:
	$(COMPOSE) $(COMPOSE_FILES) up -d

down:
	$(COMPOSE) $(COMPOSE_FILES) down

restart: down up

logs:
	$(COMPOSE) $(COMPOSE_FILES) logs -f app

rebuild:
	$(COMPOSE) $(COMPOSE_FILES) up -d --build
	@container="$$($(COMPOSE) $(COMPOSE_FILES) ps -q app)"; \
	if [ -z "$$container" ]; then \
		echo "Erreur : impossible de trouver le conteneur du service 'app'." >&2; \
		exit 1; \
	fi; \
	exec docker logs -f "$$container"
