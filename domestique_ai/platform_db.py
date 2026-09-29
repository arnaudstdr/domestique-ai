"""DB plateforme — identité multi-tenant (comptes, sessions, invitations, liens).

Base SQLite séparée de la DB activités (``data/platform.db`` par défaut). Porte
l'identité transverse : utilisateurs (coach/athlète), tokens de session opaques
(stockés hashés), invitations à usage unique, et la relation coach↔athlète.

Mêmes idiomes que ``ingestion.db`` : connexions ouvertes/fermées par
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

from domestique_ai.config import (
    get_platform_db_path,
    get_session_secret,
    get_session_ttl_days,
)

VALID_ROLES = ("coach", "athlete")

# Nombre d'échecs de login consécutifs avant verrouillage temporaire du compte.
MAX_FAILED_ATTEMPTS = 5
# Durée du verrouillage après dépassement du seuil (minutes).
LOCKOUT_MINUTES = 15


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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Ajoute ``column`` à ``table`` si absente — migration douce idempotente.

    ``ddl`` est le type/contraintes SQL (ex. ``"TEXT"`` ou
    ``"INTEGER NOT NULL DEFAULT 0"``). SQLite ne supporte pas
    ``ADD COLUMN IF NOT EXISTS``, d'où le test via ``PRAGMA table_info``.
    """
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


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
                created_at TEXT NOT NULL,
                email TEXT,
                password_hash TEXT,
                totp_secret TEXT,
                totp_enabled INTEGER NOT NULL DEFAULT 0,
                password_changed_at TEXT,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT,
                avatar TEXT,
                garmin_email TEXT,
                garmin_password TEXT,
                email_verified INTEGER NOT NULL DEFAULT 0,
                coach_invite_code TEXT
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_public_id ON users(public_id)")
        # Au plus un coach bootstrap (index partiel).
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_bootstrap "
            "ON users(is_bootstrap) WHERE is_bootstrap = 1"
        )
        # Colonnes d'auth (mot de passe + 2FA TOTP) — migration additive sur les
        # bases existantes (les comptes créés avant n'ont ni email ni password).
        _ensure_column(conn, "users", "email", "TEXT")
        _ensure_column(conn, "users", "password_hash", "TEXT")
        _ensure_column(conn, "users", "totp_secret", "TEXT")
        _ensure_column(conn, "users", "totp_enabled", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "password_changed_at", "TEXT")
        _ensure_column(conn, "users", "failed_attempts", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "locked_until", "TEXT")
        # Photo de profil (data URL `data:image/<type>;base64,…`, redimensionnée
        # côté client). Migration additive : ``NULL`` = aucune photo.
        _ensure_column(conn, "users", "avatar", "TEXT")
        # Credentials Garmin Connect *par athlète* (le compte Garmin est celui de
        # l'athlète, pas un compte global). Migration additive : ``NULL`` = pas de
        # connexion Garmin. ``garmin_password`` n'est jamais exposé dans
        # ``_user_dict`` (cf. ``get_user_garmin_credentials``).
        _ensure_column(conn, "users", "garmin_email", "TEXT")
        _ensure_column(conn, "users", "garmin_password", "TEXT")
        # Token d'abonnement au flux iCalendar (webcal), par athlète. Remplace la
        # clé globale ``DOMESTIQUE_AI_CALENDAR_FEED_KEY`` (conservée en compat) :
        # l'URL du calendrier porte ce token opaque et identifie l'athlète, ce qui
        # évite d'exposer la clé globale et supprime le besoin de ``?athlete=``.
        # Jamais exposé dans ``_user_dict`` (secret).
        _ensure_column(conn, "users", "feed_token", "TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_feed_token "
            "ON users(feed_token) WHERE feed_token IS NOT NULL"
        )
        # Vérification d'email (inscription publique self-service). Les comptes
        # créés par invitation/legacy sont considérés vérifiés (lien de confiance).
        _ensure_column(conn, "users", "email_verified", "INTEGER NOT NULL DEFAULT 0")
        # Lien d'invitation réutilisable d'un coach (partagé aux athlètes). Stocké
        # en clair, comme ``feed_token`` : exposé uniquement à son propriétaire et
        # régénérable. Jamais dans ``_user_dict``.
        _ensure_column(conn, "users", "coach_invite_code", "TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_coach_invite_code "
            "ON users(coach_invite_code) WHERE coach_invite_code IS NOT NULL"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
            "ON users(email) WHERE email IS NOT NULL"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recovery_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                code_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used_at TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_codes_user ON recovery_codes(user_id)"
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
            CREATE TABLE IF NOT EXISTS auth_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                purpose TEXT NOT NULL CHECK (purpose IN ('email_verify', 'password_reset')),
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                consumed_at TEXT
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_tokens_hash ON auth_tokens(token_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id, purpose)"
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                public_id TEXT,
                role TEXT,
                author_email TEXT,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                page TEXT,
                app_version TEXT,
                user_agent TEXT,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC)")
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
        "email": row["email"],
        "email_verified": bool(row["email_verified"]),
        "totp_enabled": bool(row["totp_enabled"]),
        "has_password": bool(row["password_hash"]),
        "avatar": row["avatar"],
        "garmin_email": row["garmin_email"],
        "has_garmin_credentials": bool(row["garmin_email"] and row["garmin_password"]),
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


