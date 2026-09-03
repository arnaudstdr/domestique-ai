"""Tests d'intégration des endpoints FastAPI.

Utilise un override de `DOMESTIQUE_AI_DB_PATH` pour isoler chaque test sur
une base SQLite tmp. Aucun appel réseau (Strava / Ollama mockés).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from domestique_ai.ingestion.strava import init_db, save_activity


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    db = tmp_path / "api_test.db"
    monkeypatch.setenv("DOMESTIQUE_AI_DB_PATH", str(db))
    monkeypatch.setenv("DOMESTIQUE_AI_OBJECTIVE_PATH", str(tmp_path / "obj.yaml"))
    monkeypatch.delenv("STRAVA_HR_REST", raising=False)
    monkeypatch.delenv("STRAVA_HR_MAX", raising=False)
    init_db(db)
    from domestique_ai.api.main import app

    with TestClient(app) as c:
        yield c


def _insert_activity(db: Path, strava_id: int, date: str, training_load: float) -> None:
    save_activity(
        {
            "id": strava_id,
            "date": date,
            "duration": 3600,
            "avg_heart_rate": 145,
            "max_heart_rate": 170,
            "avg_power": 200,
            "elevation_gain": 300,
            "distance": 30000,
            "training_load": training_load,
            "sport_type": "Ride",
        },
        db_path=db,
    )


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_metrics_load_empty(client: TestClient) -> None:
    r = client.get("/api/metrics/load")
    assert r.status_code == 200
    body = r.json()
    assert body["current"] is None
    assert body["history"] == []


def test_metrics_load_with_activities(client: TestClient, tmp_path: Path) -> None:
    db = Path(tmp_path / "api_test.db")
    _insert_activity(db, 1, "2026-04-01T08:00:00Z", 80.0)
    _insert_activity(db, 2, "2026-04-15T08:00:00Z", 100.0)
    r = client.get("/api/metrics/load?days=180")
    assert r.status_code == 200
    body = r.json()
    assert body["current"] is not None
    assert body["current"]["zone"] in {
        "freshness",
        "optimal",
        "overreaching",
        "overtraining",
    }
    assert len(body["history"]) > 0


def test_activities_list_pagination(client: TestClient, tmp_path: Path) -> None:
    db = Path(tmp_path / "api_test.db")
    for i in range(5):
        _insert_activity(db, 100 + i, f"2026-04-{10 + i:02d}T08:00:00Z", 50.0)
    r = client.get("/api/activities?page=1&page_size=3")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 3
    assert len(body["items"]) == 3
    # Tri DESC par date — l'activité 104 doit être en premier.
    assert body["items"][0]["strava_id"] == 104


def _insert_full(
    db: Path,
    strava_id: int,
    date: str,
    *,
    distance_m: float = 30000.0,
    elevation_m: float = 300.0,
    duration_sec: int = 3600,
    training_load: float = 50.0,
    sport_type: str = "Ride",
) -> None:
    save_activity(
        {
            "id": strava_id,
            "date": date,
            "duration": duration_sec,
            "avg_heart_rate": 145,
            "max_heart_rate": 170,
            "avg_power": 200,
            "elevation_gain": elevation_m,
            "distance": distance_m,
            "training_load": training_load,
            "sport_type": sport_type,
        },
        db_path=db,
    )


def test_activities_filter_by_date_range(client: TestClient, tmp_path: Path) -> None:
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-10T08:00:00Z")
    _insert_full(db, 2, "2026-04-20T08:00:00Z")
    _insert_full(db, 3, "2026-04-30T08:00:00Z")
    # Bornes inclusives au jour près.
    r = client.get("/api/activities?date_from=2026-04-15&date_to=2026-04-25")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["strava_id"] for i in items] == [2]


def test_activities_filter_date_to_is_inclusive(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """``date_to=2026-04-30`` doit inclure les activités du 30 avril en soirée."""
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-30T22:00:00Z")
    r = client.get("/api/activities?date_to=2026-04-30")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_activities_filter_by_sport_types(
    client: TestClient,
    tmp_path: Path,
) -> None:
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-10T08:00:00Z", sport_type="Ride")
    _insert_full(db, 2, "2026-04-11T08:00:00Z", sport_type="VirtualRide")
    _insert_full(db, 3, "2026-04-12T08:00:00Z", sport_type="Walk")
    r = client.get("/api/activities?sport_types=Ride&sport_types=Walk")
    assert r.status_code == 200
    ids = sorted(i["strava_id"] for i in r.json()["items"])
    assert ids == [1, 3]


def test_activities_filter_by_distance(client: TestClient, tmp_path: Path) -> None:
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-10T08:00:00Z", distance_m=20000)  # 20 km
    _insert_full(db, 2, "2026-04-11T08:00:00Z", distance_m=50000)  # 50 km
    _insert_full(db, 3, "2026-04-12T08:00:00Z", distance_m=100000)  # 100 km
    r = client.get("/api/activities?distance_min_km=30&distance_max_km=80")
    assert r.status_code == 200
    ids = sorted(i["strava_id"] for i in r.json()["items"])
    assert ids == [2]


def test_activities_filter_by_elevation(client: TestClient, tmp_path: Path) -> None:
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-10T08:00:00Z", elevation_m=100)
    _insert_full(db, 2, "2026-04-11T08:00:00Z", elevation_m=800)
    r = client.get("/api/activities?elevation_min_m=500")
    assert [i["strava_id"] for i in r.json()["items"]] == [2]


def test_activities_filter_by_duration(client: TestClient, tmp_path: Path) -> None:
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-10T08:00:00Z", duration_sec=1800)  # 30 min
    _insert_full(db, 2, "2026-04-11T08:00:00Z", duration_sec=7200)  # 2 h
    r = client.get("/api/activities?duration_min_sec=3600")
    assert [i["strava_id"] for i in r.json()["items"]] == [2]


def test_activities_filter_by_tss(client: TestClient, tmp_path: Path) -> None:
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-10T08:00:00Z", training_load=30.0)
    _insert_full(db, 2, "2026-04-11T08:00:00Z", training_load=120.0)
    r = client.get("/api/activities?tss_min=50&tss_max=200")
    assert [i["strava_id"] for i in r.json()["items"]] == [2]


def test_activities_filter_combinations_are_and(
    client: TestClient,
    tmp_path: Path,
) -> None:
    """Tous les filtres sont combinés en ET logique."""
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-10T08:00:00Z", sport_type="Ride", distance_m=80000)
    _insert_full(db, 2, "2026-04-11T08:00:00Z", sport_type="VirtualRide", distance_m=80000)
    _insert_full(db, 3, "2026-04-12T08:00:00Z", sport_type="Ride", distance_m=20000)
    r = client.get(
        "/api/activities?sport_types=Ride&distance_min_km=50",
    )
    assert [i["strava_id"] for i in r.json()["items"]] == [1]


def test_activities_filter_invalid_date_returns_400(client: TestClient) -> None:
    r = client.get("/api/activities?date_from=not-a-date")
    assert r.status_code == 400


def test_activities_sport_types_endpoint(
    client: TestClient,
    tmp_path: Path,
) -> None:
    db = Path(tmp_path / "api_test.db")
    _insert_full(db, 1, "2026-04-10T08:00:00Z", sport_type="Ride")
    _insert_full(db, 2, "2026-04-11T08:00:00Z", sport_type="VirtualRide")
    _insert_full(db, 3, "2026-04-12T08:00:00Z", sport_type="Ride")
    r = client.get("/api/activities/sport-types")
    assert r.status_code == 200
    # Tri alpha, dédupliqué.
    assert r.json() == ["Ride", "VirtualRide"]


def test_overtraining_endpoint_empty(client: TestClient) -> None:
    r = client.get("/api/metrics/overtraining")
    assert r.status_code == 200
    body = r.json()
    assert body["alerts"] == []
    assert body["indicators"]["monotony"] is None


def test_morning_get_empty(client: TestClient) -> None:
    r = client.get("/api/morning")
    assert r.status_code == 200
    body = r.json()
    assert body["history"] == []
    assert body["alerts"] == []
    assert set(body["baselines"].keys()) == {
        "hrv_ms",
        "resting_hr",
        "sleep_hours",
        "sleep_score",
        "stress_score",
    }


def test_morning_post_then_get(client: TestClient) -> None:
    r = client.post(
        "/api/morning",
        json={"date": "2026-05-01", "hrv_ms": 60.0, "resting_hr": 50.0},
    )
    assert r.status_code == 204
    body = client.get("/api/morning").json()
    assert len(body["history"]) == 1
    assert body["history"][0]["hrv_ms"] == 60.0


def test_objective_get_absent(client: TestClient) -> None:
    r = client.get("/api/objective")
    assert r.status_code == 200
    assert r.json() is None


def test_objective_put_then_get(client: TestClient) -> None:
    payload = {
        "type": "cyclosportive",
        "date": "2026-09-01",
        "distance_km": 150.0,
        "elevation_m": 2500.0,
        "target_ftp": None,
        "target_avg_hr_zone": None,
        "notes": "Objectif été",
    }
    r = client.put("/api/objective", json=payload)
    assert r.status_code == 200
    got = client.get("/api/objective").json()
    assert got["type"] == "cyclosportive"
    assert got["distance_km"] == 150.0


def test_strava_sync_status_idle(client: TestClient) -> None:
    r = client.get("/api/strava/sync-status")
    assert r.status_code == 200
    assert r.json()["status"] in {"idle", "syncing", "done", "error"}


def test_strava_recalculate_empty_db(client: TestClient) -> None:
    r = client.post("/api/strava/recalculate")
    assert r.status_code == 200
    assert r.json()["status"] == "done"


def test_activity_detail_not_found(client: TestClient) -> None:
    # Sans credentials Strava, l'endpoint doit échouer avec 503 au moment du Depends.
    r = client.get("/api/activities/999999")
    assert r.status_code in {404, 503}


def test_coach_sessions_empty(client: TestClient) -> None:
    r = client.get("/api/coach/sessions")
    assert r.status_code == 200
    assert r.json() == []


def test_coach_chat_streaming_with_mocked_run_turn_stream(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le coach yield bien la séquence d'événements SSE attendue.

    Sémantique deltas : `thinking` et `token` arrivent en plusieurs fragments.
    L'event interne `final` ne doit pas traverser le wire.
    """

    async def fake_stream(message: str, history=None, *, ctx=None):  # noqa: ANN001
        yield {"type": "thinking", "value": "ref"}
        yield {"type": "thinking", "value": "lexion"}
        yield {
            "type": "tool_call",
            "name": "get_training_load_state",
            "args": {},
        }
        yield {
            "type": "tool_result",
            "name": "get_training_load_state",
            "result": {"available": False, "reason": "vide"},
        }
        yield {"type": "token", "value": "Sal"}
        yield {"type": "token", "value": "ut"}
        yield {
            "type": "final",
            "content": "Salut",
            "thinking": "reflexion",
            "tool_trace": [
                {
                    "name": "get_training_load_state",
                    "arguments": {},
                    "result": {"available": False, "reason": "vide"},
                }
            ],
        }

    monkeypatch.setattr(
        "domestique_ai.api.routers.coach.run_turn_stream",
        fake_stream,
    )

    with client.stream(
        "POST",
        "/api/coach/chat",
        json={"session_id": None, "message": "Test"},
    ) as response:
        assert response.status_code == 200
        events: list[str] = []
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(line[5:].strip())

    joined = "".join(events)
    assert "session_id" in joined
    assert "thinking" in joined
    assert "tool_call" in joined
    assert "tool_result" in joined
    assert "done" in joined
    # Sémantique deltas : au moins 2 events thinking et 2 events token.
    assert sum(1 for e in events if '"type": "thinking"' in e) >= 2
    assert sum(1 for e in events if '"type": "token"' in e) >= 2
    # L'event "final" est interne, ne doit pas traverser le wire.
    assert '"type": "final"' not in joined


