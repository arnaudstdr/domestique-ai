"""Tests de l'export iCalendar d'un plan d'entraînement."""

from __future__ import annotations

import datetime as dt

import pytest

from domestique_ai.export.ics import (
    _escape_text,
    _fold_line,
    _format_dt_floating,
    _format_duration,
    plan_to_ics,
)
from domestique_ai.processing.plan_builder import Workout, WorkoutStep

FIXED_NOW = dt.datetime(2026, 5, 21, 14, 30, 0, tzinfo=dt.UTC)


def _make_workout(
    date: str = "2026-05-25",
    name: str = "Endurance Z2 90'",
    duration_min: int = 90,
    kind: str = "endurance",
    structure: list[WorkoutStep] | None = None,
    estimated_tss: float = 82.5,
    notes: str = "",
) -> Workout:
    if structure is None:
        structure = [
            WorkoutStep(phase="warmup", zone="z1", duration_sec=540),
            WorkoutStep(phase="active", zone="z2", duration_sec=4320),
            WorkoutStep(phase="cooldown", zone="z1", duration_sec=540),
        ]
    return Workout(
        date=date,
        name=name,
        sport="cycling",
        kind=kind,
        duration_min=duration_min,
        target_zone="z2",
        structure=structure,
        estimated_tss=estimated_tss,
        notes=notes,
    )


# ---------- Helpers internes -------------------------------------------------


def test_escape_text_quotes_special_chars():
    # L'ordre d'échappement des backslashes ne doit pas re-traiter les
    # antislashes qu'on vient d'introduire.
    assert _escape_text("Hello; world, test\nline\\back") == (
        "Hello\\; world\\, test\\nline\\\\back"
    )


def test_format_dt_floating_uses_local_time_without_tz():
    # Format RFC 5545 "floating local time" — pas de Z, pas de TZID.
    assert _format_dt_floating("2026-05-25", 18, 0) == "20260525T180000"


def test_format_duration_handles_hours_and_minutes():
    assert _format_duration(90) == "PT1H30M"
    assert _format_duration(60) == "PT1H"
    assert _format_duration(45) == "PT45M"
    assert _format_duration(0) == "PT0M"


def test_fold_line_short_lines_pass_through():
    short = "BEGIN:VEVENT"
    assert _fold_line(short) == short


def test_fold_line_breaks_long_lines_at_75_bytes():
    long_line = "X" * 200
    folded = _fold_line(long_line)
    # Doit contenir au moins un caractère de continuation (CRLF + espace).
    assert "\r\n " in folded
    # Aucune ligne brute ne doit dépasser 75 octets (avant l'espace de pliage).
    for segment in folded.split("\r\n "):
        assert len(segment.encode("utf-8")) <= 75


def test_fold_line_respects_utf8_codepoint_boundaries():
    """Régression : un accent multi-octets ne doit JAMAIS être coupé en deux.

    Avant le fix, ``encoded[:75].decode("utf-8")`` plantait avec
    ``UnicodeDecodeError: 'utf-8' codec can't decode byte 0xc3 in position
    73: unexpected end of data`` quand un `é` (0xC3 0xA9) tombait à cheval.
    """
    # Beaucoup d'accents → chaque `é` = 2 octets en UTF-8.
    long_line = "Échauffement Z2 endurance progressive " * 5
    # Ne doit pas planter.
    folded = _fold_line(long_line)
    # Chaque segment doit pouvoir être encodé/décodé sans erreur.
    for segment in folded.split("\r\n "):
        encoded = segment.encode("utf-8")
        assert encoded.decode("utf-8") == segment
        assert len(encoded) <= 75


def test_fold_line_preserves_content_after_concatenation():
    """La concaténation des segments doit redonner exactement la ligne d'origine."""
    long_line = "Séance d'intervalles à haute intensité avec éléments tempo " * 3
    folded = _fold_line(long_line)
    rebuilt = folded.replace("\r\n ", "")
    assert rebuilt == long_line


def test_fold_line_handles_4byte_emoji_safely():
    """Les emojis (4 octets UTF-8) doivent rester atomiques.

    Ce n'est pas un cas attendu en production (le projet bannit les emojis
    en français) mais le helper doit rester robuste si l'utilisateur en met
    dans les notes d'une séance.
    """
    # Une longue ligne avec un emoji (4 octets) toutes les ~20 caractères.
    long_line = "Bloc seuil 🚴 répété " * 10
    folded = _fold_line(long_line)
    # Aucune frontière de codepoint cassée.
    for segment in folded.split("\r\n "):
        assert segment.encode("utf-8").decode("utf-8") == segment


