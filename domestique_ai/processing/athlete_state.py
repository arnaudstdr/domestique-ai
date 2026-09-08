"""État physiologique consolidé de l'athlète — la source de faits du coach.

Ce module centralise la lecture de l'état réel (charge, tendance, fatigue,
récupération, profil) pour que le générateur de plan, la revue hebdomadaire et
le check du matin raisonnent tous sur **les mêmes faits**, sans inventer de
règle arbitraire nulle part ailleurs.

Fonctions pures (testables sans base ni réseau) :

- ``summarize_load_state`` : CTL / ATL / TSB courants + trajectoire CTL + TSB
  chronique, à partir de la grille ``calculate_ctl_atl_tsb``.
- ``is_deconditioned`` : règle composite « reprise » (CTL bas / CTL en
  décroissance / fatigue chronique).
- ``ramp_weeks_for_level`` / ``intensity_ceiling`` : plafond d'intensité gradué
  sur les premières semaines d'une reprise (base → tempo → normal), calibré par
  le niveau de l'athlète.

Et un agrégateur best-effort (lit la DB / YAML, ne lève jamais) :

- ``build_coach_state`` : dict unique combinant charge, profil, objectif,
  récupération, compliance et disponibilités.
- ``format_state_block`` : le même état rendu en bloc texte pour le prompt LLM.
"""

from __future__ import annotations

from typing import Any

# Plafond d'intensité par semaine pendant une reprise.
CEILING_BASE = "base"  # Z1-Z2 seulement (aucun tempo, aucun intervalle)
CEILING_TEMPO = "tempo"  # tempo/sweetspot court autorisé, jamais de Z4-Z5
CEILING_FULL = "full"  # cadence normale du type d'objectif

# Nombre de semaines de reprise (fondation) avant réintroduction complète de
# l'intensité, selon le niveau. Semaine 0 = base stricte, semaines intermédiaires
# = tempo, puis cadence normale.
_LEVEL_RAMP_WEEKS: dict[str, int] = {
    "beginner": 3,
    "intermediate": 2,
    "ex_competitor": 2,
    "advanced": 1,
}

# Seuil de TSB chronique (moyenne 7 j) sous lequel on est en fatigue installée —
# aligné sur ``overtraining.TSB_CHRONIC_THRESHOLD``.
_TSB_FATIGUE = -20.0


def summarize_load_state(curves: list[dict[str, Any]]) -> dict[str, Any]:
    """Résume la grille CTL/ATL/TSB en un état lisible par le coach.

    Retourne ``{available, ctl, atl, tsb, ctl_trend, chronic_tsb}`` où
    ``ctl_trend`` ∈ {"rising", "falling", "flat"} (moyenne CTL 7 j vs 14 j) et
    ``chronic_tsb`` la moyenne du TSB sur les 7 derniers jours (None si
    historique trop court).
    """
    if not curves:
        return {
            "available": False,
            "ctl": None,
            "atl": None,
            "tsb": None,
            "ctl_trend": "flat",
            "chronic_tsb": None,
        }

    last = curves[-1]
    ctl = float(last.get("CTL", 0.0))
    atl = float(last.get("ATL", 0.0))
    tsb = float(last.get("TSB", 0.0))

    ctls = [float(c.get("CTL", 0.0)) for c in curves]
    week7 = ctls[-7:]
    week14 = ctls[-14:] if len(ctls) >= 14 else ctls
    avg7 = sum(week7) / len(week7)
    avg14 = sum(week14) / len(week14)
    # Marge de 2 % pour ne pas labelliser un bruit de EMA en tendance.
    if avg7 > avg14 * 1.02:
        trend = "rising"
    elif avg7 < avg14 * 0.98:
        trend = "falling"
    else:
        trend = "flat"

    tsb_window = curves[-7:]
    chronic_tsb = round(sum(float(c.get("TSB", 0.0)) for c in tsb_window) / len(tsb_window), 1)

    return {
        "available": True,
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(tsb, 1),
        "ctl_trend": trend,
        "chronic_tsb": chronic_tsb,
    }


def is_deconditioned(
    ctl_current: float,
    *,
    ctl_trend: str | None = None,
    chronic_tsb: float | None = None,
    threshold: float = 20.0,
) -> bool:
    """Règle composite « reprise » : l'athlète est déconditionné si au moins un
    de ces faits est vrai — CTL sous le plancher de forme, CTL en décroissance
    (sortie de coupure), ou fatigue chronique (TSB 7 j très négatif).
    """
    if ctl_current < threshold:
        return True
    if ctl_trend == "falling":
        return True
    return chronic_tsb is not None and chronic_tsb <= _TSB_FATIGUE


