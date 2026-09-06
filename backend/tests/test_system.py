from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_version() -> None:
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["service"] == "option-agent"


def test_system_status_has_trading_disabled() -> None:
    response = client.get("/system/status")
    assert response.status_code == 200
    assert response.json()["trading"] == "DISABLED"
