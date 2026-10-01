"""Observabilité de l'usage Ollama (tokens, latence, coût) — panneau admin.

Deux vues :

- ``GET /ollama-usage`` : conso **app** réelle sur une fenêtre glissante — appels,
  tokens (prompt/cached/completion), latence, coût estimé (tarifs configurables),
  erreurs, agrégats par type d'appel / modèle / athlète, derniers appels.
- ``GET /ollama-cloud`` : **reconstitution façon console Ollama Cloud** — requêtes
  du mois par modèle, quota hebdo pondéré calibré (l'admin cale la valeur pour
  coller au % vu sur ollama.com, qu'aucune API n'expose), projection d'épuisement
  et recommandation de forfait.

Les compteurs bruts (requêtes, tokens) sont exacts ; le **% de quota est une
estimation** (Ollama ne publie ni quota en tokens fixe ni endpoint d'usage).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel

from domestique_ai import platform_db
from domestique_ai.llm.usage import label_human

router = APIRouter()

_PRICE_PROMPT_KEY = "llm_price_prompt_per_1k"
_PRICE_COMPLETION_KEY = "llm_price_completion_per_1k"
_PRICE_CACHED_KEY = "llm_price_cached_per_1k"
_MODEL_PRICES_KEY = "llm_model_prices"
_WEEKLY_QUOTA_KEY = "llm_weekly_quota_units"
_WEIGHTS_KEY = "llm_model_weights"
_ALERT_PCT_KEY = "llm_alert_pct"

_DEFAULT_ALERT_PCT = 90.0
# Poids par défaut par niveau de modèle Ollama (1 léger → 4 extra-lourd). Sert
# au quota pondéré quand l'admin n'a pas défini de mapping explicite.
_DEFAULT_MODEL_WEIGHTS = {"gemma4:31b-cloud": 2.0}
# Tarifs par défaut du modèle cloud principal (USD / 1M → ramenés au /1k),
# d'après la fiche modèle ollama.com. Surchargeables par modèle via
# ``llm_model_prices``, sinon retombée sur les tarifs plats ci-dessus.
_DEFAULT_MODEL_PRICES = {
    "gemma4:31b-cloud": {"prompt": 0.00014, "cached": 0.00005, "completion": 0.00040}
}


def _float_setting(key: str, default: float) -> float:
    raw = platform_db.get_setting(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _json_setting(key: str) -> dict:
    import json

    raw = platform_db.get_setting(key)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _flat_prices() -> tuple[float, float, float]:
    """Tarifs plats USD / 1k (prompt, cached, completion) — fallback par défaut."""
    return (
        _float_setting(_PRICE_PROMPT_KEY, 0.0),
        _float_setting(_PRICE_CACHED_KEY, 0.0),
        _float_setting(_PRICE_COMPLETION_KEY, 0.0),
    )


def _model_prices() -> dict[str, tuple[float, float, float]]:
    """Tarifs USD / 1k par modèle (``prompt``, ``cached``, ``completion``).

    Source : ``llm_model_prices`` (JSON) ; sinon tarifs par défaut connus
    (``_DEFAULT_MODEL_PRICES``) ; sinon tarifs plats. Un modèle absent retombe
    sur les tarifs plats.
    """
    parsed = _json_setting(_MODEL_PRICES_KEY)
    out: dict[str, tuple[float, float, float]] = {}
    for model, spec in {**_DEFAULT_MODEL_PRICES, **parsed}.items():
        if not isinstance(spec, dict):
            continue
        try:
            out[str(model)] = (
                float(spec.get("prompt", 0.0)),
                float(spec.get("cached", 0.0)),
                float(spec.get("completion", 0.0)),
            )
        except (ValueError, TypeError):
            continue
    return out


def _prices_for(model: str | None) -> tuple[float, float, float]:
    """Tarifs (prompt, cached, completion) /1k pour un modèle donné."""
    model_prices = _model_prices()
    if model and model in model_prices:
        return model_prices[model]
    return _flat_prices()


def _weights() -> dict[str, float]:
    parsed = _json_setting(_WEIGHTS_KEY)
    if not parsed:
        return dict(_DEFAULT_MODEL_WEIGHTS)
    out: dict[str, float] = {}
    for model, weight in parsed.items():
        try:
            out[str(model)] = float(weight)
        except (ValueError, TypeError):
            continue
    return out


def _model_weight(model: str | None, weights: dict[str, float]) -> float:
    if model and model in weights:
        return weights[model]
    return 1.0


def _day_slice(days: int) -> str:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=max(1, days))
    return since.isoformat()


# ---------------------------------------------------------------------------
# Vue 1 — usage app
# ---------------------------------------------------------------------------


class UsageTotals(BaseModel):
    calls: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int
    total_tokens: int
    errors: int
    avg_duration_ms: float | None = None
    estimated_cost_usd: float
    price_prompt_per_1k: float
    price_cached_per_1k: float
    price_completion_per_1k: float


class UsageBreakdown(BaseModel):
    key: str
    label: str
    calls: int
    prompt_tokens: int
    completion_tokens: int
    errors: int
    estimated_cost_usd: float


class UsageActor(BaseModel):
    public_id: str | None = None
    display_name: str | None = None
    calls: int
    total_tokens: int
    estimated_cost_usd: float


class UsageCall(BaseModel):
    id: int
    created_at: str
    actor_public_id: str | None = None
    label: str | None = None
    label_human: str
    entrypoint: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_duration_ms: float | None = None
    status: str
    error_type: str | None = None
    tools_count: int


class AdminOllamaUsage(BaseModel):
    days: int
    since: str
    totals: UsageTotals
    by_label: list[UsageBreakdown]
    by_model: list[UsageBreakdown]
    by_actor: list[UsageActor]
    recent: list[UsageCall]


def _cost(
    prompt_tokens: int,
    completion_tokens: int,
    price_prompt: float,
    price_comp: float,
    cached_tokens: int = 0,
    price_cached: float = 0.0,
) -> float:
    """Coût USD d'un appel.

    ``prompt_tokens`` (``prompt_eval_count`` chez Ollama) **inclut** les tokens
    servis depuis le cache : on facture ``(prompt - cached)`` au tarif input et
    ``cached`` au tarif (moins cher) du cache.
    """
    cached = min(cached_tokens, prompt_tokens)
    fresh = max(0, prompt_tokens - cached)
    return round(
        fresh / 1000.0 * price_prompt
        + cached / 1000.0 * price_cached
        + completion_tokens / 1000.0 * price_comp,
        4,
    )


@router.get("/ollama-usage", response_model=AdminOllamaUsage)
def get_ollama_usage(days: int = Query(default=30, ge=1, le=365)) -> AdminOllamaUsage:
    """Usage Ollama réel de l'app sur ``days`` jours (tokens, latence, coût)."""
    since = _day_slice(days)
    rows = platform_db.fetch_llm_calls(since=since)

    prompt_tokens = sum(r["prompt_tokens"] or 0 for r in rows)
    cached_tokens = sum(r["cached_tokens"] or 0 for r in rows)
    completion_tokens = sum(r["completion_tokens"] or 0 for r in rows)
    errors = sum(1 for r in rows if r["status"] != "ok")
    durations = [r["total_duration_ms"] for r in rows if r["total_duration_ms"] is not None]

    by_label: dict[str, UsageBreakdown] = {}
    by_model: dict[str, UsageBreakdown] = {}
    by_actor: dict[str, UsageActor] = {}
    total_cost = 0.0

    for r in rows:
        price_prompt, price_cached, price_comp = _prices_for(r["model"])
        call_cost = _cost(
            r["prompt_tokens"] or 0,
            r["completion_tokens"] or 0,
            price_prompt,
            price_comp,
            cached_tokens=r["cached_tokens"] or 0,
            price_cached=price_cached,
        )
        total_cost += call_cost

        label_key = r["label"] or "—"
        entry = by_label.setdefault(
            label_key,
            UsageBreakdown(
                key=label_key,
                label=label_human(r["label"]),
                calls=0,
                prompt_tokens=0,
                completion_tokens=0,
                errors=0,
                estimated_cost_usd=0.0,
            ),
        )
        entry.calls += 1
        entry.prompt_tokens += r["prompt_tokens"] or 0
        entry.completion_tokens += r["completion_tokens"] or 0
        entry.errors += 1 if r["status"] != "ok" else 0
        entry.estimated_cost_usd = round(entry.estimated_cost_usd + call_cost, 4)

        model_key = r["model"] or "—"
        m_entry = by_model.setdefault(
            model_key,
            UsageBreakdown(
                key=model_key,
                label=model_key,
                calls=0,
                prompt_tokens=0,
                completion_tokens=0,
                errors=0,
                estimated_cost_usd=0.0,
            ),
        )
        m_entry.calls += 1
        m_entry.prompt_tokens += r["prompt_tokens"] or 0
        m_entry.completion_tokens += r["completion_tokens"] or 0
        m_entry.errors += 1 if r["status"] != "ok" else 0
        m_entry.estimated_cost_usd = round(m_entry.estimated_cost_usd + call_cost, 4)

        actor_key = r["actor_public_id"] or "—"
        a_entry = by_actor.setdefault(
            actor_key,
            UsageActor(
                public_id=r["actor_public_id"], calls=0, total_tokens=0, estimated_cost_usd=0.0
            ),
        )
        a_entry.calls += 1
        a_entry.total_tokens += (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)
        a_entry.estimated_cost_usd = round(a_entry.estimated_cost_usd + call_cost, 4)

    labels = {a["public_id"]: a.get("display_name") for a in platform_db.list_users()}
    for actor in by_actor.values():
        if actor.public_id:
            actor.display_name = labels.get(actor.public_id)

    recent = [
        UsageCall(
            id=r["id"],
            created_at=r["created_at"],
            actor_public_id=r["actor_public_id"],
            label=r["label"],
            label_human=label_human(r["label"]),
            entrypoint=r["entrypoint"],
            model=r["model"],
            prompt_tokens=r["prompt_tokens"],
            completion_tokens=r["completion_tokens"],
            total_duration_ms=r["total_duration_ms"],
            status=r["status"],
            error_type=r["error_type"],
            tools_count=r["tools_count"] or 0,
        )
        for r in rows[:50]
    ]

    return AdminOllamaUsage(
        days=days,
        since=since,
        totals=UsageTotals(
            calls=len(rows),
            prompt_tokens=prompt_tokens,
            cached_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            errors=errors,
            avg_duration_ms=round(sum(durations) / len(durations), 1) if durations else None,
            estimated_cost_usd=round(total_cost, 4),
            price_prompt_per_1k=_flat_prices()[0],
            price_cached_per_1k=_flat_prices()[1],
            price_completion_per_1k=_flat_prices()[2],
        ),
        by_label=sorted(by_label.values(), key=lambda x: x.calls, reverse=True),
        by_model=sorted(by_model.values(), key=lambda x: x.calls, reverse=True),
        by_actor=sorted(by_actor.values(), key=lambda x: x.calls, reverse=True),
        recent=recent,
    )