def test_plan_to_ics_with_accented_notes_does_not_raise(tmp_path):
    """Régression : export complet d'un plan avec notes accentuées."""
    from domestique_ai.processing.plan_builder import Workout, WorkoutStep

    workout = Workout(
        date="2026-05-25",
        name="Séance d'évaluation seuil — bloc principal en zone tempo",
        sport="cycling",
        kind="tempo",
        duration_min=75,
        target_zone="z3",
        structure=[
            WorkoutStep(phase="warmup", zone="z1", duration_sec=600),
            WorkoutStep(phase="active", zone="z3", duration_sec=3000),
            WorkoutStep(phase="cooldown", zone="z1", duration_sec=900),
        ],
        estimated_tss=90.0,
        notes=(
            "Séance d'évaluation pour estimer la FTP — rester régulier, "
            "éviter les à-coups, contrôler la respiration"
        ),
    )
    # Ne doit plus lever UnicodeDecodeError.
    payload = plan_to_ics([workout], plan_id=1, now=FIXED_NOW)
    text = payload.decode("utf-8")
    # Contenu accentué bien préservé (potentiellement coupé sur plusieurs lignes
    # avec CRLF + espace, mais reconstructible).
    rebuilt = text.replace("\r\n ", "")
    assert "Séance" in rebuilt
    assert "évaluation" in rebuilt
    assert "régulier" in rebuilt


# ---------- plan_to_ics() ----------------------------------------------------


def test_plan_to_ics_returns_bytes_with_expected_structure():
    plan = [_make_workout()]
    payload = plan_to_ics(plan, plan_id=42, now=FIXED_NOW)
    text = payload.decode("utf-8")
    # Wrapper VCALENDAR + un VEVENT au minimum.
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert "END:VCALENDAR\r\n" in text
    assert "BEGIN:VEVENT" in text
    assert "END:VEVENT" in text


def test_plan_to_ics_uses_stable_uid():
    """L'UID doit dépendre du plan_id et de la date — pas du temps d'export."""
    plan = [_make_workout(date="2026-05-25")]
    a = plan_to_ics(plan, plan_id=7, now=FIXED_NOW).decode("utf-8")
    b = plan_to_ics(plan, plan_id=7, now=FIXED_NOW + dt.timedelta(hours=1)).decode("utf-8")
    # Les UID restent identiques même si DTSTAMP change → idempotence côté
    # client calendrier (réimport = update, pas duplicat).
    assert "UID:plan-7-2026-05-25@domestique-ai" in a
    assert "UID:plan-7-2026-05-25@domestique-ai" in b


def test_plan_to_ics_emits_dtstart_at_default_hour():
    plan = [_make_workout(date="2026-05-25")]
    text = plan_to_ics(plan, plan_id=1, now=FIXED_NOW).decode("utf-8")
    # 18 h locale par défaut.
    assert "DTSTART:20260525T180000" in text


def test_plan_to_ics_duration_matches_workout():
    plan = [_make_workout(duration_min=75)]
    text = plan_to_ics(plan, plan_id=1, now=FIXED_NOW).decode("utf-8")
    assert "DURATION:PT1H15M" in text


def test_plan_to_ics_description_contains_structure_and_tss():
    plan = [_make_workout(estimated_tss=82.5)]
    text = plan_to_ics(plan, plan_id=1, now=FIXED_NOW).decode("utf-8")
    # Le DESCRIPTION inclut la structure échauffement/actif/cooldown et le TSS.
    assert "Echauffement" in text or "chauffement" in text  # accents tolérés
    assert "Z2" in text
    # 82.5 formaté avec :.0f → 82 (arrondi banquier de Python).
    assert "TSS" in text and "82" in text


def test_plan_to_ics_escapes_special_chars_in_summary_and_notes():
    plan = [
        _make_workout(
            name="Sweetspot 2x20'; bloc principal",
            notes="Outdoor — vent fort, prudence",
        )
    ]
    text = plan_to_ics(plan, plan_id=1, now=FIXED_NOW).decode("utf-8")
    # Le ; doit être échappé en \; dans la valeur sérialisée.
    assert "SUMMARY:Sweetspot 2x20'\\; bloc principal" in text
    # La virgule de DESCRIPTION doit être échappée en \,
    assert "vent fort\\, prudence" in text


