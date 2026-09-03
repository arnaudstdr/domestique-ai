"""Tests du module ``domestique_ai.processing.today``."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from domestique_ai.processing import today as today_mod
from domestique_ai.processing.today import propose_workout_today


def _patch_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isole DB, availability, profil et objectif sous tmp_path."""
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "today.db"))
    monkeypatch.setenv(
        "DOMESTIQUE_AI_AVAILABILITY_PATH",
        str(tmp_path / "avail.yaml"),
    )
    monkeypatch.setenv(
        "DOMESTIQUE_AI_PROFILE_PATH",
        str(tmp_path / "profile.yaml"),
    )
    monkeypatch.setenv(
        "DOMESTIQUE_AI_OBJECTIVE_PATH",
        str(tmp_path / "objective.yaml"),
    )
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    from domestique_ai.config import invalidate_profile_cache

    invalidate_profile_cache()


def _set_availability(tmp_path: Path, content: str) -> None:
    (tmp_path / "avail.yaml").write_text(content)


def _set_objective(tmp_path: Path, content: str) -> None:
    (tmp_path / "objective.yaml").write_text(content)


def _patch_tsb(monkeypatch: pytest.MonkeyPatch, tsb: float) -> None:
    """Force la valeur de TSB du jour en interceptant ``calculate_ctl_atl_tsb``."""

    def fake_curves(*_args, **_kwargs):  # noqa: ANN001, ANN002
        return [{"date": "2026-01-01", "CTL": 50.0, "ATL": 50.0, "TSB": tsb}]

    monkeypatch.setattr(today_mod, "calculate_ctl_atl_tsb", fake_curves)
    monkeypatch.setattr(today_mod, "fetch_activities_from_db", lambda *_a, **_k: [])


def _patch_activities(
    monkeypatch: pytest.MonkeyPatch,
    activities: list[dict],
    tsb: float,
) -> None:
    """Permet d'injecter des activités passées tout en forçant le TSB."""

    def fake_curves(*_args, **_kwargs):  # noqa: ANN001, ANN002
        return [{"date": "2026-01-01", "CTL": 50.0, "ATL": 50.0, "TSB": tsb}]

    monkeypatch.setattr(today_mod, "calculate_ctl_atl_tsb", fake_curves)
    monkeypatch.setattr(today_mod, "fetch_activities_from_db", lambda *_a, **_k: activities)


@pytest.fixture(autouse=True)
def _disable_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Désactive l'appel LLM par défaut dans tous les tests.

    Les tests qui veulent valider la branche LLM monkeypatcheront
    ``_decide_kind_with_llm`` ou ``chat_structured_sync`` explicitement.
    """
    monkeypatch.setattr(today_mod, "_decide_kind_with_llm", lambda _d: None)


def test_rest_day_when_weekday_not_in_availability(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  saturday:\n    max_duration_min: 180\n    context: outdoor\n",
    )
    monday = dt.date(2026, 1, 5)  # 2026-01-05 = lundi
    result = propose_workout_today(today=monday)
    assert result["rest_day"] is True
    assert "lundi" in result["reason"].lower()


def test_endurance_when_tsb_optimal(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["rest_day"] is False
    assert result["workout"]["kind"] == "endurance"
    assert result["workout"]["target_zone"] == "z2"
    assert result["workout"]["duration_min"] == 90
    assert result["tsb_zone"] == "Optimal"
    assert result["source"] == "fallback"
    assert result["rationale"]


def test_recovery_when_tsb_low(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 60\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, -25.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "recovery"
    assert result["workout"]["target_zone"] == "z1"
    assert result["tsb_zone"] == "Surentraîné"


def test_tempo_when_tsb_high_without_intervals_day(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 75\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 12.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "tempo"
    assert result["workout"]["target_zone"] == "z3"


def test_intervals_when_tsb_high_and_intervals_day_matches(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        """
days:
  monday:
    max_duration_min: 90
    context: indoor
preferences:
  intervals_day: monday
""",
    )
    _patch_tsb(monkeypatch, 12.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "intervals"
    assert result["workout"]["target_zone"] == "z4"


def test_duration_capped_by_availability(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 45\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["duration_min"] == 45


def test_available_min_override_forces_duration_and_overrides_off_day(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  saturday:\n    max_duration_min: 180\n    context: outdoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday, available_min=70)
    assert result["rest_day"] is False
    assert result["workout"]["duration_min"] == 70


def test_no_availability_file_yields_workout(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["rest_day"] is False
    assert result["workout"]["kind"] == "endurance"
    assert result["workout"]["duration_min"] == 90


def test_structure_is_populated(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 60\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    structure = result["workout"]["structure"]
    assert len(structure) >= 1
    assert all("phase" in s and "zone" in s for s in structure)


def test_estimated_tss_is_positive(tmp_path, monkeypatch):
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 60\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["estimated_tss"] > 0


# ---------------------------------------------------------------------------
# Nouveaux tests : objectif, J-1, zones semaine, LLM, cache
# ---------------------------------------------------------------------------


def test_taper_when_two_weeks_before_objective(tmp_path, monkeypatch):
    """À ≤ 2 semaines de l'objectif, on bascule sur tempo (taper)."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    _set_objective(
        tmp_path,
        "type: cyclosportive\ndate: 2026-01-18\ndistance_km: 120\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monday = dt.date(2026, 1, 5)  # 2 semaines avant le 18
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "tempo"
    assert "taper" in result["rationale"].lower()


def test_endurance_after_intensity_yesterday(tmp_path, monkeypatch):
    """Si la dernière séance était des intervalles hier, on enchaîne en endurance."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        """