# ---------------------------------------------------------------------------
# Vue 2 — « console » Ollama Cloud reconstituée
# ---------------------------------------------------------------------------
#
# Ollama applique **deux fenêtres de limite** (doc ollama.com/cloud) :
#   - une **session** de 5 h (reset toutes les 5 h) ;
#   - une **hebdomadaire** de 7 j (reset tous les 7 j).
# Les dates d'ancre ne sont pas documentées. On reprend l'ancre mesurée par la
# communauté (github.com/Momar8989/ollama-usage) : blocs de 5 h et fenêtre de
# 7 j ancrés à epoch 2026-08-12 11:55 UTC, minute :55. **Approximation non
# officielle** — suffisante pour situer la consommation et anticiper un forfait.

# Ancre commune des deux fenêtres (measurement communautaire, non documentée).
_WINDOW_EPOCH = dt.datetime(2026, 8, 12, 11, 55, tzinfo=dt.UTC)
_SESSION_BLOCK = dt.timedelta(hours=5)
_WEEK_BLOCK = dt.timedelta(days=7)
# Nombre de blocs de session modélisés dans la projection intra-session.
_SESSION_IDX = 1


class WindowUsage(BaseModel):
    """Usage et projection sur une fenêtre de reset (session 5 h ou hebdo 7 j)."""

    label: str
    window_start: str
    window_end: str
    seconds_until_reset: int
    units_used: float
    requests: int
    quota_units: float
    usage_pct: float
    projected_pct_at_reset: float | None = None


