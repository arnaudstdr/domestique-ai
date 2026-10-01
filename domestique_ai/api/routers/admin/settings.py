"""Réglages plateforme (override runtime des variables d'env)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from domestique_ai import platform_db
from domestique_ai.api.deps import require_admin
from domestique_ai.api.routers.admin._common import audit

router = APIRouter()

_MAX_BROADCAST = 500

# Clés de réglages du module d'observabilité LLM (cf. ``admin/llm_usage.py``).
_PRICE_PROMPT_KEY = "llm_price_prompt_per_1k"
_PRICE_CACHED_KEY = "llm_price_cached_per_1k"
_PRICE_COMPLETION_KEY = "llm_price_completion_per_1k"
_MODEL_PRICES_KEY = "llm_model_prices"
_WEEKLY_QUOTA_KEY = "llm_weekly_quota_units"
_WEIGHTS_KEY = "llm_model_weights"
_ALERT_PCT_KEY = "llm_alert_pct"


def _float_setting(key: str, default: float = 0.0) -> float:
    raw = platform_db.get_setting(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class AdminSettings(BaseModel):
    signup_enabled: bool
    maintenance_mode: bool = False
    broadcast_message: str | None = None
    # Observabilité LLM — tarifs plats (fallback USD /1k), quota hebdo pondéré,
    # seuil d'alerte, et poids/tarifs par modèle (JSON). Les prix par modèle
    # (``llm_model_prices``) priment sur les tarifs plats.
    llm_price_prompt_per_1k: float = 0.0
    llm_price_cached_per_1k: float = 0.0
    llm_price_completion_per_1k: float = 0.0
    llm_weekly_quota_units: float = 0.0
    llm_alert_pct: float = 90.0
    llm_model_weights: str = ""
    llm_model_prices: str = ""


class SettingsUpdate(BaseModel):
    signup_enabled: bool | None = None
    maintenance_mode: bool | None = None
    broadcast_message: str | None = Field(default=None, max_length=_MAX_BROADCAST)
    llm_price_prompt_per_1k: float | None = Field(default=None, ge=0)
    llm_price_cached_per_1k: float | None = Field(default=None, ge=0)
    llm_price_completion_per_1k: float | None = Field(default=None, ge=0)
    llm_weekly_quota_units: float | None = Field(default=None, ge=0)
    llm_alert_pct: float | None = Field(default=None, ge=0, le=100)
    llm_model_weights: str | None = None
    llm_model_prices: str | None = None


def _current_settings() -> AdminSettings:
    announcement = platform_db.get_announcement()
    return AdminSettings(
        signup_enabled=platform_db.effective_signup_enabled(),
        maintenance_mode=announcement["maintenance_mode"],
        broadcast_message=announcement["message"],
        llm_price_prompt_per_1k=_float_setting(_PRICE_PROMPT_KEY),
        llm_price_cached_per_1k=_float_setting(_PRICE_CACHED_KEY),
        llm_price_completion_per_1k=_float_setting(_PRICE_COMPLETION_KEY),
        llm_weekly_quota_units=_float_setting(_WEEKLY_QUOTA_KEY),
        llm_alert_pct=_float_setting(_ALERT_PCT_KEY, 90.0),
        llm_model_weights=platform_db.get_setting(_WEIGHTS_KEY) or "",
        llm_model_prices=platform_db.get_setting(_MODEL_PRICES_KEY) or "",
    )


@router.get("/settings", response_model=AdminSettings)
def get_settings() -> AdminSettings:
    """Réglages plateforme effectifs (override DB sinon variable d'env)."""
    return _current_settings()


@router.put("/settings", response_model=AdminSettings)
def update_settings(
    body: SettingsUpdate,
    admin: dict = Depends(require_admin),  # noqa: B008
) -> AdminSettings:
    """Met à jour les réglages plateforme (override en base)."""
    data = body.model_dump(exclude_unset=True)
    if "signup_enabled" in data and data["signup_enabled"] is not None:
        platform_db.set_setting("signup_enabled", "1" if data["signup_enabled"] else "0")
    if "maintenance_mode" in data and data["maintenance_mode"] is not None:
        platform_db.set_setting("maintenance_mode", "1" if data["maintenance_mode"] else "0")
    if "broadcast_message" in data:
        message = (data["broadcast_message"] or "").strip()
        if len(message) > _MAX_BROADCAST:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Message trop long (max {_MAX_BROADCAST} caractères).",
            )
        platform_db.set_setting("broadcast_message", message or None)
    for key in (
        "llm_price_prompt_per_1k",
        "llm_price_cached_per_1k",
        "llm_price_completion_per_1k",
        "llm_weekly_quota_units",
        "llm_alert_pct",
    ):
        if key in data and data[key] is not None:
            platform_db.set_setting(key, repr(float(data[key])))
    for key in ("llm_model_weights", "llm_model_prices"):
        if key in data and data[key] is not None:
            raw = str(data[key]).strip()
            if raw:
                import json

                try:
                    json.loads(raw)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"{key} : JSON invalide ({exc}).",
                    ) from exc
            platform_db.set_setting(key, raw or None)
    if data:
        audit(admin, "settings_update", details=data)
    return _current_settings()
