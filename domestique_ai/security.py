"""Primitives de sécurité de l'auth : hachage mot de passe, TOTP, recovery codes,
challenge 2FA signé.

Isole toute la crypto dans un module sans dépendance à FastAPI ni à la
persistance, pour rester testable et réutilisable (CLI de bootstrap, endpoints).
Le secret HMAC du challenge provient de ``get_session_secret()`` — même pepper
que les tokens de session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import secrets
import time
from typing import Any

import pyotp
import qrcode
import qrcode.image.svg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from domestique_ai.config import get_session_secret

_hasher = PasswordHasher()

PASSWORD_MIN_LENGTH = 10
TOTP_ISSUER = "DomestiqueAI"
# Tolérance de ±1 pas de 30 s (dérive d'horloge du téléphone).
TOTP_VALID_WINDOW = 1
RECOVERY_CODE_COUNT = 10
CHALLENGE_TTL_SEC = 300


# ---------------------------------------------------------------------------
# Mot de passe
# ---------------------------------------------------------------------------


class PasswordPolicyError(ValueError):
    """Mot de passe refusé par la politique (trop court, etc.)."""


def assert_password_strength(password: str) -> None:
    """Valide la politique minimale. Lève ``PasswordPolicyError`` sinon."""
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(
            f"Le mot de passe doit contenir au moins {PASSWORD_MIN_LENGTH} caractères."
        )


def hash_password(password: str) -> str:
    """Hash argon2id du mot de passe (sel inclus dans la chaîne retournée)."""
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Vérifie un mot de passe contre son hash argon2. False si inconnu/invalide."""
    if not password_hash or not password:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# TOTP (RFC 6238)
# ---------------------------------------------------------------------------


def new_totp_secret() -> str:
    """Nouveau secret TOTP base32 (à afficher une seule fois à l'enrôlement)."""
    return pyotp.random_base32()


def totp_uri(secret: str, account: str, issuer: str = TOTP_ISSUER) -> str:
    """URI ``otpauth://`` à encoder en QR pour l'app d'authentification."""
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def totp_qr_svg_data_uri(uri: str) -> str:
    """QR code de ``uri`` en data-URL SVG (base64) — pas de dépendance Pillow."""
    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def verify_totp(secret: str | None, code: str) -> bool:
    """Vérifie un code TOTP à 6 chiffres (fenêtre ±1 pas). False si malformé."""
    if not secret or not code:
        return False
    candidate = code.strip().replace(" ", "")
    if not candidate.isdigit():
        return False
    return bool(pyotp.TOTP(secret).verify(candidate, valid_window=TOTP_VALID_WINDOW))


# ---------------------------------------------------------------------------
# Codes de secours
# ---------------------------------------------------------------------------


def _normalize_recovery_code(code: str) -> str:
    return (code or "").strip().lower().replace(" ", "").replace("-", "")


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Génère ``count`` codes de secours en clair (affichés une seule fois)."""
    codes: list[str] = []
    for _ in range(count):
        raw = secrets.token_hex(5)  # 10 caractères hex
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery_code(code: str) -> str:
    """Hash argon2 d'un code de secours (normalisé : casse, tirets, espaces)."""
    return _hasher.hash(_normalize_recovery_code(code))


def verify_recovery_code(code_hash: str, code: str) -> bool:
    """Vérifie un code de secours contre son hash argon2."""
    normalized = _normalize_recovery_code(code)
    if not code_hash or not normalized:
        return False
    try:
        return _hasher.verify(code_hash, normalized)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# Challenge 2FA (stateless, signé HMAC, courte durée)
# ---------------------------------------------------------------------------


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def create_login_challenge(user_id: int, ttl_sec: int = CHALLENGE_TTL_SEC) -> str:
    """Token signé prouvant que le mot de passe a été validé (attente du TOTP).

    Stateless : ``payload.signature``. L'usage est borné par la TTL courte et la
    vérification du scope ``"2fa"`` — pas de stockage.
    """
    payload: dict[str, Any] = {
        "scope": "2fa",
        "uid": user_id,
        "exp": int(time.time()) + ttl_sec,
        "nonce": secrets.token_hex(8),
    }
    body = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        get_session_secret(), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{body}.{signature}"


def verify_login_challenge(token: str) -> int | None:
    """Valide un challenge 2FA. Retourne l'``user_id``, ou ``None`` si invalide/expiré."""
    if not token or "." not in token:
        return None
    body, _, signature = token.partition(".")
    expected = hmac.new(
        get_session_secret(), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    if payload.get("scope") != "2fa":
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    uid = payload.get("uid")
    return int(uid) if isinstance(uid, int) else None