def test_plan_to_ics_multiple_events_in_one_calendar():
    plan = [
        _make_workout(date="2026-05-25"),
        _make_workout(date="2026-05-27", name="Tempo Z3 60'", duration_min=60),
        _make_workout(date="2026-05-30", name="Intervalles Z4 4x10'"),
    ]
    text = plan_to_ics(plan, plan_id=2, now=FIXED_NOW).decode("utf-8")
    assert text.count("BEGIN:VEVENT") == 3
    assert text.count("END:VEVENT") == 3
    # Tous les UID partagent le même plan_id mais varient sur la date.
    assert "UID:plan-2-2026-05-25@domestique-ai" in text
    assert "UID:plan-2-2026-05-27@domestique-ai" in text


def test_plan_to_ics_uses_crlf_line_endings():
    """RFC 5545 exige des CRLF, pas seulement LF — sinon Outlook refuse l'import."""
    plan = [_make_workout()]
    payload = plan_to_ics(plan, plan_id=1, now=FIXED_NOW)
    # On vérifie qu'il y a bien des \r\n (la string contient des CRLF).
    assert b"\r\n" in payload
    # Et qu'aucun \n n'est isolé sans \r juste avant.
    raw = payload
    lone_lf = 0
    for i, byte in enumerate(raw):
        if byte == 0x0A and (i == 0 or raw[i - 1] != 0x0D):
            lone_lf += 1
    assert lone_lf == 0


def test_plan_to_ics_dtstamp_in_utc():
    plan = [_make_workout()]
    text = plan_to_ics(plan, plan_id=1, now=FIXED_NOW).decode("utf-8")
    # Format UTC : YYYYMMDDTHHMMSSZ
    assert "DTSTAMP:20260521T143000Z" in text


def test_plan_to_ics_empty_plan_returns_minimal_calendar():
    payload = plan_to_ics([], plan_id=1, now=FIXED_NOW)
    text = payload.decode("utf-8")
    assert "BEGIN:VCALENDAR" in text
    assert "END:VCALENDAR" in text
    assert "BEGIN:VEVENT" not in text


def test_plan_to_ics_custom_default_hour():
    plan = [_make_workout(date="2026-05-25")]
    text = plan_to_ics(plan, plan_id=1, default_hour=7, now=FIXED_NOW).decode("utf-8")
    assert "DTSTART:20260525T070000" in text
    # Pas de fuite de l'ancienne valeur.
    assert "T180000" not in text


def test_plan_to_ics_categories_include_kind():
    plan = [_make_workout(kind="intervals")]
    text = plan_to_ics(plan, plan_id=1, now=FIXED_NOW).decode("utf-8")
    assert "CATEGORIES:Entrainement,intervals" in text


# ---------- Endpoint ---------------------------------------------------------


def test_endpoint_returns_text_calendar(tmp_path, monkeypatch):
    """Smoke test : l'endpoint répond 200 avec un body iCalendar valide."""
    # On stocke un plan minimal en DB tmp pour pouvoir le récupérer ensuite.
    from fastapi.testclient import TestClient

    from domestique_ai.api.main import app
    from domestique_ai.config import get_db_path
    from domestique_ai.llm import plan_storage

    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "ics.db"))
    # Force le re-resolve du chemin (les autres modules lisent get_db_path()
    # à chaque appel donc rien à faire ; juste contrôler que c'est bien tmp).
    assert get_db_path() == tmp_path / "ics.db"

    plan_id = plan_storage.save_plan(
        [_make_workout(date="2026-05-25"), _make_workout(date="2026-05-28")],
        target_date=dt.date(2026, 6, 15),
        target_event_type="cyclosportive",
        sessions_per_week=4,
    )

    client = TestClient(app)
    response = client.get(f"/api/plan/{plan_id}/export.ics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "filename=" in response.headers.get("content-disposition", "")
    text = response.content.decode("utf-8")
    assert "BEGIN:VCALENDAR" in text
    assert text.count("BEGIN:VEVENT") == 2


def test_endpoint_returns_404_when_plan_missing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from domestique_ai.api.main import app

    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(tmp_path / "missing.db"))
    client = TestClient(app)
    response = client.get("/api/plan/9999/export.ics")
    assert response.status_code == 404


# Le test ci-dessus protège la branche `summary in text` en cas d'évolution
# future du Workout — mais on évite de pinner une chaîne précise pour ne pas
# casser sur un simple changement de nommage.
@pytest.mark.parametrize("hour", [6, 12, 18, 21])
def test_default_hour_param_emits_correct_dtstart(hour: int):
    plan = [_make_workout()]
    text = plan_to_ics(plan, plan_id=1, default_hour=hour, now=FIXED_NOW).decode("utf-8")
    assert f"T{hour:02d}0000" in text
