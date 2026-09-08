"""Tests du module ``domestique_ai.processing.athlete_state``."""

from __future__ import annotations

import datetime as dt

from domestique_ai.processing.athlete_state import (
    CEILING_BASE,
    CEILING_FULL,
    CEILING_TEMPO,
    format_state_block,
    intensity_ceiling,
    is_deconditioned,
    ramp_weeks_for_level,
    summarize_load_state,
)


def _curves(ctl_series: list[float]) -> list[dict[str, object]]:
    base = dt.date(2026, 9, 1)
    out = []
    for i, c in enumerate(ctl_series):
        out.append(
            {
                "date": (base + dt.timedelta(days=i)).isoformat(),
                "CTL": c,
                "ATL": c + 3.0,
                "TSB": -3.0,
            }
        )
    return out


# --- summarize_load_state ---------------------------------------------------


def test_summarize_empty_is_unavailable() -> None:
    s = summarize_load_state([])
    assert s["available"] is False
    assert s["ctl"] is None
    assert s["ctl_trend"] == "flat"


def test_summarize_detects_rising_ctl() -> None:
    s = summarize_load_state(_curves([10 + 0.5 * i for i in range(30)]))
    assert s["available"] is True
    assert s["ctl_trend"] == "rising"
    assert s["tsb"] is not None


def test_summarize_detects_falling_ctl() -> None:
    s = summarize_load_state(_curves([40 - 0.5 * i for i in range(30)]))
    assert s["ctl_trend"] == "falling"


# --- is_deconditioned (règle composite) -------------------------------------


def test_reprise_when_ctl_below_threshold() -> None:
    assert is_deconditioned(9.0, threshold=20.0) is True


def test_reprise_when_ctl_falling_even_if_high() -> None:
    assert is_deconditioned(40.0, ctl_trend="falling") is True


def test_reprise_when_chronic_tsb_very_negative() -> None:
    assert is_deconditioned(50.0, ctl_trend="rising", chronic_tsb=-25.0) is True


def test_not_reprise_when_fit_and_rising() -> None:
    assert is_deconditioned(60.0, ctl_trend="rising", chronic_tsb=2.0) is False


# --- intensity_ceiling (rampe graduée) --------------------------------------


def test_full_ceiling_when_not_reprise() -> None:
    ceiling = intensity_ceiling(0, ctl_current=60.0, level="beginner", ctl_trend="rising")
    assert ceiling == CEILING_FULL


def test_ramp_starts_with_base_week() -> None:
    assert intensity_ceiling(0, ctl_current=9.0, level="intermediate") == CEILING_BASE


def test_ramp_second_week_is_tempo_for_intermediate() -> None:
    assert intensity_ceiling(1, ctl_current=9.0, level="intermediate") == CEILING_TEMPO


def test_ramp_ends_returns_to_full() -> None:
    # intermediate : 2 semaines de rampe (idx 0 base, idx 1 tempo) puis full.
    assert ramp_weeks_for_level("intermediate") == 2
    assert intensity_ceiling(2, ctl_current=9.0, level="intermediate") == CEILING_FULL


def test_advanced_ramp_is_shorter_than_beginner() -> None:
    adv = [intensity_ceiling(i, ctl_current=9.0, level="advanced") for i in range(3)]
    beg = [intensity_ceiling(i, ctl_current=9.0, level="beginner") for i in range(3)]
    assert adv[1] == CEILING_FULL
    assert beg[1] != CEILING_FULL
    assert beg[0] == CEILING_BASE


# --- format_state_block -----------------------------------------------------


def test_format_block_mentions_facts_and_reprise() -> None:
    state = {
        "load": {
            "available": True,
            "ctl": 9.0,
            "atl": 12.0,
            "tsb": -3.0,
            "ctl_trend": "falling",
            "chronic_tsb": -3.0,
        },
        "profile": {"level": "ex_competitor", "ftp": 250},
        "objective": {"type": "forme"},
        "reprise": True,
    }
    text = format_state_block(state)
    assert "État réel" in text
    assert "ancien compétiteur" in text
    assert "déconditionné" in text
    # Le bloc ne doit JAMAIS prescrire lui-même une séance (il n'énonce que des faits).
    assert "Intervalles" not in text