class CloudModelUsage(BaseModel):
    model: str
    requests: int
    weight: float
    units: float
    prompt_tokens: int
    completion_tokens: int


class AdminOllamaCloud(BaseModel):
    period: str
    period_start: str
    period_end: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    alert_pct: float
    recommendation: str
    session: WindowUsage
    weekly: WindowUsage
    models: list[CloudModelUsage]


def _window_bounds(now: dt.datetime, block: dt.timedelta) -> tuple[dt.datetime, dt.datetime]:
    """Bornes de la fenêtre ancrée courante : ``[start, end)`` contenant ``now``."""
    elapsed = now - _WINDOW_EPOCH
    index = int(elapsed / block) if elapsed >= dt.timedelta(0) else -1
    start = _WINDOW_EPOCH + block * index
    return start, start + block


def _window_usage(
    label: str,
    now: dt.datetime,
    block: dt.timedelta,
    quota: float,
    weights: dict[str, float],
) -> WindowUsage:
    """Agrège l'usage des appels dans la fenêtre ancrée + projette la fin."""
    start, end = _window_bounds(now, block)
    rows = platform_db.fetch_llm_calls(since=start.isoformat(), until=end.isoformat())
    units = round(sum(_model_weight(r["model"], weights) for r in rows), 2)
    pct = round(units / quota * 100, 1) if quota > 0 else 0.0

    projected: float | None = None
    elapsed_s = (now - start).total_seconds()
    remaining_s = max(0.0, (end - now).total_seconds())
    if rows and quota > 0 and elapsed_s > 0:
        rate = units / elapsed_s  # unités / seconde
        projected = round((units + rate * remaining_s) / quota * 100, 1)

    return WindowUsage(
        label=label,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        seconds_until_reset=int(remaining_s),
        units_used=units,
        requests=len(rows),
        quota_units=quota,
        usage_pct=pct,
        projected_pct_at_reset=projected,
    )


