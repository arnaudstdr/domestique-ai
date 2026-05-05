"""
Tools exposés au coach LLM via tool calling Ollama.

Chaque tool est composé de deux choses :
- un schéma JSON conforme au format OpenAI/Ollama (description + paramètres),
- une fonction Python pure qui prend les arguments désérialisés et renvoie
  un dict JSON-sérialisable.

Le LLM ne reçoit que des données déjà calculées par notre code Python — il ne
peut pas inventer des chiffres (CTL, TSB, zones), seulement les commenter.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from domestique_ai.processing.analyzer import (
    HR_ZONE_KEYS,
    calculate_ctl_atl_tsb,
    fetch_activities_from_db,
)


def _tsb_zone_label(tsb: float) -> str:
    """Mêmes seuils que dashboard._tsb_zone_label, sans emoji."""
    if tsb > 5:
        return "Frais"
    if tsb >= -10:
        return "Optimal"
    if tsb >= -20:
        return "Fatigué"
    return "Surentraîné"


def _filter_recent(activities: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    if not activities or days <= 0:
        return []
    last_date = activities[-1].get("date") or ""
    if not last_date:
        return []
    import datetime as dt
    end = dt.datetime.fromisoformat(last_date.replace("Z", "+00:00"))
    start = end - dt.timedelta(days=days)
    out = []
    for act in activities:
        d = act.get("date")
        if not d:
            continue
        when = dt.datetime.fromisoformat(d.replace("Z", "+00:00"))
        if when >= start:
            out.append(act)
    return out


def get_training_load_state() -> dict[str, Any]:
    """État courant CTL/ATL/TSB + zone interprétative (Frais/Optimal/Fatigué/Surentraîné)."""
    activities = fetch_activities_from_db()
    curves = calculate_ctl_atl_tsb(activities)
    if not curves:
        return {"available": False, "reason": "Aucune activité en base."}
    last = curves[-1]
    return {
        "available": True,
        "date": last["date"],
        "ctl": last["CTL"],
        "atl": last["ATL"],
        "tsb": last["TSB"],
        "zone": _tsb_zone_label(last["TSB"]),
        "interpretation": {
            "ctl": "Forme à long terme (charge moyenne 42 jours).",
            "atl": "Fatigue récente (charge moyenne 7 jours).",
            "tsb": "Fraîcheur (CTL - ATL). Positif = frais, négatif = fatigué.",
        },
    }


def get_recent_activities(days: int = 7) -> dict[str, Any]:
    """Liste des activités sur les N derniers jours (jusqu'à la dernière en base)."""
    activities = fetch_activities_from_db()
    recent = _filter_recent(activities, days)
    out = []
    for act in recent:
        out.append({
            "date": act.get("date"),
            "duration_sec": act.get("duration"),
            "distance_km": round((act.get("distance") or 0) / 1000, 2),
            "elevation_m": act.get("elevation_gain"),
            "avg_heart_rate": act.get("avg_heart_rate"),
            "max_heart_rate": act.get("max_heart_rate"),
            "avg_power": act.get("avg_power"),
            "training_load": act.get("training_load"),
            "hr_zones_sec": {
                key: act.get(f"hr_{key}_time")
                for key in HR_ZONE_KEYS
            },
        })
    return {"days": days, "count": len(out), "activities": out}


def get_zone_distribution(days: int = 14) -> dict[str, Any]:
    """Répartition cumulée du temps par zone HR sur les N derniers jours."""
    activities = fetch_activities_from_db()
    recent = _filter_recent(activities, days)
    totals: dict[str, float] = dict.fromkeys(HR_ZONE_KEYS, 0.0)
    counted = 0
    for act in recent:
        zones = {key: act.get(f"hr_{key}_time") for key in HR_ZONE_KEYS}
        if any(v is None for v in zones.values()):
            continue
        for key, value in zones.items():
            totals[key] += value or 0.0
        counted += 1
    total = sum(totals.values())
    distribution = {
        key: {
            "seconds": round(value, 1),
            "minutes": round(value / 60, 1),
            "share_pct": round((value / total * 100) if total else 0.0, 1),
        }
        for key, value in totals.items()
    }
    return {
        "days": days,
        "activities_with_zones": counted,
        "activities_total_in_window": len(recent),
        "total_seconds": round(total, 1),
        "distribution": distribution,
    }


def get_objective() -> dict[str, Any]:
    """Objectif d'entraînement courant (ou indication s'il est absent)."""
    from domestique_ai.llm.objectives import load_objective
    obj = load_objective()
    if obj is None:
        return {
            "available": False,
            "reason": "Aucun fichier data/objective.yaml. "
                      "Copier data/objective.yaml.example pour en créer un.",
        }
    return {"available": True, "objective": obj.to_dict()}


def get_activity_details(strava_id: int) -> dict[str, Any]:
    """Détail complet d'une activité identifiée par strava_id."""
    import sqlite3

    from domestique_ai.config import get_db_path
    from domestique_ai.ingestion.strava import init_db
    init_db()
    conn = sqlite3.connect(get_db_path())
    try:
        cursor = conn.execute(
            "SELECT strava_id, date, duration, avg_heart_rate, max_heart_rate, "
            "avg_power, elevation_gain, distance, training_load, "
            "hr_z1_time, hr_z2_time, hr_z3_time, hr_z4_time, hr_z5_time "
            "FROM activities WHERE strava_id = ?",
            (strava_id,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        return {"available": False, "strava_id": strava_id}
    return {
        "available": True,
        "strava_id": row[0],
        "date": row[1],
        "duration_sec": row[2],
        "avg_heart_rate": row[3],
        "max_heart_rate": row[4],
        "avg_power": row[5],
        "elevation_m": row[6],
        "distance_km": round((row[7] or 0) / 1000, 2),
        "training_load": row[8],
        "hr_zones_sec": {
            HR_ZONE_KEYS[i]: row[9 + i] for i in range(5)
        },
    }


_WORKOUT_TEMPLATES = {
    "recovery": {
        "kind": "recovery",
        "structure": [
            {"phase": "ride", "zone": "z1", "fraction": 1.0},
        ],
        "rationale": "Sortie de récupération active : Z1 strict, cadence libre, "
                     "pas de bosse. Favorise la circulation sans charger.",
    },
    "endurance": {
        "kind": "endurance",
        "structure": [
            {"phase": "warmup", "zone": "z1", "fraction": 0.10},
            {"phase": "ride", "zone": "z2", "fraction": 0.80},
            {"phase": "cooldown", "zone": "z1", "fraction": 0.10},
        ],
        "rationale": "Foncier : long en Z2, base aérobie, capillarisation. "
                     "Tu peux ajouter quelques relances courtes hors compteur.",
    },
    "tempo": {
        "kind": "tempo",
        "structure": [
            {"phase": "warmup", "zone": "z1", "fraction": 0.15},
            {"phase": "tempo", "zone": "z3", "fraction": 0.65},
            {"phase": "cooldown", "zone": "z1", "fraction": 0.20},
        ],
        "rationale": "Tempo soutenu : Z3 continu pour résistance aérobie. "
                     "À placer quand TSB > -10.",
    },
    "threshold": {
        "kind": "intervals_threshold",
        "structure": [
            {"phase": "warmup", "zone": "z2", "fraction": 0.20},
            {"phase": "intervals",
             "block": {"work_min": 8, "work_zone": "z4",
                       "rest_min": 4, "rest_zone": "z2"},
             "fraction_total": 0.65},
            {"phase": "cooldown", "zone": "z1", "fraction": 0.15},
        ],
        "rationale": "Seuil : intervalles 8' Z4 / 4' Z2. Ajuster nb de reps "
                     "selon durée totale (60 min ≈ 3 reps, 90 min ≈ 4-5 reps).",
    },
    "vo2max": {
        "kind": "intervals_vo2max",
        "structure": [
            {"phase": "warmup", "zone": "z2", "fraction": 0.25},
            {"phase": "intervals",
             "block": {"work_min": 3, "work_zone": "z5",
                       "rest_min": 3, "rest_zone": "z1"},
             "fraction_total": 0.55},
            {"phase": "cooldown", "zone": "z1", "fraction": 0.20},
        ],
        "rationale": "VO2max : 3' Z5 / 3' Z1. Très exigeant, à placer "
                     "quand TSB > 0 et avec récup ≥ 48 h ensuite.",
    },
}


def _kind_for_target(target_zone: str) -> str:
    return {
        "z1": "recovery",
        "z2": "endurance",
        "z3": "tempo",
        "z4": "threshold",
        "z5": "vo2max",
    }.get(target_zone, "endurance")


def propose_workout(target_zone: str, duration_min: int,
                    kind: str | None = None) -> dict[str, Any]:
    """
    Squelette de séance basé sur la zone cible et la durée.

    target_zone : z1..z5 (zone dominante visée).
    duration_min : durée totale en minutes.
    kind : recovery | endurance | tempo | threshold | vo2max (optionnel,
           déduit de target_zone si absent).
    """
    target_zone = (target_zone or "").lower()
    if target_zone not in HR_ZONE_KEYS:
        return {
            "available": False,
            "reason": f"target_zone invalide: {target_zone!r}. "
                      f"Attendu: {list(HR_ZONE_KEYS)}",
        }
    if duration_min <= 0:
        return {"available": False, "reason": "duration_min doit être positif."}

    selected_kind = kind or _kind_for_target(target_zone)
    template = _WORKOUT_TEMPLATES.get(selected_kind)
    if template is None:
        return {
            "available": False,
            "reason": f"kind inconnu: {selected_kind!r}. "
                      f"Attendu: {sorted(_WORKOUT_TEMPLATES)}",
        }

    return {
        "available": True,
        "target_zone": target_zone,
        "duration_min": duration_min,
        "kind": template["kind"],
        "structure": template["structure"],
        "rationale": template["rationale"],
    }


# ---- Schémas JSON pour le LLM ------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_training_load_state",
            "description": "Renvoie l'état courant CTL/ATL/TSB (forme, fatigue, "
                           "fraîcheur) + zone interprétative.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_activities",
            "description": "Liste les activités sur les N derniers jours avec "
                           "TSS, durée, distance, dénivelé, HR moyenne et zones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Fenêtre glissante en jours (défaut 7).",
                        "minimum": 1, "maximum": 365,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_zone_distribution",
            "description": "Répartition cumulée du temps par zone HR (Z1..Z5) "
                           "sur les N derniers jours, avec part en pourcentage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Fenêtre en jours (défaut 14).",
                        "minimum": 1, "maximum": 365,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_objective",
            "description": "Lit l'objectif d'entraînement courant (type, date, "
                           "distance, dénivelé, notes).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_activity_details",
            "description": "Détail complet d'une activité par son strava_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strava_id": {
                        "type": "integer",
                        "description": "Identifiant Strava de l'activité.",
                    },
                },
                "required": ["strava_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_workout",
            "description": "Génère un squelette de séance (échauffement, corps, "
                           "retour au calme) selon une zone cible et une durée.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_zone": {
                        "type": "string",
                        "enum": list(HR_ZONE_KEYS),
                        "description": "Zone HR dominante visée.",
                    },
                    "duration_min": {
                        "type": "integer",
                        "description": "Durée totale de la séance en minutes.",
                        "minimum": 15, "maximum": 480,
                    },
                    "kind": {
                        "type": "string",
                        "enum": list(_WORKOUT_TEMPLATES.keys()),
                        "description": "Type de séance (déduit de target_zone "
                                       "si absent).",
                    },
                },
                "required": ["target_zone", "duration_min"],
            },
        },
    },
]


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_training_load_state": get_training_load_state,
    "get_recent_activities": get_recent_activities,
    "get_zone_distribution": get_zone_distribution,
    "get_objective": get_objective,
    "get_activity_details": get_activity_details,
    "propose_workout": propose_workout,
}


def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Exécute un tool par son nom et retourne son résultat (ou une erreur)."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"Tool inconnu: {name}"}
    try:
        return fn(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"Arguments invalides pour {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Échec de {name}: {exc}"}