days:
  monday:
    max_duration_min: 90
    context: indoor
preferences:
  intervals_day: monday
""",
    )
    yesterday = dt.date(2026, 1, 4)
    activities = [
        {
            "date": yesterday.isoformat(),
            "avg_heart_rate": 150,
            "hr_z1_time": 600,
            "hr_z2_time": 400,
            "hr_z3_time": 300,
            "hr_z4_time": 900,  # forte dose Z4 → intervals
            "hr_z5_time": 200,
        }
    ]
    _patch_activities(monkeypatch, activities, tsb=10.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "endurance"


def test_tempo_when_weekly_zones_too_monotonous(tmp_path, monkeypatch):
    """Semaine très Z2 + peu d'intensité → on injecte du tempo pour casser la monotonie."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    activities = []
    for offset in (2, 3, 5):
        d = dt.date(2026, 1, 5) - dt.timedelta(days=offset)
        activities.append(
            {
                "date": d.isoformat(),
                "avg_heart_rate": 135,
                "hr_z1_time": 200,
                "hr_z2_time": 3600,  # massivement Z2
                "hr_z3_time": 0,
                "hr_z4_time": 0,
                "hr_z5_time": 0,
            }
        )
    _patch_activities(monkeypatch, activities, tsb=0.0)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "tempo"
    assert (
        "monotonie" in result["rationale"].lower() or "polarisation" in result["rationale"].lower()
    )


def test_llm_decision_is_used_when_available(tmp_path, monkeypatch):
    """Quand le LLM renvoie un JSON valide, sa décision est appliquée."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 120\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)

    def fake_llm(_dossier):
        return {
            "kind": "tempo",
            "duration_min": 75,
            "rationale": "Décision LLM : tempo Z3.",
            "confidence": 0.8,
        }

    monkeypatch.setattr(today_mod, "_decide_kind_with_llm", fake_llm)

    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["source"] == "llm"
    assert result["workout"]["kind"] == "tempo"
    assert result["workout"]["duration_min"] == 75
    assert result["signals"]["llm_confidence"] == 0.8


def test_llm_invalid_json_falls_back(tmp_path, monkeypatch):
    """Si _decide_kind_with_llm retourne None, le fallback déterministe prend la main."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)
    monkeypatch.setattr(today_mod, "_decide_kind_with_llm", lambda _d: None)
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["source"] == "fallback"
    assert result["workout"]["kind"] == "endurance"


def test_cache_hit_short_circuits_llm(tmp_path, monkeypatch):
    """Un second appel le même jour relit le cache au lieu de recalculer."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)

    call_count = {"n": 0}

    def counted_llm(_dossier):
        call_count["n"] += 1
        return {
            "kind": "tempo",
            "duration_min": 60,
            "rationale": "Test cache.",
            "confidence": 0.7,
        }

    monkeypatch.setattr(today_mod, "_decide_kind_with_llm", counted_llm)

    monday = dt.date(2026, 1, 5)
    first = propose_workout_today(today=monday)
    second = propose_workout_today(today=monday)
    assert call_count["n"] == 1
    assert first["workout"]["kind"] == second["workout"]["kind"]
    assert second["source"] == "cache"


def test_refresh_bypasses_cache(tmp_path, monkeypatch):
    """refresh=True force la régénération."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)

    call_count = {"n": 0}

    def counted_llm(_dossier):
        call_count["n"] += 1
        return {
            "kind": "endurance",
            "duration_min": 60,
            "rationale": "Test refresh.",
            "confidence": 0.7,
        }

    monkeypatch.setattr(today_mod, "_decide_kind_with_llm", counted_llm)

    monday = dt.date(2026, 1, 5)
    propose_workout_today(today=monday)
    propose_workout_today(today=monday, refresh=True)
    assert call_count["n"] == 2


def test_planned_workout_short_circuits(tmp_path, monkeypatch):
    """Si un plan persisté couvre aujourd'hui, on retourne la séance du plan."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 0.0)

    monday = dt.date(2026, 1, 5)
    planned = {
        "date": monday.isoformat(),
        "name": "Séance plan",
        "sport": "cycling",
        "kind": "intervals",
        "duration_min": 75,
        "target_zone": "z4",
        "structure": [
            {"phase": "warmup", "zone": "z1", "duration_sec": 600, "repeat": 1},
            {"phase": "active", "zone": "z4", "duration_sec": 1800, "repeat": 1},
            {"phase": "cooldown", "zone": "z1", "duration_sec": 600, "repeat": 1},
        ],
        "estimated_tss": 95.0,
        "notes": "",
    }

    monkeypatch.setattr(today_mod, "_planned_workout_for", lambda _today, _ctx=None: planned)

    result = propose_workout_today(today=monday)
    assert result["source"] == "plan"
    assert result["workout"]["kind"] == "intervals"
    assert result["workout"]["duration_min"] == 75


def test_critical_alert_forces_recovery(tmp_path, monkeypatch):
    """Une alerte critique (surentraînement / dérive matin) impose recovery."""
    _patch_paths(tmp_path, monkeypatch)
    _set_availability(
        tmp_path,
        "days:\n  monday:\n    max_duration_min: 90\n    context: indoor\n",
    )
    _patch_tsb(monkeypatch, 8.0)  # même frais → on doit forcer recovery
    monkeypatch.setattr(
        today_mod,
        "_alerts_summary",
        lambda _ctx=None: {"critical": True, "messages": ["tsb_chronic: fatigue durable"]},
    )
    monday = dt.date(2026, 1, 5)
    result = propose_workout_today(today=monday)
    assert result["workout"]["kind"] == "recovery"
    assert result["signals"]["alerts"]
