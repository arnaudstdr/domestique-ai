"""Tests des primitives de sécurité (mot de passe, TOTP, recovery, challenge)."""

from __future__ import annotations

import time

import pyotp
import pytest

from domestique_ai import security


def test_hash_and_verify_password_roundtrip():
    hashed = security.hash_password("correct horse battery")
    assert hashed != "correct horse battery"
    assert security.verify_password(hashed, "correct horse battery") is True
    assert security.verify_password(hashed, "wrong password") is False


def test_verify_password_handles_missing_or_invalid_hash():
    assert security.verify_password(None, "secret") is False
    assert security.verify_password("", "secret") is False
    assert security.verify_password("not-a-hash", "secret") is False
    assert security.verify_password(security.hash_password("x"), "") is False


def test_assert_password_strength():
    security.assert_password_strength("longenough1")  # ne lève pas
    with pytest.raises(security.PasswordPolicyError):
        security.assert_password_strength("short")


def test_totp_secret_and_verification():
    secret = security.new_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert security.verify_totp(secret, code) is True
    assert security.verify_totp(secret, "000000") is False
    assert security.verify_totp(secret, "abcdef") is False
    assert security.verify_totp(None, code) is False


def test_totp_uri_and_qr():
    secret = security.new_totp_secret()
    uri = security.totp_uri(secret, "alice@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "alice%40example.com" in uri or "alice@example.com" in uri
    data_uri = security.totp_qr_svg_data_uri(uri)
    assert data_uri.startswith("data:image/svg+xml;base64,")


def test_recovery_codes_generation_and_verification():
    codes = security.generate_recovery_codes(3)
    assert len(codes) == 3
    assert all("-" in c for c in codes)
    hashed = security.hash_recovery_code(codes[0])
    assert security.verify_recovery_code(hashed, codes[0]) is True
    # Normalisation : casse et tirets ignorés.
    assert security.verify_recovery_code(hashed, codes[0].upper().replace("-", "")) is True
    assert security.verify_recovery_code(hashed, codes[1]) is False
    assert security.verify_recovery_code("", codes[0]) is False


def test_login_challenge_roundtrip_and_tamper():
    token = security.create_login_challenge(42)
    assert security.verify_login_challenge(token) == 42
    assert security.verify_login_challenge(token + "x") is None
    assert security.verify_login_challenge("garbage") is None
    assert security.verify_login_challenge("") is None

    body, _, _ = token.partition(".")
    assert security.verify_login_challenge(f"{body}.deadbeef") is None


def test_login_challenge_expired():
    token = security.create_login_challenge(7, ttl_sec=-1)
    assert security.verify_login_challenge(token) is None


def test_login_challenge_scope_mismatch(monkeypatch):
    # Un token signé avec un autre scope ne doit pas passer.
    import hashlib
    import hmac
    import json

    from domestique_ai.config import get_session_secret

    body = security._b64url(
        json.dumps(
            {"scope": "other", "uid": 1, "exp": int(time.time()) + 60},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    sig = hmac.new(get_session_secret(), body.encode(), hashlib.sha256).hexdigest()
    assert security.verify_login_challenge(f"{body}.{sig}") is None
