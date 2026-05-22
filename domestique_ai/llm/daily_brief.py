"""
Briefing quotidien du coach — palier 1 de la proactivité.

Agrège l'état du jour (TSB, alerte saillante, séance suggérée) et génère une
phrase de synthèse courte via LLM. Le LLM ne fait que reformuler des données
structurées déjà calculées — aucune valeur chiffrée ne peut être inventée.

Cache en mémoire (clé ``date + tsb_arrondi + hash_alertes``) avec TTL qui
expire au changement de jour : un seul appel Ollama par jour et par état,
peu importe combien de fois le Dashboard est ouvert.

Fallback déterministe si Ollama injoignable ou JSON mal formé : on
construit une phrase template à partir des signaux structurés.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import threading
from typing import Any

from domestique_ai.llm.ollama_client import chat_structured_sync
from domestique_ai.processing.morning_metrics import detect_morning_alerts
from domestique_ai.processing.overtraining import detect_overtraining_signals
from domestique_ai.processing.today import propose_workout_today

log = logging.getLogger(__name__)

_BRIEF_CACHE: dict[tuple[str, int, str], dict[str, Any]] = {}
_BRIEF_LOCK = threading.Lock()

_LLM_SYSTEM_PROMPT = (
    "Tu es un coach d'endurance francophone. Tu dois résumer l'état du jour "
    "d'un cycliste en une phrase concise et concrète (max 25 mots, 1 phrase). "
    "Tu DOIS répondre en JSON strict avec une clé `summary` (string).\n\n"
    "Règles :\n"
    "- N'invente AUCUN chiffre — ne cite que ceux fournis dans le dossier.\n"
    "- Cite le TSB et la zone d'état seulement quand pertinent.\n"
    "- Mentionne la séance suggérée si elle existe (type + durée).\n"
    "- Si une alerte critique est présente, prends-la en compte.\n"
    "- Pas d'emoji, pas de ponctuation décorative.\n"
    "- Ton : direct, factuel, pas de blabla motivationnel."
)


def _round_tsb(tsb: float) -> int:
    """Bucket TSB pour clé de cache (pas de 5 points → 4 buckets autour de 0)."""
    return int(round(float(tsb) / 5.0))


def _hash_alerts(alerts: list[str]) -> str:
    """Hash stable du set d'alertes — change quand un signal apparaît/disparaît."""
    payload = "\n".join(sorted(alerts))
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]


