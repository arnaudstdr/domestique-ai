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
