"""Tests pour le module d'objectif d'entraînement."""

from __future__ import annotations

import pytest

from domestique_ai.llm.objectives import (
    Objective,
    ObjectiveError,
    load_objective,
    save_objective,
)


def test_load_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH",
                       str(tmp_path / "missing.yaml"))
    assert load_objective() is None


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "obj.yaml"
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(path))

    original = Objective(
        type="cyclosportive",
        date="2026-09-15",
        distance_km=120,
        elevation_m=1500,
        notes="Marmotte",
    )
    save_objective(original)

    loaded = load_objective()
    assert loaded is not None
    assert loaded.type == "cyclosportive"
    assert loaded.distance_km == 120
    assert loaded.notes == "Marmotte"


def test_load_rejects_invalid_type(tmp_path, monkeypatch):
    path = tmp_path / "bad.yaml"
    path.write_text("type: random_thing\n")
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(path))

    with pytest.raises(ObjectiveError):
        load_objective()


def test_load_rejects_non_dict(tmp_path, monkeypatch):
    path = tmp_path / "bad.yaml"
    path.write_text("- just\n- a\n- list\n")
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(path))

    with pytest.raises(ObjectiveError):
        load_objective()


def test_to_dict_strips_none_and_empty():
    obj = Objective(type="maintenance", notes="")
    payload = obj.to_dict()
    assert payload == {"type": "maintenance"}


def test_load_coerces_native_date_to_string(tmp_path, monkeypatch):
    # PyYAML parse YYYY-MM-DD non quoté en datetime.date — on doit toujours
    # exposer une chaîne ISO pour rester JSON-sérialisable côté tool calling.
    path = tmp_path / "obj.yaml"
    path.write_text("type: cyclosportive\ndate: 2026-09-15\n")
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(path))

    loaded = load_objective()
    assert loaded is not None
    assert isinstance(loaded.date, str)
    assert loaded.date == "2026-09-15"

    # to_dict() doit aussi renvoyer une chaîne (utilisé par le tool get_objective)
    import json
    json.dumps(loaded.to_dict())  # ne doit pas lever


def test_extra_fields_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "obj.yaml"
    path.write_text(
        "type: course\ndate: 2026-04-01\ndistance_km: 21.1\n"
        "custom_field: foo\n"
    )
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(path))
    loaded = load_objective()
    assert loaded is not None
    assert loaded.extra == {"custom_field": "foo"}
    assert loaded.to_dict()["custom_field"] == "foo"