def _select_primary_alert(
    overtraining: dict[str, Any],
    morning: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Sélectionne l'alerte la plus saillante : critique > warning, OT > morning.

    On préfère un signal d'overtraining objectif (TSB chronique, strain, saut
    volume) à une dérive matinale auto-déclarée, parce que la fiabilité des
    métriques objectives est plus haute.
    """
    ot_alerts = overtraining.get("alerts") or []
    critical_indicators = {"tsb_chronic", "strain"}
    for alert in ot_alerts:
        if alert.get("indicator") in critical_indicators:
            return {
                "type": alert.get("indicator", "overtraining"),
                "severity": "danger",
                "message": alert.get("message", ""),
            }
    if ot_alerts:
        first = ot_alerts[0]
        return {
            "type": first.get("indicator", "overtraining"),
            "severity": "warning",
            "message": first.get("message", ""),
        }
    morning_critical = [m for m in morning if m.get("severity") == "critical"]
    pool = morning_critical or morning
    if pool:
        first = pool[0]
        delta = float(first.get("delta_pct", 0.0))
        arrow = "↓" if delta < 0 else "↑"
        return {
            "type": f"morning_{first.get('metric')}",
            "severity": "danger" if first.get("severity") == "critical" else "warning",
            "message": (
                f"{first.get('metric')} {arrow} {delta:+.1f}% vs baseline "
                f"({first.get('latest', 0):.1f} le {first.get('latest_date')})"
            ),
        }
    return None


def _collect_signals(today: _dt.date) -> dict[str, Any]:
    """Construit le dossier de signaux : TSB, séance, alerte. Best-effort, ne lève pas."""
    workout = propose_workout_today(today=today)
    try:
        overtraining = detect_overtraining_signals()
    except Exception:  # noqa: BLE001
        log.debug("Échec overtraining", exc_info=True)
        overtraining = {"alerts": []}
    try:
        morning = detect_morning_alerts()
    except Exception:  # noqa: BLE001
        log.debug("Échec morning", exc_info=True)
        morning = []

    primary_alert = _select_primary_alert(overtraining, morning)

    tsb = workout.get("tsb")
    tsb_zone = workout.get("tsb_zone")
    # Quand jour off, propose_workout_today n'expose pas tsb. On le recalcule
    # à partir des signaux disponibles si possible.
    if tsb is None:
        from domestique_ai.processing.analyzer import (
            calculate_ctl_atl_tsb,
            fetch_activities_from_db,
        )
        curves = calculate_ctl_atl_tsb(fetch_activities_from_db(), end_date=today)
        if curves:
            tsb = float(curves[-1]["TSB"])
            from domestique_ai.processing.today import _tsb_zone_label
            tsb_zone = _tsb_zone_label(tsb)

    return {
        "today": today.isoformat(),
        "tsb": round(float(tsb), 1) if tsb is not None else None,
        "tsb_zone": tsb_zone,
        "primary_alert": primary_alert,
        "workout": workout,
    }


def _build_fallback_summary(signals: dict[str, Any]) -> str:
    """Phrase template — utilisée si Ollama est injoignable ou produit du JSON cassé."""
    tsb = signals.get("tsb")
    zone = signals.get("tsb_zone") or "—"
    workout = signals.get("workout") or {}
    alert = signals.get("primary_alert")

    if workout.get("rest_day"):
        suggestion = "repos prévu"
    else:
        w = workout.get("workout") or {}
        kind = w.get("kind", "séance")
        duration = w.get("duration_min")
        suggestion = f"{kind} {duration} min" if duration else kind

    parts: list[str] = []
    if tsb is not None:
        parts.append(f"TSB {tsb:+.1f} ({zone})")
    parts.append(suggestion)
    base = " — ".join(parts)
    if alert:
        base += f". Alerte : {alert.get('message', '')}"
    return base


def _generate_summary_with_llm(signals: dict[str, Any]) -> str | None:
    """Demande au LLM une phrase courte. Retourne ``None`` si échec."""
    payload = json.dumps(signals, ensure_ascii=False, default=str)
    messages = [
        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Voici les signaux du jour. Renvoie UNIQUEMENT le JSON "
                "{\"summary\": \"...\"} avec une phrase de synthèse.\n\n" + payload
            ),
        },
    ]
    response = chat_structured_sync(messages, timeout_s=15.0)
    if not response:
        return None
    summary = response.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    return summary.strip()


def _workout_to_brief(workout: dict[str, Any]) -> dict[str, Any]:
    """Extrait le sous-ensemble pertinent de la séance pour le brief.

    Inclut la structure détaillée (steps + zone cible + TSS estimé + notes)
    pour que la `DailyBriefCard` puisse afficher le détail au déroulement,
    sans nouvel appel HTTP.
    """
    if workout.get("rest_day"):
        return {
            "rest_day": True,
            "reason": workout.get("reason"),
            "kind": None,
            "duration_min": None,
            "name": None,
            "target_zone": None,
            "estimated_tss": None,
            "structure": [],
            "notes": None,
        }
    w = workout.get("workout") or {}
    return {
        "rest_day": False,
        "reason": None,
        "kind": w.get("kind"),
        "duration_min": w.get("duration_min"),
        "name": w.get("name"),
        "target_zone": w.get("target_zone"),
        "estimated_tss": w.get("estimated_tss"),
        "structure": list(w.get("structure") or []),
        "notes": w.get("notes") or None,
    }


def build_daily_brief(
    today: _dt.date | None = None,
    refresh: bool = False,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Construit le brief quotidien.

    Retourne un dict de la forme :

    ``{
        "date": "YYYY-MM-DD",
        "summary": "Phrase de synthèse",
        "tsb": float | None,
        "tsb_zone": str | None,
        "primary_alert": {"type", "severity", "message"} | None,
        "today_workout": {"rest_day", "kind", "duration_min", "name", "reason"},
        "source": "cache" | "llm" | "fallback",
      }``

    Args:
        today : date du jour (override pour tests).
        refresh : si ``True``, bypass le cache.
        use_llm : si ``False``, génère directement la phrase template (utile
            pour tests / mode hors-ligne).
    """
    target = today or _dt.date.today()
    signals = _collect_signals(target)
    alert_signature = (
        [signals["primary_alert"]["message"]]
        if signals.get("primary_alert")
        else []
    )
    cache_key = (
        target.isoformat(),
        _round_tsb(signals.get("tsb") or 0.0),
        _hash_alerts(alert_signature),
    )

    if not refresh:
        with _BRIEF_LOCK:
            cached = _BRIEF_CACHE.get(cache_key)
        if cached is not None:
            return {**cached, "source": "cache"}

    summary: str | None = None
    source = "fallback"
    if use_llm:
        summary = _generate_summary_with_llm(signals)
        if summary:
            source = "llm"
    if summary is None:
        summary = _build_fallback_summary(signals)

    payload = {
        "date": target.isoformat(),
        "summary": summary,
        "tsb": signals.get("tsb"),
        "tsb_zone": signals.get("tsb_zone"),
        "primary_alert": signals.get("primary_alert"),
        "today_workout": _workout_to_brief(signals.get("workout") or {}),
        "source": source,
    }
    with _BRIEF_LOCK:
        # On purge le cache des jours antérieurs : on n'y revient jamais et ça
        # évite de grossir indéfiniment.
        for key in list(_BRIEF_CACHE):
            if key[0] != target.isoformat():
                del _BRIEF_CACHE[key]
        _BRIEF_CACHE[cache_key] = payload
    return payload


