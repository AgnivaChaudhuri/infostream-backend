from __future__ import annotations

from fastapi.testclient import TestClient


def test_invalid_schedule_rejected(client: TestClient) -> None:
    response = client.put(
        "/digest/schedule",
        json={
            "schedule": "not-a-time",
            "run_every": True,
            "spacing": 300,
        },
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_SCHEDULE"


def test_valid_schedule_saved(client: TestClient) -> None:
    response = client.put(
        "/digest/schedule",
        json={
            "schedule": "2026-03-02T12:00:00Z",
            "run_every": True,
            "spacing": 3600,
        },
    )
    assert response.status_code == 200
    settings = response.json()["data"]
    assert settings["run_every"] is True
    assert settings["spacing"] == 3600
