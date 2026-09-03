"""DB plateforme — identité multi-tenant (comptes, sessions, invitations, liens).

Base SQLite séparée de la DB activités (``data/platform.db`` par défaut). Porte
l'identité transverse : utilisateurs (coach/athlète), tokens de session opaques
(stockés hashés), invitations à usage unique, et la relation coach↔athlète.

Mêmes idiomes que ``ingestion.strava`` : connexions ouvertes/fermées par
fonction, ``CREATE TABLE IF NOT EXISTS``, schéma idempotent. Les tokens en clair
ne sortent du module qu'à deux endroits : création d'invitation
(``create_invitation``) et acceptation/login (``accept_invitation`` /
``create_session``). Tout le reste ne manipule que des hash.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from domestique_ai.config import get_platform_db_path, get_session_secret

VALID_ROLES = ("coach", "athlete")


class InvitationError(RuntimeError):
    """Invitation inconnue, expirée ou déjà consommée."""


# ---------------------------------------------------------------------------
# Bas niveau
# ---------------------------------------------------------------------------


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        when = dt.datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return dt.datetime.now(dt.UTC) >= when


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else get_platform_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _hash_token(plaintext: str) -> str:
    """HMAC-SHA256 du token (pepper = secret applicatif). Hex."""
    return hmac.new(get_session_secret(), plaintext.encode("utf-8"), hashlib.sha256).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def init_platform_db(path: Path | None = None) -> None:
    """Crée les tables de la DB plateforme. Idempotent."""
    db_path = Path(path) if path else get_platform_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL CHECK (role IN ('coach', 'athlete')),
                display_name TEXT,
                is_bootstrap INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_id ON users(public_id)")
        # Au plus un coach bootstrap (index partiel).
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_bootstrap "
            "ON users(is_bootstrap) WHERE is_bootstrap = 1"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                revoked_at TEXT,
                last_used_at TEXT
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invitations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL CHECK (role IN ('coach', 'athlete')),
                created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
                accepted_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                accepted_at TEXT
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_invitations_token_hash "
            "ON invitations(token_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invitations_created_by ON invitations(created_by)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                state_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                used_at TEXT
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_states_hash ON oauth_states(state_hash)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reconnect_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                used_at TEXT
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_reconnect_tokens_hash "
            "ON reconnect_tokens(token_hash)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coach_athlete (
                coach_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                athlete_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (coach_id, athlete_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_coach_athlete_athlete ON coach_athlete(athlete_id)"
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sérialisation (jamais token_hash dans les dicts exposés)
# ---------------------------------------------------------------------------


def _user_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "public_id": row["public_id"],
        "role": row["role"],
        "display_name": row["display_name"],
        "is_bootstrap": bool(row["is_bootstrap"]),
        "created_at": row["created_at"],
    }


def _invitation_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "created_by": row["created_by"],
        "status": row["status"],
        "accepted_user_id": row["accepted_user_id"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "accepted_at": row["accepted_at"],
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def create_user(
    role: str, display_name: str | None = None, is_bootstrap: bool = False, path: Path | None = None
) -> dict[str, Any]:
    if role not in VALID_ROLES:
        raise ValueError(f"role invalide: {role!r}")
    conn = _connect(path)
    try:
        public_id = uuid.uuid4().hex
        cur = conn.execute(
            "INSERT INTO users (public_id, role, display_name, is_bootstrap, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (public_id, role, display_name, 1 if is_bootstrap else 0, _now()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _user_dict(row)
    finally:
        conn.close()


def get_user_by_public_id(public_id: str, path: Path | None = None) -> dict[str, Any] | None:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM users WHERE public_id = ?", (public_id,)).fetchone()
        return _user_dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int, path: Path | None = None) -> dict[str, Any] | None:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_dict(row) if row else None
    finally:
        conn.close()


def list_users(role: str | None = None, path: Path | None = None) -> list[dict[str, Any]]:
    """Liste tous les utilisateurs (optionnellement filtrés par rôle), triés par id.

    Utilisé par le scheduler pour énumérer les athlètes à synchroniser.
    """
    conn = _connect(path)
    try:
        if role is None:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM users WHERE role = ? ORDER BY id", (role,)
            ).fetchall()
        return [_user_dict(r) for r in rows]
    finally:
        conn.close()


def get_bootstrap_coach(path: Path | None = None) -> dict[str, Any] | None:
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM users WHERE is_bootstrap = 1 LIMIT 1").fetchone()
        return _user_dict(row) if row else None
    finally:
        conn.close()


def get_or_create_bootstrap_coach(path: Path | None = None) -> dict[str, Any]:
    """Renvoie le coach propriétaire (créé à la volée la 1re fois). Idempotent."""
    existing = get_bootstrap_coach(path)
    if existing is not None:
        return existing
    try:
        return create_user(role="coach", display_name="Owner", is_bootstrap=True, path=path)
    except sqlite3.IntegrityError:
        # Course : un autre appel l'a créé entre-temps.
        existing = get_bootstrap_coach(path)
        if existing is None:
            raise
        return existing


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def create_session(
    user_id: int, expires_at: str | None = None, path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Crée une session pour ``user_id``. Retourne (dict, token_clair)."""
    conn = _connect(path)
    try:
        token = _generate_token()
        cur = conn.execute(
            "INSERT INTO sessions (user_id, token_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, _hash_token(token), _now(), expires_at),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, user_id, created_at, expires_at, revoked_at, last_used_at "
            "FROM sessions WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        session = {
            "id": row["id"],
            "user_id": row["user_id"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "last_used_at": row["last_used_at"],
        }
        return (session, token)
    finally:
        conn.close()


def resolve_session_token(plaintext: str, path: Path | None = None) -> dict[str, Any] | None:
    """Résout un token de session clair en utilisateur, ou None.

    None si : token inconnu, session révoquée, ou expirée. Met à jour
    ``last_used_at`` (best-effort).
    """
    if not plaintext:
        return None
    token_hash = _hash_token(plaintext)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT id, user_id, expires_at, revoked_at FROM sessions WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if _is_expired(row["expires_at"]):
            return None
        conn.execute(
            "UPDATE sessions SET last_used_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        return _user_dict(user) if user else None
    finally:
        conn.close()


def revoke_session(plaintext: str, path: Path | None = None) -> bool:
    """Révoque la session correspondant au token clair. True si une session active a été révoquée."""
    if not plaintext:
        return False
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (_now(), _hash_token(plaintext)),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def create_invitation(
    created_by: int | None, role: str, expires_at: str | None = None, path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Crée une invitation. Retourne (dict, token_clair). Le clair n'est montré qu'ici."""
    if role not in VALID_ROLES:
        raise ValueError(f"role invalide: {role!r}")
    conn = _connect(path)
    try:
        token = _generate_token()
        cur = conn.execute(
            "INSERT INTO invitations (token_hash, role, created_by, status, created_at, expires_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (_hash_token(token), role, created_by, _now(), expires_at),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM invitations WHERE id = ?", (cur.lastrowid,)).fetchone()
        return (_invitation_dict(row), token)
    finally:
        conn.close()


def list_invitations(
    created_by: int | None = None, path: Path | None = None
) -> list[dict[str, Any]]:
    conn = _connect(path)
    try:
        if created_by is None:
            rows = conn.execute("SELECT * FROM invitations ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM invitations WHERE created_by = ? ORDER BY id DESC",
                (created_by,),
            ).fetchall()
        return [_invitation_dict(r) for r in rows]
    finally:
        conn.close()


def revoke_invitation(
    invitation_id: int, created_by: int | None = None, path: Path | None = None
) -> bool:
    """Révoque une invitation ``pending``. Retourne True si une ligne a changé.

    Si ``created_by`` est fourni, seule une invitation créée par ce coach peut
    être révoquée (isolation). On ne révoque que les invitations encore
    ``pending`` (une invitation déjà acceptée/révoquée n'est pas touchée).
    """
    conn = _connect(path)
    try:
        if created_by is None:
            cur = conn.execute(
                "UPDATE invitations SET status = 'revoked' WHERE id = ? AND status = 'pending'",
                (invitation_id,),
            )
        else:
            cur = conn.execute(
                "UPDATE invitations SET status = 'revoked' "
                "WHERE id = ? AND status = 'pending' AND created_by = ?",
                (invitation_id, created_by),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def accept_invitation(
    plaintext: str, display_name: str | None = None, path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Consomme une invitation : crée l'utilisateur + une session (+ lien coach si applicable).

    Retourne (user_dict, session_token_clair). Lève ``InvitationError`` si
    l'invitation est inconnue, expirée ou déjà consommée. Transaction unique.
    """
    if not plaintext:
        raise InvitationError("Invitation invalide.")
    token_hash = _hash_token(plaintext)
    conn = _connect(path)
    try:
        inv = conn.execute(
            "SELECT * FROM invitations WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if inv is None:
            raise InvitationError("Invitation inconnue.")
        if inv["status"] != "pending":
            raise InvitationError("Invitation déjà utilisée ou révoquée.")
        if _is_expired(inv["expires_at"]):
            conn.execute(
                "UPDATE invitations SET status = 'expired' WHERE id = ?",
                (inv["id"],),
            )
            conn.commit()
            raise InvitationError("Invitation expirée.")

        now = _now()
        public_id = uuid.uuid4().hex
        cur = conn.execute(
            "INSERT INTO users (public_id, role, display_name, is_bootstrap, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (public_id, inv["role"], display_name, now),
        )
        user_id = cur.lastrowid

        # Lien coach↔athlète si l'invite vient d'un coach et crée un athlète.
        created_by = inv["created_by"]
        if created_by is not None and inv["role"] == "athlete":
            inviter = conn.execute("SELECT role FROM users WHERE id = ?", (created_by,)).fetchone()
            if inviter is not None and inviter["role"] == "coach":
                conn.execute(
                    "INSERT OR IGNORE INTO coach_athlete (coach_id, athlete_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (created_by, user_id, now),
                )

        token = _generate_token()
        conn.execute(
            "INSERT INTO sessions (user_id, token_hash, created_at) VALUES (?, ?, ?)",
            (user_id, _hash_token(token), now),
        )
        conn.execute(
            "UPDATE invitations SET status = 'accepted', accepted_user_id = ?, "
            "accepted_at = ? WHERE id = ?",
            (user_id, now, inv["id"]),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return (_user_dict(user), token)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# OAuth state (anti-CSRF + porteur d'identité pour le callback Strava)
# ---------------------------------------------------------------------------


def create_oauth_state(
    user_id: int, expires_at: str | None = None, path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Crée un ``state`` OAuth lié à ``user_id``. Retourne (row, state_clair)."""
    conn = _connect(path)
    try:
        token = _generate_token()
        cur = conn.execute(
            "INSERT INTO oauth_states (user_id, state_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, _hash_token(token), _now(), expires_at),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, user_id, created_at, expires_at, used_at FROM oauth_states WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        state = {
            "id": row["id"],
            "user_id": row["user_id"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "used_at": row["used_at"],
        }
        return (state, token)
    finally:
        conn.close()


def consume_oauth_state(plaintext: str, path: Path | None = None) -> dict[str, Any] | None:
    """Valide et consomme un ``state`` (usage unique). Retourne le user, ou None.

    None si : state inconnu, déjà utilisé, ou expiré. Marque ``used_at`` au passage.
    """
    if not plaintext:
        return None
    state_hash = _hash_token(plaintext)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT id, user_id, expires_at, used_at FROM oauth_states WHERE state_hash = ?",
            (state_hash,),
        ).fetchone()
        if row is None or row["used_at"] is not None:
            return None
        if _is_expired(row["expires_at"]):
            return None
        conn.execute(
            "UPDATE oauth_states SET used_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        return _user_dict(user) if user else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tokens de reconnexion (athlète déconnecté → nouvelle session, usage unique)
# ---------------------------------------------------------------------------


def create_reconnect_token(
    user_id: int, expires_at: str | None = None, path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Crée un token de reconnexion lié à ``user_id``. Retourne (row, token_clair)."""
    conn = _connect(path)
    try:
        token = _generate_token()
        cur = conn.execute(
            "INSERT INTO reconnect_tokens (user_id, token_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, _hash_token(token), _now(), expires_at),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, user_id, created_at, expires_at, used_at "
            "FROM reconnect_tokens WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return (
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "used_at": row["used_at"],
            },
            token,
        )
    finally:
        conn.close()


def consume_reconnect_token(plaintext: str, path: Path | None = None) -> dict[str, Any] | None:
    """Valide et consomme un token de reconnexion (usage unique). Retourne le user.

    None si : token inconnu, déjà utilisé, ou expiré. Marque ``used_at`` au passage.
    """
    if not plaintext:
        return None
    token_hash = _hash_token(plaintext)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT id, user_id, expires_at, used_at FROM reconnect_tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if row is None or row["used_at"] is not None:
            return None
        if _is_expired(row["expires_at"]):
            return None
        conn.execute(
            "UPDATE reconnect_tokens SET used_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        return _user_dict(user) if user else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Relations coach ↔ athlète
# ---------------------------------------------------------------------------


def link_coach_athlete(coach_id: int, athlete_id: int, path: Path | None = None) -> None:
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO coach_athlete (coach_id, athlete_id, created_at) "
            "VALUES (?, ?, ?)",
            (coach_id, athlete_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def list_athletes_for_coach(coach_id: int, path: Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT u.* FROM coach_athlete ca "
            "JOIN users u ON u.id = ca.athlete_id "
            "WHERE ca.coach_id = ? ORDER BY u.id",
            (coach_id,),
        ).fetchall()
        return [_user_dict(r) for r in rows]
    finally:
        conn.close()
