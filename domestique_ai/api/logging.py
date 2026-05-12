"""Configuration centralisée du logging applicatif.

Format compact mais lisible : `[<ts>] <level> <logger> | <msg>`. Le niveau
est configurable via la variable d'environnement `DOMESTIQUE_AI_LOG_LEVEL`
(`DEBUG`, `INFO`, `WARNING`, `ERROR` — défaut `INFO`).

Les loggers `uvicorn.*` sont laissés intacts (uvicorn gère son propre formatter).
"""

from __future__ import annotations

import logging
import os
import sys

_DEFAULT_FMT = "[%(asctime)s] %(levelname)-7s %(name)-30s | %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

_CONFIGURED = False


def setup_logging() -> None:
    """Initialise le logger racine une fois pour toute l'application."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = os.getenv("DOMESTIQUE_AI_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_DEFAULT_FMT, datefmt=_DEFAULT_DATEFMT))

    root = logging.getLogger("domestique_ai")
    root.setLevel(level)
    # On veut éviter de polluer le root logger Python qui peut avoir des
    # handlers ajoutés par les libs tierces (urllib3, etc.). Tout se passe
    # sous le préfixe `domestique_ai`.
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False

    # On laisse uvicorn et fastapi parler avec leur format usuel (access log
    # + erreurs serveur). On baisse juste le niveau si l'utilisateur a passé
    # DEBUG pour qu'on voie aussi les détails uvicorn.
    if level <= logging.DEBUG:
        logging.getLogger("uvicorn").setLevel(logging.DEBUG)
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Renvoie un logger préfixé `domestique_ai.api.<name>`."""
    if not name.startswith("domestique_ai"):
        name = f"domestique_ai.api.{name}"
    return logging.getLogger(name)
