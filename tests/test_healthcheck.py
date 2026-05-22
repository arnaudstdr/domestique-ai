"""Tests du module healthcheck.py (ping Healthchecks.io)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from domestique_ai import healthcheck


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HEALTHCHECKS_PING_URL", raising=False)
    yield


def test_ping_no_op_when_url_missing(monkeypatch):
    """Sans URL configurée, aucun appel HTTP."""

    def fake_get(*args, **kwargs):
        raise AssertionError("requests.get ne doit pas être appelé")

    monkeypatch.setattr(healthcheck.requests, "get", fake_get)
    assert healthcheck.ping_healthcheck() is False


def test_ping_no_op_when_url_blank(monkeypatch):
    """Une URL composée uniquement d'espaces est traitée comme absente."""
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "   ")

    def fake_get(*args, **kwargs):
        raise AssertionError("ne doit pas être appelé")

    monkeypatch.setattr(healthcheck.requests, "get", fake_get)
    assert healthcheck.ping_healthcheck() is False


def test_ping_calls_get_on_url(monkeypatch):
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc-123")

    captured: dict = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(healthcheck.requests, "get", fake_get)
    assert healthcheck.ping_healthcheck() is True
    assert captured["url"] == "https://hc-ping.com/abc-123"
    assert captured["timeout"] == healthcheck._HTTP_TIMEOUT_SEC


def test_ping_strips_whitespace_in_url(monkeypatch):
    """URL avec espaces parasites — on les strip pour éviter une erreur HTTP."""
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "  https://hc-ping.com/abc  ")

    captured: dict = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(healthcheck.requests, "get", fake_get)
    healthcheck.ping_healthcheck()
    assert captured["url"] == "https://hc-ping.com/abc"


def test_ping_returns_false_on_4xx(monkeypatch):
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/bad")

    def fake_get(url, timeout=None):
        resp = MagicMock()
        resp.status_code = 404
        return resp

    monkeypatch.setattr(healthcheck.requests, "get", fake_get)
    assert healthcheck.ping_healthcheck() is False


def test_ping_returns_false_on_network_error(monkeypatch):
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc")

    def fake_get(*args, **kwargs):
        raise requests.Timeout("trop long")

    monkeypatch.setattr(healthcheck.requests, "get", fake_get)
    # Ne doit pas lever — la fonction avale et retourne False.
    assert healthcheck.ping_healthcheck() is False


def test_ping_returns_true_on_2xx(monkeypatch):
    """Healthchecks.io renvoie typiquement 200 ; 201/204 doivent aussi passer."""
    monkeypatch.setenv("HEALTHCHECKS_PING_URL", "https://hc-ping.com/abc")

    for code in (200, 201, 204, 299):
        def fake_get(*args, _code=code, **kwargs):
            resp = MagicMock()
            resp.status_code = _code
            return resp

        monkeypatch.setattr(healthcheck.requests, "get", fake_get)
        assert healthcheck.ping_healthcheck() is True, f"code {code}"
