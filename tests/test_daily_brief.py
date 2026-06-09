"""Tests du briefing quotidien (palier 1 + palier 2)."""

from __future__ import annotations

import datetime as dt

import pytest

from domestique_ai.llm import daily_brief
from domestique_ai.llm.daily_brief import (
    _build_fallback_summary,
    _hash_alerts,
    _round_tsb,
    _select_primary_alert,
    build_coach_context,
    build_daily_brief,
    clear_cache,
)


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Réinitialise le cache entre chaque test."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def stable_signals(monkeypatch):
    """Stub ``_collect_signals`` pour rendre les tests déterministes."""
    def fake_collect(today, ctx=None):
        return {
            "today": today.isoformat(),
            "tsb": 5.2,
            "tsb_zone": "Frais",
            "primary_alert": None,
            "workout": {
                "rest_day": False,
                "workout": {
                    "kind": "endurance",
                    "duration_min": 90,
                    "name": "Endurance Z2 90'",
                },
                "tsb": 5.2,
                "tsb_zone": "Frais",
            },
        }

    monkeypatch.setattr(daily_brief, "_collect_signals", fake_collect)
    return fake_collect


# ---------- Helpers internes -------------------------------------------------


def test_round_tsb_buckets_around_zero():
    """Bucket TSB par pas de 5, arrondi banquier."""
    assert _round_tsb(0.0) == 0
    assert _round_tsb(2.4) == 0  # < 2.5 → bucket 0
    # 2.5/5 = 0.5 → 0 (banker's). 7.5/5 = 1.5 → 2 (banker's).
    assert _round_tsb(2.5) == 0
    assert _round_tsb(7.5) == 2
    assert _round_tsb(-7.5) == -2
    assert _round_tsb(12.3) == 2


def test_hash_alerts_stable_for_same_set():
    a = _hash_alerts(["TSB chronique faible", "Monotony élevée"])
    b = _hash_alerts(["Monotony élevée", "TSB chronique faible"])
    # L'ordre ne doit pas affecter le hash (sorted en interne).
    assert a == b


def test_hash_alerts_changes_when_alert_added():
    base = _hash_alerts(["TSB chronique faible"])
    extra = _hash_alerts(["TSB chronique faible", "Monotony élevée"])
    assert base != extra


def test_hash_alerts_empty_is_stable():
    assert _hash_alerts([]) == _hash_alerts([])


# ---------- Sélection alerte saillante ---------------------------------------


def test_select_primary_alert_prioritizes_tsb_chronic():
    overtraining = {
        "alerts": [
            {"indicator": "monotony", "message": "Monotony élevée"},
            {"indicator": "tsb_chronic", "message": "TSB chronique faible"},
        ]
    }
    primary = _select_primary_alert(overtraining, [])
    assert primary["type"] == "tsb_chronic"
    assert primary["severity"] == "danger"


def test_select_primary_alert_falls_back_to_warning():
    overtraining = {"alerts": [{"indicator": "monotony", "message": "Monotony"}]}
    primary = _select_primary_alert(overtraining, [])
    assert primary["severity"] == "warning"


def test_select_primary_alert_uses_morning_when_no_overtraining():
    morning = [
        {
            "metric": "hrv",
            "delta_pct": -22.0,
            "severity": "critical",
            "latest": 28.0,
            "latest_date": "2026-05-21",
        }
    ]
    primary = _select_primary_alert({"alerts": []}, morning)
    assert primary["type"] == "morning_hrv"
    assert primary["severity"] == "danger"
    assert "hrv" in primary["message"]


def test_select_primary_alert_returns_none_when_clean():
    assert _select_primary_alert({"alerts": []}, []) is None


# ---------- Fallback summary -------------------------------------------------


def test_fallback_summary_includes_tsb_and_workout():
    signals = {
        "tsb": 5.2,
        "tsb_zone": "Frais",
        "primary_alert": None,
        "workout": {
            "rest_day": False,
            "workout": {"kind": "endurance", "duration_min": 90, "name": "..."},
        },
    }
    summary = _build_fallback_summary(signals)
    assert "TSB" in summary
    assert "5.2" in summary
    assert "endurance" in summary
    assert "90" in summary


def test_fallback_summary_mentions_rest_day():
    signals = {
        "tsb": -2.0,
        "tsb_zone": "Optimal",
        "primary_alert": None,
        "workout": {"rest_day": True, "reason": "Vendredi off"},
    }
    summary = _build_fallback_summary(signals)
    assert "repos" in summary.lower()


def test_fallback_summary_appends_alert_message():
    signals = {
        "tsb": -15.0,
        "tsb_zone": "Fatigué",
        "primary_alert": {
            "type": "tsb_chronic",
            "severity": "danger",
            "message": "TSB chronique sous -20",
        },
        "workout": {
            "rest_day": False,
            "workout": {"kind": "recovery", "duration_min": 45, "name": "..."},
        },
    }
    summary = _build_fallback_summary(signals)
    assert "Alerte" in summary
    assert "TSB chronique" in summary


# ---------- build_daily_brief : fallback path --------------------------------