def build_coach_context(today: _dt.date | None = None) -> str:
    """Bloc texte injecté au démarrage d'une nouvelle session coach (palier 2).

    Format pensé pour être ajouté en message ``system`` après le SYSTEM_PROMPT
    principal : il fournit au LLM l'état courant sans qu'il ait à appeler
    immédiatement les tools sur la 1re question banale.

    Précise explicitement que ces chiffres sont les chiffres autorisés à
    citer, et que pour creuser le coach peut toujours appeler les tools.
    """
    target = today or _dt.date.today()
    brief = build_daily_brief(today=target)
    lines = [
        "Contexte courant injecté par l'app (pas besoin d'appeler les tools "
        "pour ces chiffres — ils sont issus du même calcul que tes tools) :",
        f"- Date : {brief['date']}",
    ]
    if brief.get("tsb") is not None:
        lines.append(
            f"- TSB : {brief['tsb']:+.1f} ({brief.get('tsb_zone') or '—'})"
        )
    workout = brief.get("today_workout") or {}
    if workout.get("rest_day"):
        lines.append(f"- Séance du jour : repos ({workout.get('reason') or 'jour off'})")
    elif workout.get("kind"):
        duration = workout.get("duration_min")
        lines.append(
            f"- Séance suggérée : {workout['kind']}"
            + (f" {duration} min" if duration else "")
        )
    alert = brief.get("primary_alert")
    if alert:
        lines.append(f"- Alerte saillante : {alert.get('message', '')}")
    else:
        lines.append("- Alerte saillante : aucune")
    lines.append(
        "Pour creuser (CTL, ATL, zones, dernière activité, plan, etc.), "
        "appelle les tools comme d'habitude."
    )
    return "\n".join(lines)


def clear_cache() -> None:
    """Vide le cache en mémoire (utile pour les tests)."""
    with _BRIEF_LOCK:
        _BRIEF_CACHE.clear()


__all__ = ["build_coach_context", "build_daily_brief", "clear_cache"]