@router.get("/ollama-cloud", response_model=AdminOllamaCloud)
def get_ollama_cloud(
    period: str = Query(default="month", pattern="^(week|month)$"),
) -> AdminOllamaCloud:
    """Reconstitution du panneau d'usage Ollama Cloud (requêtes + quota estimé).

    Deux fenêtres (session 5 h + hebdo 7 j), quotas **pondérés** (unités =
    Σ requêtes × poids du modèle) et calibrés par l'admin : aucune API Ollama
    n'expose le vrai % de quota, et les bornes des fenêtres sont des
    approximations (ancre communautaire, non documentée).
    """
    now = dt.datetime.now(dt.UTC)
    start = now - dt.timedelta(days=7 if period == "week" else 30)
    rows = platform_db.fetch_llm_calls(since=start.isoformat())

    weights = _weights()
    quota = _float_setting(_WEEKLY_QUOTA_KEY, 0.0)
    alert_pct = _float_setting(_ALERT_PCT_KEY, _DEFAULT_ALERT_PCT)
    # La limite « session » partage l'échelle du quota hebdo mais couvre 5 h :
    # à défaut de valeur propre, on la dérive (quota hebdo réparti sur les
    # ~33 blocs de 5 h d'une semaine).
    session_quota = _float_setting("llm_session_quota_units", quota / 33 if quota > 0 else 0.0)

    models: dict[str, CloudModelUsage] = {}
    for r in rows:
        model = r["model"] or "—"
        entry = models.setdefault(
            model,
            CloudModelUsage(
                model=model,
                requests=0,
                weight=_model_weight(r["model"], weights),
                units=0.0,
                prompt_tokens=0,
                completion_tokens=0,
            ),
        )
        entry.requests += 1
        entry.prompt_tokens += r["prompt_tokens"] or 0
        entry.completion_tokens += r["completion_tokens"] or 0
        entry.units = round(entry.units + entry.weight, 2)

    session = _window_usage("session", now, _SESSION_BLOCK, session_quota, weights)
    weekly = _window_usage("week", now, _WEEK_BLOCK, quota, weights)

    # Le % contraignant est le max des deux fenêtres.
    binding = max(
        (w for w in (session, weekly) if w.quota_units > 0),
        key=lambda w: w.usage_pct,
        default=None,
    )
    if quota <= 0 or binding is None:
        recommendation = "Quota non configuré — définir le quota hebdo dans les réglages."
    elif binding.usage_pct >= 100:
        recommendation = (
            f"Limite « {binding.label} » dépassée — passage à un forfait supérieur recommandé."
        )
    elif binding.projected_pct_at_reset is not None and binding.projected_pct_at_reset >= 100:
        recommendation = f"Épuisement projeté ({binding.label}) — envisager Pro."
    elif binding.usage_pct >= alert_pct:
        recommendation = f"Proche du quota ({binding.label}) — surveiller la consommation."
    else:
        recommendation = "Consommation sous contrôle (forfait Free suffisant)."

    return AdminOllamaCloud(
        period=period,
        period_start=start.isoformat(),
        period_end=now.isoformat(),
        requests=len(rows),
        prompt_tokens=sum(r["prompt_tokens"] or 0 for r in rows),
        completion_tokens=sum(r["completion_tokens"] or 0 for r in rows),
        alert_pct=alert_pct,
        recommendation=recommendation,
        session=session,
        weekly=weekly,
        models=sorted(models.values(), key=lambda x: x.requests, reverse=True),
    )


__all__ = ["router"]
