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
    assert response.json()["version"] == "0.2.0"


def test_system_status_keeps_trading_disabled() -> None:
    response = client.get("/system/status")
    assert response.status_code == 200
    assert response.json()["trading"] == "DISABLED"


def test_groww_status_without_credentials() -> None:
    response = client.get("/groww/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["trading"] == "DISABLED"


def test_groww_profile_requires_credentials() -> None:
    response = client.get("/groww/profile")
    assert response.status_code == 503