def _feedback_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "public_id": row["public_id"],
        "role": row["role"],
        "author_email": row["author_email"],
        "category": row["category"],
        "message": row["message"],
        "page": row["page"],
        "app_version": row["app_version"],
        "user_agent": row["user_agent"],
        "created_at": row["created_at"],
        "status": row["status"],
    }


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def create_user(
    role: str,
    display_name: str | None = None,
    is_bootstrap: bool = False,
    path: Path | None = None,
    *,
    email: str | None = None,
    password_hash: str | None = None,
    email_verified: bool = True,
) -> dict[str, Any]:
    """Crée un utilisateur. ``email``/``password_hash`` optionnels (inscription).

    ``email_verified`` vaut ``True`` par défaut pour les usages historiques
    (comptes bootstrap/invités = lien de confiance) ; l'inscription publique
    passe explicitement ``False``.
    """
    if role not in VALID_ROLES:
        raise ValueError(f"role invalide: {role!r}")
    conn = _connect(path)
    try:
        public_id = uuid.uuid4().hex
        normalized_email = (email or "").strip().lower() or None
        now = _now()
        cur = conn.execute(
            "INSERT INTO users (public_id, role, display_name, is_bootstrap, created_at, "
            "email, password_hash, password_changed_at, email_verified) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                public_id,
                role,
                display_name,
                1 if is_bootstrap else 0,
                now,
                normalized_email,
                password_hash,
                now if password_hash else None,
                1 if email_verified else 0,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _user_dict(row)
    finally:
        conn.close()


def set_email_verified(user_id: int, verified: bool = True, path: Path | None = None) -> None:
    """Pose le drapeau de vérification d'email d'un utilisateur."""
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE users SET email_verified = ? WHERE id = ?",
            (1 if verified else 0, user_id),
        )
        conn.commit()
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


# ---------------------------------------------------------------------------
# Feedback (retours des testeurs — data plateforme, cross-tenant)
# ---------------------------------------------------------------------------


