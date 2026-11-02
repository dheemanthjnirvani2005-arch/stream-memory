from fastapi.testclient import TestClient

from app.main import app
from app.services import health


def test_health_reports_database_ok(monkeypatch) -> None:
    monkeypatch.setattr(health, "check_database_health", lambda: True)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_degrades_when_database_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(health, "check_database_health", lambda: False)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_health_contains_database_exceptions(monkeypatch) -> None:
    def fail() -> bool:
        raise RuntimeError("database down")

    monkeypatch.setattr(health, "check_database_health", fail)
    response = TestClient(app).get("/health")
    assert response.json()["status"] == "degraded"