def ramp_weeks_for_level(level: str | None) -> int:
    """Nombre de semaines de reprise avant réintroduction pleine de l'intensité."""
    return _LEVEL_RAMP_WEEKS.get(level or "intermediate", 2)


def intensity_ceiling(
    week_idx: int,
    *,
    ctl_current: float,
    level: str | None,
    ctl_trend: str | None = None,
    chronic_tsb: float | None = None,
    threshold: float = 20.0,
) -> str:
    """Plafond d'intensité d'une semaine de plan (base / tempo / full).

    Hors reprise → ``full`` (le type d'objectif gère la cadence). En reprise,
    on grimpe progressivement : semaine 0 = base stricte, semaines de rampe
    suivantes = tempo, puis retour au rythme normal une fois la rampe passée.
    """
    if not is_deconditioned(
        ctl_current, ctl_trend=ctl_trend, chronic_tsb=chronic_tsb, threshold=threshold
    ):
        return CEILING_FULL

    ramp = max(1, ramp_weeks_for_level(level))
    if week_idx >= ramp:
        return CEILING_FULL
    if week_idx == 0:
        return CEILING_BASE
    return CEILING_TEMPO


def _fmt(x: Any, *, nd: int = 1) -> str:
    if x is None:
        return "—"
    if isinstance(x, (int, float)):
        return f"{float(x):.{nd}f}".rstrip("0").rstrip(".") if nd else str(x)
    return str(x)


_TREND_LABELS = {"rising": "en hausse", "falling": "en baisse", "flat": "stable"}
_LEVEL_LABELS = {
    "beginner": "débutant",
    "intermediate": "intermédiaire",
    "advanced": "avancé",
    "ex_competitor": "ancien compétiteur qui reprend",
}


def format_state_block(state: dict[str, Any]) -> str:
    """Rend l'état réel en bloc texte factuel pour le prompt du coach LLM.

    Ne contient QUE des faits calculés : le LLM raisonne dessus, il n'invente
    aucun chiffre.
    """
    load = state.get("load") or {}
    profile = state.get("profile") or {}
    objective = state.get("objective") or {}
    morning = state.get("morning") or {}
    compliance = state.get("compliance") or {}

    lines: list[str] = ["État réel de l'athlète (données calculées, fiables) :"]

    level = profile.get("level")
    level_label = _LEVEL_LABELS.get(level or "", level or "—")
    lines.append(
        f"- Profil : niveau {level_label}"
        + (f", FTP {_fmt(profile.get('ftp'), nd=0)} W" if profile.get("ftp") else "")
    )

    if load.get("available"):
        trend = _TREND_LABELS.get(load.get("ctl_trend", "flat"), "stable")
        lines.append(
            f"- Charge : CTL {_fmt(load.get('ctl'))} · ATL {_fmt(load.get('atl'))} · "
            f"TSB {_fmt(load.get('tsb'))} ; CTL {trend} "
            f"(TSB chronique 7 j {_fmt(load.get('chronic_tsb'))})."
        )
    else:
        lines.append("- Charge : aucune activité enregistrée (état inconnu, prudence).")

    reprise = state.get("reprise")
    if reprise is None:
        reprise = is_deconditioned(
            float(load.get("ctl") or 0.0),
            ctl_trend=load.get("ctl_trend"),
            chronic_tsb=load.get("chronic_tsb"),
        )
    if reprise:
        lines.append(
            "- Lecture : état déconditionné (reprise) — la forme doit se "
            "reconstruire par le volume avant de re-stimuler l'intensité."
        )

    if morning:
        parts: list[str] = []
        if morning.get("readiness_median") is not None:
            parts.append(f"readiness {_fmt(morning['readiness_median'], nd=0)}/100")
        if morning.get("sleep_median") is not None:
            parts.append(f"sommeil {_fmt(morning['sleep_median'])} h")
        if morning.get("hrv_delta_pct") is not None:
            parts.append(f"dérive HRV {_fmt(morning['hrv_delta_pct'])} %")
        if parts:
            lines.append("- Récupération : " + " · ".join(parts) + ".")

    if compliance:
        lines.append(
            "- Semaine écoulée : "
            f"{compliance.get('done', 0)} faite(s), {compliance.get('partial', 0)} "
            f"partielle(s), {compliance.get('missed', 0)} manquée(s), "
            f"{compliance.get('skipped_by_decision', 0)} repos coach ; "
            f"TSS planifié {_fmt(compliance.get('planned_tss'), nd=0)} vs réalisé "
            f"{_fmt(compliance.get('realized_tss'), nd=0)}."
        )

    if objective.get("type"):
        obj_line = f"- Objectif : {objective['type']}"
        if objective.get("date"):
            obj_line += f" (le {objective['date']})"
        if objective.get("distance_km"):
            obj_line += f", {_fmt(objective['distance_km'], nd=0)} km"
        if objective.get("elevation_m"):
            obj_line += f" / {objective['elevation_m']} m D+"
        lines.append(obj_line + ".")

    return "\n".join(lines)