def insert_feedback(
    *,
    category: str,
    message: str,
    user_id: int | None = None,
    public_id: str | None = None,
    role: str | None = None,
    author_email: str | None = None,
    page: str | None = None,
    app_version: str | None = None,
    user_agent: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Enregistre un retour utilisateur et le retourne sérialisé."""
    conn = _connect(path)
    try:
        now = _now()
        cur = conn.execute(
            "INSERT INTO feedback (user_id, public_id, role, author_email, category, "
            "message, page, app_version, user_agent, created_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')",
            (
                user_id,
                public_id,
                role,
                (author_email or "").strip().lower() or None,
                category,
                message,
                page,
                app_version,
                user_agent,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM feedback WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _feedback_dict(row)
    finally:
        conn.close()


def list_feedback(limit: int | None = None, path: Path | None = None) -> list[dict[str, Any]]:
    """Liste les retours, du plus récent au plus ancien."""
    conn = _connect(path)
    try:
        if limit is None:
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC, id DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_feedback_dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Credentials (email + mot de passe) et 2FA TOTP
# ---------------------------------------------------------------------------


def get_user_by_email(email: str, path: Path | None = None) -> dict[str, Any] | None:
    """Cherche un utilisateur par email (insensible à la casse). ``None`` si absent."""
    normalized = (email or "").strip().lower()
    if not normalized:
        return None
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM users WHERE lower(email) = ?", (normalized,)).fetchone()
        return _user_dict(row) if row else None
    finally:
        conn.close()


def get_user_credentials(user_id: int, path: Path | None = None) -> dict[str, Any] | None:
    """Renvoie les champs sensibles d'un user (password_hash, totp_secret, lockout).

    Séparé de ``_user_dict`` exprès : ces champs ne doivent jamais partir dans les
    payloads API. ``None`` si l'utilisateur n'existe pas.
    """
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT id, email, password_hash, totp_secret, totp_enabled, "
            "password_changed_at, failed_attempts, locked_until FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "password_hash": row["password_hash"],
            "totp_secret": row["totp_secret"],
            "totp_enabled": bool(row["totp_enabled"]),
            "password_changed_at": row["password_changed_at"],
            "failed_attempts": row["failed_attempts"],
            "locked_until": row["locked_until"],
        }
    finally:
        conn.close()


def set_user_credentials(
    user_id: int,
    email: str,
    password_hash: str,
    path: Path | None = None,
) -> None:
    """Pose/remplace l'email + le hash de mot de passe d'un utilisateur.

    ``email`` est normalisé en minuscules (unicité portée par l'index partiel).
    Lève ``sqlite3.IntegrityError`` si l'email est déjà pris par un autre compte.
    """
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE users SET email = ?, password_hash = ?, password_changed_at = ? WHERE id = ?",
            (
                (email or "").strip().lower(),
                password_hash,
                _now(),
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def revoke_all_sessions(user_id: int, path: Path | None = None) -> int:
    """Révoque toutes les sessions actives d'un utilisateur. Retourne le nombre révoqué.

    Utilisé à la réinitialisation du mot de passe (déconnexion globale).
    """
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_now(), user_id),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def set_password(user_id: int, password_hash: str, path: Path | None = None) -> None:
    """Met à jour le hash de mot de passe sans toucher à l'email (changement de mdp)."""
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
            (password_hash, _now(), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_user_avatar(user_id: int, avatar: str | None, path: Path | None = None) -> None:
    """Pose ou efface la photo de profil (data URL). ``None`` efface la colonne."""
    conn = _connect(path)
    try:
        conn.execute("UPDATE users SET avatar = ? WHERE id = ?", (avatar, user_id))
        conn.commit()
    finally:
        conn.close()


def get_user_by_feed_token(token: str, path: Path | None = None) -> dict[str, Any] | None:
    """Résout l'athlète propriétaire d'un token de flux iCalendar.

    ``None`` si le token est vide/inconnu. Le token lui-même n'est jamais
    retourné (``_user_dict`` ne l'expose pas).
    """
    normalized = (token or "").strip()
    if not normalized:
        return None
    conn = _connect(path)
    try:
        row = conn.execute("SELECT * FROM users WHERE feed_token = ?", (normalized,)).fetchone()
        return _user_dict(row) if row else None
    finally:
        conn.close()


def get_or_create_feed_token(user_id: int, path: Path | None = None) -> str:
    """Retourne le token de flux de l'utilisateur, en le générant si absent.

    Idempotent : un token déjà posé n'est jamais écrasé (utiliser
    ``rotate_feed_token`` pour le révoquer). Lève ``ValueError`` si l'utilisateur
    n'existe pas.
    """
    conn = _connect(path)
    try:
        row = conn.execute("SELECT feed_token FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError(f"utilisateur inconnu: {user_id}")
        existing = row["feed_token"]
        if existing:
            return existing
        token = secrets.token_urlsafe(32)
        conn.execute("UPDATE users SET feed_token = ? WHERE id = ?", (token, user_id))
        conn.commit()
        return token
    finally:
        conn.close()


def rotate_feed_token(user_id: int, path: Path | None = None) -> str:
    """Régénère (révoque puis remplace) le token de flux de l'utilisateur."""
    token = secrets.token_urlsafe(32)
    conn = _connect(path)
    try:
        conn.execute("UPDATE users SET feed_token = ? WHERE id = ?", (token, user_id))
        conn.commit()
        return token
    finally:
        conn.close()


def clear_feed_token(user_id: int, path: Path | None = None) -> None:
    """Efface le token de flux (désactive l'abonnement existant)."""
    conn = _connect(path)
    try:
        conn.execute("UPDATE users SET feed_token = NULL WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def set_user_garmin_credentials(
    user_id: int, email: str | None, password: str | None, path: Path | None = None
) -> None:
    """Pose/remplace les credentials Garmin Connect d'un athlète.

    ``email``/``password`` ``None`` (ou vides) effacent la connexion. Le mot de
    passe est stocké en clair dans ``platform.db`` — même niveau de protection
    que ``password_hash`` (fichier local non chiffré) ; il n'est **jamais** exposé
    par ``_user_dict``.
    """
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE users SET garmin_email = ?, garmin_password = ? WHERE id = ?",
            (
                (email or "").strip() or None,
                password or None,
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def clear_user_garmin_credentials(user_id: int, path: Path | None = None) -> None:
    """Efface la connexion Garmin d'un athlète (email + mot de passe)."""
    set_user_garmin_credentials(user_id, None, None, path=path)


def get_user_garmin_credentials(
    user_id: int, path: Path | None = None
) -> tuple[str | None, str | None]:
    """Credentials Garmin d'un athlète, lus explicitement (réservé au backend).

    Séparé de ``_user_dict`` exprès : le mot de passe ne doit jamais partir dans
    un payload API. Retourne ``(None, None)`` si l'utilisateur n'existe pas ou
    n'a pas de connexion Garmin.
    """
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT garmin_email, garmin_password FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return (None, None)
        return (row["garmin_email"], row["garmin_password"])
    finally:
        conn.close()


def set_totp_secret(user_id: int, secret: str, path: Path | None = None) -> None:
    """Enregistre un secret TOTP non encore confirmé (``totp_enabled`` reste 0)."""
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE users SET totp_secret = ?, totp_enabled = 0 WHERE id = ?",
            (secret, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def enable_totp(user_id: int, path: Path | None = None) -> bool:
    """Marque la 2FA comme active. ``False`` si aucun secret n'est enregistré."""
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE users SET totp_enabled = 1 "
            "WHERE id = ? AND totp_secret IS NOT NULL AND totp_secret != ''",
            (user_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def disable_totp(user_id: int, path: Path | None = None) -> None:
    """Désactive la 2FA et efface le secret + les codes de secours."""
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?",
            (user_id,),
        )
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def replace_recovery_codes(user_id: int, code_hashes: list[str], path: Path | None = None) -> None:
    """Remplace l'ensemble des codes de secours d'un utilisateur (les anciens sont purgés)."""
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
        now = _now()
        conn.executemany(
            "INSERT INTO recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, ?)",
            [(user_id, h, now) for h in code_hashes],
        )
        conn.commit()
    finally:
        conn.close()


def list_recovery_codes(
    user_id: int, include_used: bool = False, path: Path | None = None
) -> list[dict[str, Any]]:
    """Codes de secours d'un utilisateur (hashés). Par défaut, seuls les non utilisés."""
    conn = _connect(path)
    try:
        if include_used:
            rows = conn.execute(
                "SELECT id, code_hash, created_at, used_at FROM recovery_codes "
                "WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, code_hash, created_at, used_at FROM recovery_codes "
                "WHERE user_id = ? AND used_at IS NULL ORDER BY id",
                (user_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "code_hash": r["code_hash"],
                "created_at": r["created_at"],
                "used_at": r["used_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def mark_recovery_code_used(code_id: int, path: Path | None = None) -> bool:
    """Marque un code de secours comme consommé. ``False`` s'il était déjà utilisé."""
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE recovery_codes SET used_at = ? WHERE id = ? AND used_at IS NULL",
            (_now(), code_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def record_failed_login(user_id: int, path: Path | None = None) -> dict[str, Any]:
    """Incrémente le compteur d'échecs et verrouille le compte au-delà du seuil.

    Retourne ``{"failed_attempts", "locked_until"}`` après mise à jour.
    """
    conn = _connect(path)
    try:
        row = conn.execute("SELECT failed_attempts FROM users WHERE id = ?", (user_id,)).fetchone()
        attempts = (row["failed_attempts"] if row else 0) + 1
        locked_until: str | None = None
        if attempts >= MAX_FAILED_ATTEMPTS:
            locked_until = (
                dt.datetime.now(dt.UTC) + dt.timedelta(minutes=LOCKOUT_MINUTES)
            ).isoformat()
        conn.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
            (attempts, locked_until, user_id),
        )
        conn.commit()
        return {"failed_attempts": attempts, "locked_until": locked_until}
    finally:
        conn.close()


def clear_failed_login(user_id: int, path: Path | None = None) -> None:
    """Remet à zéro le compteur d'échecs et lève le verrou (après login réussi)."""
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


def user_is_locked(locked_until: str | None) -> bool:
    """True tant que le verrou de login est actif (``locked_until`` non expiré)."""
    return bool(locked_until) and not _is_expired(locked_until)


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


def _default_session_expiry() -> str | None:
    """Expiration par défaut d'une session (``get_session_ttl_days``). ``None`` si TTL désactivé."""
    days = get_session_ttl_days()
    if days <= 0:
        return None
    return (dt.datetime.now(dt.UTC) + dt.timedelta(days=days)).isoformat()


def create_session(
    user_id: int, expires_at: str | None = None, path: Path | None = None
) -> tuple[dict[str, Any], str]:
    """Crée une session pour ``user_id``. Retourne (dict, token_clair).

    Si ``expires_at`` est ``None``, la TTL par défaut s'applique
    (``DOMESTIQUE_AI_SESSION_TTL_DAYS``, 30 j ; ``0`` → pas d'expiration).
    """
    conn = _connect(path)
    try:
        token = _generate_token()
        cur = conn.execute(
            "INSERT INTO sessions (user_id, token_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (
                user_id,
                _hash_token(token),
                _now(),
                expires_at if expires_at is not None else _default_session_expiry(),
            ),
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
    plaintext: str,
    display_name: str | None = None,
    email: str | None = None,
    password_hash: str | None = None,
    path: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Consomme une invitation : crée l'utilisateur + une session (+ lien coach si applicable).

    Retourne (user_dict, session_token_clair). Lève ``InvitationError`` si
    l'invitation est inconnue, expirée ou déjà consommée. Transaction unique :
    si ``email`` est déjà pris (``sqlite3.IntegrityError``), rien n'est committé
    et l'invitation reste ``pending``.
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
        normalized_email = (email or "").strip().lower() or None
        cur = conn.execute(
            "INSERT INTO users (public_id, role, display_name, is_bootstrap, created_at, "
            "email, password_hash, password_changed_at, email_verified) "
            "VALUES (?, ?, ?, 0, ?, ?, ?, ?, 1)",
            (
                public_id,
                inv["role"],
                display_name,
                now,
                normalized_email,
                password_hash,
                now if password_hash else None,
            ),
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
            "INSERT INTO sessions (user_id, token_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, _hash_token(token), now, _default_session_expiry()),
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


def consume_invitation_for_link(
    plaintext: str, athlete_user_id: int, path: Path | None = None
) -> dict[str, Any]:
    """Consomme une invitation athlète pour RELIER un compte existant au coach.

    Ne crée ni utilisateur ni session : l'athlète possède déjà son compte et
    son identité. Le lien ``coach_athlete`` est créé, l'invitation passe
    ``accepted``. Retourne l'invitation consommée.

    Lève ``InvitationError`` si l'invitation est inconnue/expirée/déjà consommée,
    n'est pas une invitation athlète, ou n'a pas été émise par un coach.
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
            conn.execute("UPDATE invitations SET status = 'expired' WHERE id = ?", (inv["id"],))
            conn.commit()
            raise InvitationError("Invitation expirée.")
        if inv["role"] != "athlete":
            raise InvitationError("Cette invitation n'est pas destinée à un athlète.")
        created_by = inv["created_by"]
        inviter = (
            conn.execute("SELECT role FROM users WHERE id = ?", (created_by,)).fetchone()
            if created_by is not None
            else None
        )
        if inviter is None or inviter["role"] != "coach":
            raise InvitationError("Invitation sans coach émetteur.")

        now = _now()
        conn.execute(
            "INSERT OR IGNORE INTO coach_athlete (coach_id, athlete_id, created_at) "
            "VALUES (?, ?, ?)",
            (created_by, athlete_user_id, now),
        )
        conn.execute(
            "UPDATE invitations SET status = 'accepted', accepted_user_id = ?, "
            "accepted_at = ? WHERE id = ?",
            (athlete_user_id, now, inv["id"]),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM invitations WHERE id = ?", (inv["id"],)).fetchone()
        return _invitation_dict(updated)
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
# Tokens éphémères d'auth (vérification d'email, reset de mot de passe)
# ---------------------------------------------------------------------------


def create_auth_token(
    user_id: int,
    purpose: str,
    expires_at: str | None = None,
    path: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Crée un token éphémère (``email_verify`` | ``password_reset``).

    Retourne ``(row_dict, token_clair)``. Le clair n'est montré qu'ici. Invalide
    au passage les tokens actifs du même ``purpose`` (un seul actif à la fois).
    """
    if purpose not in ("email_verify", "password_reset"):
        raise ValueError(f"purpose invalide: {purpose!r}")
    conn = _connect(path)
    try:
        token = _generate_token()
        now = _now()
        conn.execute(
            "UPDATE auth_tokens SET consumed_at = ? "
            "WHERE user_id = ? AND purpose = ? AND consumed_at IS NULL",
            (now, user_id, purpose),
        )
        cur = conn.execute(
            "INSERT INTO auth_tokens (user_id, purpose, token_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, purpose, _hash_token(token), now, expires_at),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM auth_tokens WHERE id = ?", (cur.lastrowid,)).fetchone()
        return (
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "purpose": row["purpose"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "consumed_at": row["consumed_at"],
            },
            token,
        )
    finally:
        conn.close()


def consume_auth_token(
    plaintext: str, purpose: str, path: Path | None = None
) -> dict[str, Any] | None:
    """Valide et consomme un token éphémère (usage unique). Retourne le user.

    ``None`` si token inconnu, déjà consommé, d'un autre ``purpose`` ou expiré.
    """
    if not plaintext or purpose not in ("email_verify", "password_reset"):
        return None
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT id, user_id, expires_at, consumed_at FROM auth_tokens "
            "WHERE token_hash = ? AND purpose = ?",
            (_hash_token(plaintext), purpose),
        ).fetchone()
        if row is None or row["consumed_at"] is not None:
            return None
        if _is_expired(row["expires_at"]):
            return None
        conn.execute(
            "UPDATE auth_tokens SET consumed_at = ? WHERE id = ?",
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


# ---------------------------------------------------------------------------
# Lien d'invitation réutilisable d'un coach
# ---------------------------------------------------------------------------


def get_or_create_coach_invite_code(coach_id: int, path: Path | None = None) -> str:
    """Retourne le code d'invitation du coach, en le générant si absent.

    Idempotent : un code déjà posé n'est jamais écrasé (utiliser
    ``rotate_coach_invite_code`` pour le révoquer). Lève ``ValueError`` si
    l'utilisateur n'existe pas ou n'est pas un coach.
    """
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT role, coach_invite_code FROM users WHERE id = ?", (coach_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"utilisateur inconnu: {coach_id}")
        if row["role"] != "coach":
            raise ValueError("seul un coach a un lien d'invitation")
        existing = row["coach_invite_code"]
        if existing:
            return existing
        code = secrets.token_urlsafe(24)
        conn.execute("UPDATE users SET coach_invite_code = ? WHERE id = ?", (code, coach_id))
        conn.commit()
        return code
    finally:
        conn.close()


def get_user_by_coach_invite_code(code: str, path: Path | None = None) -> dict[str, Any] | None:
    """Résout le coach propriétaire d'un code d'invitation. ``None`` si inconnu."""
    normalized = (code or "").strip()
    if not normalized:
        return None
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE coach_invite_code = ? AND role = 'coach'",
            (normalized,),
        ).fetchone()
        return _user_dict(row) if row else None
    finally:
        conn.close()


def rotate_coach_invite_code(coach_id: int, path: Path | None = None) -> str:
    """Régénère (révoque puis remplace) le code d'invitation du coach."""
    code = secrets.token_urlsafe(24)
    conn = _connect(path)
    try:
        row = conn.execute("SELECT role FROM users WHERE id = ?", (coach_id,)).fetchone()
        if row is None:
            raise ValueError(f"utilisateur inconnu: {coach_id}")
        if row["role"] != "coach":
            raise ValueError("seul un coach a un lien d'invitation")
        conn.execute("UPDATE users SET coach_invite_code = ? WHERE id = ?", (code, coach_id))
        conn.commit()
        return code
    finally:
        conn.close()


def delete_user(user_id: int, path: Path | None = None) -> bool:
    """Supprime un utilisateur et tout ce qui lui est rattaché en DB plateforme.

    Sessions, invitations acceptées, codes de secours, tokens de reconnexion et
    liens ``coach_athlete`` sont supprimés en cascade (contraintes
    ``ON DELETE CASCADE`` + ``PRAGMA foreign_keys = ON``). Les invitations
    *créées* par l'utilisateur passent ``created_by`` à ``NULL`` (``ON DELETE SET
    NULL``). Ne touche PAS aux données athlète sur disque (base activités,
    tokens, YAML) — c'est le rôle de l'appelant (router roster).

    Retourne ``True`` si une ligne a été supprimée, ``False`` si l'utilisateur
    n'existe pas. Refuse le bootstrap (propriétaire) — ``ValueError``.
    """
    conn = _connect(path)
    try:
        row = conn.execute("SELECT is_bootstrap FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return False
        if row["is_bootstrap"]:
            raise ValueError("Le compte propriétaire (bootstrap) ne peut pas être supprimé.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()
