"""Tests du module notifications.py (client Pushover, best-effort)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from domestique_ai import notifications


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Neutralise les vars Pushover par défaut pour chaque test."""
    for key in (
        "PUSHOVER_USER_KEY",
        "PUSHOVER_APP_TOKEN",
        "PUSHOVER_DEVICE",
        "PUSHOVER_PRIORITY_DEFAULT",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


# ---- send_pushover ---------------------------------------------------------


def test_send_pushover_no_op_when_not_configured(monkeypatch):
    """Sans creds, on ne fait aucun appel HTTP et on retourne False."""
    called = {"n": 0}

    def fake_post(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("requests.post ne doit pas être appelé")

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    assert notifications.send_pushover("hello", "world") is False
    assert called["n"] == 0


def test_send_pushover_no_op_when_token_missing(monkeypatch):
    """User seul (sans token) doit aussi être traité comme non configuré."""
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")

    def fake_post(*args, **kwargs):
        raise AssertionError("ne doit pas être appelé")

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    assert notifications.send_pushover("hello", "world") is False


def test_send_pushover_posts_payload(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")

    captured: dict = {}

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(notifications.requests, "post", fake_post)

    assert notifications.send_pushover("Title", "Body") is True
    assert captured["url"] == notifications._PUSHOVER_URL
    assert captured["data"] == {
        "user": "u-key",
        "token": "app-tok",
        "title": "Title",
        "message": "Body",
        "priority": 0,
    }
    assert captured["timeout"] == notifications._HTTP_TIMEOUT_SEC


def test_send_pushover_includes_device_when_set(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")
    monkeypatch.setenv("PUSHOVER_DEVICE", "iphone")

    captured: dict = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    notifications.send_pushover("T", "B")
    assert captured["data"]["device"] == "iphone"


def test_send_pushover_respects_default_priority(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")
    monkeypatch.setenv("PUSHOVER_PRIORITY_DEFAULT", "1")

    captured: dict = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    notifications.send_pushover("T", "B")
    assert captured["data"]["priority"] == 1


def test_send_pushover_clamps_default_priority(monkeypatch):
    """Pushover accepte -2 à 2 ; toute valeur hors borne est clampée."""
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")
    monkeypatch.setenv("PUSHOVER_PRIORITY_DEFAULT", "99")
    assert notifications._default_priority() == 2

    monkeypatch.setenv("PUSHOVER_PRIORITY_DEFAULT", "-99")
    assert notifications._default_priority() == -2


def test_send_pushover_invalid_priority_falls_back(monkeypatch):
    monkeypatch.setenv("PUSHOVER_PRIORITY_DEFAULT", "pas-un-nombre")
    assert notifications._default_priority() == 0


def test_send_pushover_explicit_priority_overrides_default(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")
    monkeypatch.setenv("PUSHOVER_PRIORITY_DEFAULT", "1")

    captured: dict = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    notifications.send_pushover("T", "B", priority=-1)
    assert captured["data"]["priority"] == -1


def test_send_pushover_returns_false_on_http_error(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")

    def fake_post(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = 400
        resp.text = '{"errors":["invalid token"]}'
        return resp

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    assert notifications.send_pushover("T", "B") is False


def test_send_pushover_returns_false_on_network_exception(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")

    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("dns fail")

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    assert notifications.send_pushover("T", "B") is False


# ---- notify_sync_completed --------------------------------------------------


def test_notify_sync_completed_noop_when_zero():
    """0 nouvelle activité = pas de notif (anti-spam)."""
    # Pas besoin de mock : la fonction sort avant tout appel.
    assert notifications.notify_sync_completed(0) is False
    assert notifications.notify_sync_completed(-1) is False


def test_notify_sync_completed_singular(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")

    captured: dict = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    notifications.notify_sync_completed(1)
    assert captured["data"]["title"] == "Nouvelle activité"
    assert "Une nouvelle activité" in captured["data"]["message"]


def test_notify_sync_completed_plural(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER_KEY", "u-key")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "app-tok")

    captured: dict = {}

    def fake_post(url, data=None, timeout=None):
        captured["data"] = data
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(notifications.requests, "post", fake_post)
    notifications.notify_sync_completed(3)
    assert captured["data"]["title"] == "Nouvelles activités"
    assert "3 nouvelles activités" in captured["data"]["message"]


def test_notify_sync_completed_swallows_unexpected_exception(monkeypatch):
    """Si une exception remonte malgré send_pushover, on doit l'avaler."""

    def boom(*args, **kwargs):
        raise RuntimeError("inattendu")

    monkeypatch.setattr(notifications, "send_pushover", boom)
    # Ne doit pas lever — la notif est best-effort.
    assert notifications.notify_sync_completed(1) is False