def test_app_routes_registered() -> None:
    """Smoke test : on s'assure que tous les routers sont bien câblés."""
    from domestique_ai.api.main import app

    # ``app.openapi()`` matérialise les routes : compatible avec les routers
    # lazy de Starlette récent (``_IncludedRouter`` sans attribut ``path``).
    paths = set(app.openapi()["paths"])
    expected = {
        "/api/health",
        "/api/metrics/load",
        "/api/metrics/overtraining",
        "/api/activities",
        "/api/activities/{strava_id}",
        "/api/morning",
        "/api/objective",
        "/api/strava/sync",
        "/api/strava/sync-status",
        "/api/strava/recalculate",
        "/api/strava/backfill-hr-zones",
        "/api/coach/sessions",
        "/api/coach/chat",
        "/api/plan",
        "/api/plan/{plan_id}",
        "/api/plan/{plan_id}/export.zip",
    }
    missing = expected - paths
    assert not missing, f"Endpoints manquants : {missing}"


def test_morning_metrics_table_initialized(client: TestClient, tmp_path: Path) -> None:
    """Garantit qu'aucune migration n'a été cassée par l'API."""
    db = Path(tmp_path / "api_test.db")
    conn = sqlite3.connect(db)
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert {"activities", "morning_metrics", "conversations"} <= tables
