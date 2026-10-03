"""Tests du service du build React : fallback SPA vs vrai 404.

``SPAStaticFiles`` sert ``index.html`` pour les routes React (sans extension)
mais doit renvoyer un 404 pour les fichiers manquants (extension) et les zones
techniques (``/api/``, ``/assets/``, ``/fonts/``) — un ``robots.txt`` absent ne
doit pas répondre l'app shell HTML.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from domestique_ai.api.main import SPAStaticFiles


def _client(tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    (dist / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    app = FastAPI()
    app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="frontend")
    return TestClient(app)


def test_spa_fallback_for_react_routes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/cgu")
    assert r.status_code == 200
    assert "shell" in r.text


def test_existing_static_file_is_served(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.text.startswith("User-agent")


def test_missing_files_and_technical_paths_return_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/missing.txt").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/fonts/missing.woff2").status_code == 404
    assert client.get("/api/unknown").status_code == 404
