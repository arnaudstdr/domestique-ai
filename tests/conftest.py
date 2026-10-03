"""Fixtures partagées pour la suite de tests."""

from __future__ import annotations

import os

import pytest

# Sentry doit être coupé AVANT tout import de ``domestique_ai.api.main`` : son
# ``_init_sentry()`` s'exécute à l'import du module (pas au lifespan) et un
# ``setenv`` en fixture s'exécute par test — trop tard, la collecte a déjà
# importé ``main`` et initialisé Sentry avec le ``SENTRY_DSN`` du ``.env``. Sans
# ça, les exceptions volontairement levées par les tests (swallow tests)
# polluent le projet Sentry (issues DOMESTIQUE-AI-1..H, 2026-10-03).
os.environ["SENTRY_ENABLED"] = "0"


@pytest.fixture(autouse=True)
def _isolate_platform_db(tmp_path, monkeypatch):
    """Isole la DB plateforme ET la racine des espaces athlètes par test.

    Le ``lifespan`` FastAPI initialise désormais la DB plateforme ; sans cette
    isolation, les tests qui montent l'app écriraient dans ``data/platform.db``
    (réel) et partageraient un état entre tests.

    ``DOMESTIQUE_AI_ATHLETES_ROOT`` est isolé pareil : les tests qui créent un
    athlète non-bootstrap (auth, roster, prescriptions) écriraient sinon dans
    ``data/athletes/`` réel — 315 dossiers orphelins accumulés sur un run.
    """
    monkeypatch.setenv("DOMESTIQUE_AI_PLATFORM_DB_PATH", str(tmp_path / "platform.db"))
    monkeypatch.setenv("DOMESTIQUE_AI_ATHLETES_ROOT", str(tmp_path / "athletes"))
    # Les tests qui montent `TestClient(app)` sans `with` ne déclenchent pas le
    # lifespan (donc pas l'init plateforme) ; on l'initialise ici pour tous.
    from domestique_ai.platform_db import init_platform_db

    init_platform_db()


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    """Vide l'état du rate-limiter in-process entre les tests (état global module)."""
    from domestique_ai import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture()
def api_auth_headers() -> dict[str, str]:
    """Header Bearer pour les tests qui montent le vrai ``app``.

    ``main.py`` active ``BearerAuthMiddleware`` dès que ``DOMESTIQUE_AI_API_TOKEN``
    est renseigné (config chargée depuis ``.env``). On renvoie un header construit
    depuis le token de l'environnement ; en auth-off (token absent), ``{}``
    (le middleware est désactivé, aucune entête nécessaire).
    """
    from domestique_ai.config import get_api_token

    token = get_api_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}
