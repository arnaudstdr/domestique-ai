"""Fixtures partagées pour la suite de tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_platform_db(tmp_path, monkeypatch):
    """Isole la DB plateforme par test.

    Le ``lifespan`` FastAPI initialise désormais la DB plateforme ; sans cette
    isolation, les tests qui montent l'app écriraient dans ``data/platform.db``
    (réel) et partageraient un état entre tests.
    """
    monkeypatch.setenv("DOMESTIQUE_AI_PLATFORM_DB_PATH", str(tmp_path / "platform.db"))
    # Les tests qui montent `TestClient(app)` sans `with` ne déclenchent pas le
    # lifespan (donc pas l'init plateforme) ; on l'initialise ici pour tous.
    from domestique_ai.platform_db import init_platform_db

    init_platform_db()


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