def build_coach_state(
    *,
    ctx: Any,
    today: Any,
    activities: list[dict[str, Any]] | None = None,
    availability: Any = None,
    compliance: dict[str, Any] | None = None,
    threshold: float = 20.0,
) -> dict[str, Any]:
    """Agrège l'état réel complet (best-effort, ne lève jamais).

    Combine la charge (CTL/ATL/TSB + tendance), le profil (dont le niveau),
    l'objectif, la récupération (matin), la compliance et les disponibilités.
    ``activities``/``availability``/``compliance`` peuvent être passés par
    l'appelant pour éviter de recharger (sinon on les calcule ici).
    """
    from domestique_ai.processing.analyzer import calculate_ctl_atl_tsb

    state: dict[str, Any] = {"today": getattr(today, "isoformat", lambda: str(today))()}

    try:
        curves = calculate_ctl_atl_tsb(activities or [], end_date=today)
    except Exception:  # noqa: BLE001
        curves = []
    load = summarize_load_state(curves)
    state["load"] = load

    state["profile"] = {
        "level": getattr(ctx, "level", None),
        "ftp": getattr(ctx, "ftp", None),
        "hr_rest": getattr(ctx, "hr_rest", None),
        "hr_max": getattr(ctx, "hr_max", None),
        "sex": getattr(ctx, "sex", None),
    }

    try:
        from domestique_ai.llm.objectives import load_objective

        objective = load_objective(ctx.objective_path)
        state["objective"] = objective.to_dict() if objective is not None else {}
    except Exception:  # noqa: BLE001
        state["objective"] = {}

    try:
        from domestique_ai.processing.morning_metrics import (
            compute_baselines,
            fetch_morning_history,
        )

        history = fetch_morning_history(days=14, db_path=ctx.db_path)
        last_week = [e for e in history if e["date"] >= (today - _days(7)).isoformat()]
        readiness_vals = [e.get("readiness_score") for e in last_week if e.get("readiness_score")]
        sleep_vals = [e.get("sleep_hours") for e in last_week if e.get("sleep_hours")]
        state["morning"] = {
            "readiness_median": _median(readiness_vals),
            "sleep_median": _median(sleep_vals),
            "hrv_delta_pct": (
                round(compute_baselines("hrv_ms", db_path=ctx.db_path).get("delta_pct"), 1)
                if compute_baselines("hrv_ms", db_path=ctx.db_path).get("available")
                else None
            ),
        }
    except Exception:  # noqa: BLE001
        state["morning"] = {}

    state["compliance"] = compliance or {}

    if availability is None:
        try:
            from domestique_ai.llm.availability import load_availability

            availability = load_availability(ctx.availability_path)
        except Exception:  # noqa: BLE001
            availability = None
    state["availability"] = availability

    state["reprise"] = is_deconditioned(
        float(load.get("ctl") or 0.0),
        ctl_trend=load.get("ctl_trend"),
        chronic_tsb=load.get("chronic_tsb"),
        threshold=threshold,
    )
    return state


def _days(n: int) -> Any:
    import datetime as _dt

    return _dt.timedelta(days=n)


def _median(values: list[float]) -> float | None:
    import statistics

    clean = [v for v in values if v is not None]
    return round(statistics.median(clean), 1) if clean else None


__all__ = [
    "CEILING_BASE",
    "CEILING_FULL",
    "CEILING_TEMPO",
    "build_coach_state",
    "format_state_block",
    "intensity_ceiling",
    "is_deconditioned",
    "ramp_weeks_for_level",
    "summarize_load_state",
]
