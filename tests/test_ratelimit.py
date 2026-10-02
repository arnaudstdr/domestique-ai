"""Tests du rate-limiter in-process."""

from __future__ import annotations

from domestique_ai import ratelimit


def test_allows_up_to_limit() -> None:
    for _ in range(3):
        assert ratelimit.check("b", "k", max_events=3, window_seconds=60) is True


def test_blocks_above_limit() -> None:
    for _ in range(3):
        ratelimit.check("b", "k", max_events=3, window_seconds=60)
    assert ratelimit.check("b", "k", max_events=3, window_seconds=60) is False


def test_keys_are_isolated() -> None:
    ratelimit.check("b", "k1", max_events=1, window_seconds=60)
    assert ratelimit.check("b", "k2", max_events=1, window_seconds=60) is True


def test_buckets_are_isolated() -> None:
    ratelimit.check("b1", "k", max_events=1, window_seconds=60)
    assert ratelimit.check("b2", "k", max_events=1, window_seconds=60) is True


def test_window_expiry(monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock[0])
    assert ratelimit.check("b", "k", max_events=1, window_seconds=10) is True
    assert ratelimit.check("b", "k", max_events=1, window_seconds=10) is False
    clock[0] += 11
    assert ratelimit.check("b", "k", max_events=1, window_seconds=10) is True


def test_client_ip_reads_client_host() -> None:
    class _Req:
        client = type("C", (), {"host": "1.2.3.4"})()

    assert ratelimit.client_ip(_Req()) == "1.2.3.4"


def test_client_ip_fallback() -> None:
    class _Req:
        client = None

    assert ratelimit.client_ip(_Req()) == "unknown"


def test_client_ip_ignores_xff_by_default(monkeypatch) -> None:
    """Sans proxy de confiance, l'en-tête spoofable est ignoré."""
    monkeypatch.delenv("DOMESTIQUE_AI_TRUSTED_PROXY", raising=False)

    class _Req:
        client = type("C", (), {"host": "10.0.0.1"})()
        headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}

    assert ratelimit.client_ip(_Req()) == "10.0.0.1"


def test_client_ip_uses_xff_when_trusted(monkeypatch) -> None:
    """Derrière un proxy de confiance, on prend le premier hop (le client)."""
    monkeypatch.setenv("DOMESTIQUE_AI_TRUSTED_PROXY", "1")

    class _Req:
        client = type("C", (), {"host": "10.0.0.1"})()
        headers = {"x-forwarded-for": "1.2.3.4, 10.0.0.1"}

    assert ratelimit.client_ip(_Req()) == "1.2.3.4"


def test_client_ip_empty_xff_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("DOMESTIQUE_AI_TRUSTED_PROXY", "1")

    class _Req:
        client = type("C", (), {"host": "10.0.0.1"})()
        headers = {"x-forwarded-for": "   "}

    assert ratelimit.client_ip(_Req()) == "10.0.0.1"
