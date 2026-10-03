"""Routeur de portabilité — export complet des données personnelles.

``GET /api/auth/me/export`` produit une archive ZIP contenant les données du
compte (identité, consentements, profil, activités, santé, conversations). Droit
à la portabilité RGPD : le fichier est lisible tel quel (JSON/YAML texte).

Les données dérivées/volumineuses ne sont pas incluses (streams bruts,
embeddings mémoire) : elles sont recalculables et sans valeur pour l'utilisateur.
L'export d'un plan (§ export ICS/ZIP) reste géré par ``plan.py``.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import sqlite3
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status

from domestique_ai import ratelimit
from domestique_ai.api.deps import get_athlete_context, get_current_user
from domestique_ai.api.logging import get_logger
from domestique_ai.athlete_context import AthleteContext
from domestique_ai.platform_db import record_admin_audit

router = APIRouter(prefix="/api/auth", tags=["account"])
log = get_logger("account")

# Tables de la DB athlète exportées telles quelles. Exclues volontairement :
# ``activity_streams`` (streams bruts volumineux, recalculables) et
# ``memory_vectors`` (embeddings dérivés des conversations).
_EXPORT_TABLES = (
    "activities",
    "morning_metrics",
    "weight_history",
    "conversations",
    "session_summaries",
    "coach_memory",
    "training_plans",
    "plan_decisions",
    "prescriptions",
)

_EXPORT_MAX_PER_HOUR = 5


def _account_payload(user: dict) -> dict:
    """Identité et consentements du compte — jamais de secret (hash, tokens, TPM)."""
    return {
        "public_id": user["public_id"],
        "role": user["role"],
        "display_name": user.get("display_name"),
        "email": user.get("email"),
        "created_at": user.get("created_at"),
        "email_verified": bool(user.get("email_verified")),
        "totp_enabled": bool(user.get("totp_enabled")),
        "garmin_email": user.get("garmin_email"),
        "consents": {
            "terms_accepted_at": user.get("terms_accepted_at"),
            "terms_accepted_version": user.get("terms_accepted_version"),
            "health_consent_at": user.get("health_consent_at"),
            "health_consent_version": user.get("health_consent_version"),
            "health_consent_withdrawn_at": user.get("health_consent_withdrawn_at"),
        },
    }


def _dump_table(db_path: Path, table: str) -> list[dict] | None:
    """Lit une table de la DB athlète en liste de dicts. ``None`` si absente."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 — nom fixe
        except sqlite3.OperationalError:
            return None
        return [dict(row) for row in rows]
    finally:
        conn.close()


def build_export_zip(user: dict, ctx: AthleteContext) -> bytes:
    """Construit l'archive ZIP d'export des données de ``user``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "account.json",
            json.dumps(_account_payload(user), ensure_ascii=False, indent=2),
        )
        if ctx.profile_path.exists():
            archive.writestr("profil/profile.yaml", ctx.profile_path.read_text(encoding="utf-8"))
        exported = []
        for table in _EXPORT_TABLES:
            rows = _dump_table(ctx.db_path, table)
            if rows is None:
                continue
            exported.append(table)
            archive.writestr(
                f"donnees/{table}.json",
                json.dumps(rows, ensure_ascii=False, indent=2, default=str),
            )
        archive.writestr(
            "LISEZ-MOI.txt",
            "Export de vos données DomestiqueAI\n"
            "==================================\n\n"
            "account.json : identité du compte et consentements enregistrés.\n"
            "profil/profil.yaml : profil athlète (FTP, zones, etc.).\n"
            "donnees/*.json : activités, métriques de santé, conversations.\n\n"
            f"Tables incluses : {', '.join(exported) or 'aucune'}.\n"
            "Non inclus (données dérivées recalculables) : streams bruts, "
            "embeddings de la mémoire du coach.\n",
        )
    return buffer.getvalue()


@router.get("/me/export")
def export_me(
    user: dict = Depends(get_current_user),  # noqa: B008
    ctx: AthleteContext = Depends(get_athlete_context),  # noqa: B008
) -> Response:
    """Archive ZIP des données personnelles du compte courant (portabilité RGPD)."""
    if not ratelimit.check(
        "export_user",
        str(user["id"]),
        max_events=_EXPORT_MAX_PER_HOUR,
        window_seconds=3600,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop d'exports demandés. Réessaie dans une heure.",
        )
    payload = build_export_zip(user, ctx)
    try:
        record_admin_audit(user, "account_export", target=user)
    except Exception:  # noqa: BLE001 — la traçabilité ne doit pas casser l'export
        log.exception("Export compte %s : écriture d'audit échouée.", user["public_id"][:8])
    log.info("Export RGPD généré pour %s (%d octets)", user["public_id"][:8], len(payload))
    filename = f"domestique-ai-export-{dt.date.today().isoformat()}.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