def test_build_daily_brief_returns_fallback_when_llm_disabled(stable_signals):
    brief = build_daily_brief(today=dt.date(2026, 5, 21), use_llm=False)
    assert brief["source"] == "fallback"
    assert brief["date"] == "2026-05-21"
    assert brief["tsb"] == pytest.approx(5.2)
    assert brief["tsb_zone"] == "Frais"
    assert brief["today_workout"]["kind"] == "endurance"
    assert brief["today_workout"]["duration_min"] == 90
    assert brief["today_workout"]["rest_day"] is False
    # Phrase template présente.
    assert isinstance(brief["summary"], str)
    assert brief["summary"]


def test_build_daily_brief_uses_llm_when_available(stable_signals, monkeypatch):
    monkeypatch.setattr(
        daily_brief,
        "_generate_summary_with_llm",
        lambda signals: "Forme correcte, séance d'endurance prévue.",
    )
    brief = build_daily_brief(today=dt.date(2026, 5, 21))
    assert brief["source"] == "llm"
    assert "Forme correcte" in brief["summary"]


def test_build_daily_brief_falls_back_when_llm_returns_none(stable_signals, monkeypatch):
    monkeypatch.setattr(
        daily_brief, "_generate_summary_with_llm", lambda signals: None
    )
    brief = build_daily_brief(today=dt.date(2026, 5, 21))
    assert brief["source"] == "fallback"


# ---------- Cache journalier -------------------------------------------------


def test_build_daily_brief_caches_within_same_day(stable_signals, monkeypatch):
    call_count = {"value": 0}

    def counting_llm(signals):
        call_count["value"] += 1
        return f"call #{call_count['value']}"

    monkeypatch.setattr(daily_brief, "_generate_summary_with_llm", counting_llm)
    a = build_daily_brief(today=dt.date(2026, 5, 21))
    b = build_daily_brief(today=dt.date(2026, 5, 21))
    assert call_count["value"] == 1
    assert a["summary"] == b["summary"]
    assert b["source"] == "cache"


def test_build_daily_brief_refresh_bypasses_cache(stable_signals, monkeypatch):
    call_count = {"value": 0}

    def counting_llm(signals):
        call_count["value"] += 1
        return f"call #{call_count['value']}"

    monkeypatch.setattr(daily_brief, "_generate_summary_with_llm", counting_llm)
    build_daily_brief(today=dt.date(2026, 5, 21))
    fresh = build_daily_brief(today=dt.date(2026, 5, 21), refresh=True)
    assert call_count["value"] == 2
    assert fresh["source"] == "llm"


def test_build_daily_brief_purges_old_cache_keys(stable_signals, monkeypatch):
    monkeypatch.setattr(daily_brief, "_generate_summary_with_llm", lambda s: "x")
    build_daily_brief(today=dt.date(2026, 5, 20))
    build_daily_brief(today=dt.date(2026, 5, 21))
    # Le cache ne doit plus contenir d'entrée pour la veille.
    # Clé = (db_path, date ISO, bucket TSB, hash alertes) → la date est en [1].
    assert all(key[1] == "2026-05-21" for key in daily_brief._BRIEF_CACHE)


# ---------- build_coach_context (palier 2) -----------------------------------


def test_build_coach_context_includes_tsb_and_workout(stable_signals):
    ctx = build_coach_context(today=dt.date(2026, 5, 21))
    assert "TSB" in ctx
    assert "5.2" in ctx
    assert "endurance" in ctx
    assert "90 min" in ctx


def test_build_coach_context_mentions_no_alert_when_clean(stable_signals):
    ctx = build_coach_context(today=dt.date(2026, 5, 21))
    assert "aucune" in ctx.lower()


def test_build_coach_context_mentions_alert_when_present(monkeypatch):
    def alerted_collect(today, ctx=None):
        return {
            "today": today.isoformat(),
            "tsb": -22.0,
            "tsb_zone": "Surentraîné",
            "primary_alert": {
                "type": "tsb_chronic",
                "severity": "danger",
                "message": "TSB chronique très bas",
            },
            "workout": {
                "rest_day": False,
                "workout": {
                    "kind": "recovery",
                    "duration_min": 30,
                    "name": "Récup 30'",
                },
            },
        }

    monkeypatch.setattr(daily_brief, "_collect_signals", alerted_collect)
    monkeypatch.setattr(daily_brief, "_generate_summary_with_llm", lambda s: None)
    ctx = build_coach_context(today=dt.date(2026, 5, 21))
    assert "TSB chronique" in ctx


def test_build_coach_context_handles_rest_day(monkeypatch):
    def rest_collect(today, ctx=None):
        return {
            "today": today.isoformat(),
            "tsb": 5.0,
            "tsb_zone": "Frais",
            "primary_alert": None,
            "workout": {"rest_day": True, "reason": "Mardi off"},
        }

    monkeypatch.setattr(daily_brief, "_collect_signals", rest_collect)
    monkeypatch.setattr(daily_brief, "_generate_summary_with_llm", lambda s: None)
    ctx = build_coach_context(today=dt.date(2026, 5, 21))
    assert "repos" in ctx.lower()
