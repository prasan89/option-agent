from datetime import date

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services.groww_client import GrowwClient

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_version() -> None:
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json()["service"] == "option-agent"
    assert response.json()["version"] == "0.3.4"


def test_system_status_keeps_trading_disabled() -> None:
    response = client.get("/system/status")
    assert response.status_code == 200
    assert response.json()["trading"] == "DISABLED"
    assert response.json()["ai_agent"] == "READY"


def test_groww_status_without_credentials() -> None:
    response = client.get("/groww/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["trading"] == "DISABLED"


def test_groww_profile_requires_credentials() -> None:
    response = client.get("/groww/profile")
    assert response.status_code == 503


def test_agent_status() -> None:
    response = client.get("/agent/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "READY"
    assert body["llm_connected"] is False
    assert "get_nifty_option_chain" in body["tools"]


def test_agent_tool_definitions() -> None:
    response = client.get("/agent/tools")
    assert response.status_code == 200
    assert len(response.json()) >= 4


def test_fno_filters_remove_test_and_non_tradable_contracts() -> None:
    df = pd.DataFrame(
        [
            {
                "trading_symbol": "021NSETEST36DECFUT",
                "is_reserved": 0,
                "buy_allowed": 1,
                "sell_allowed": 1,
                "expiry_date": date.today().isoformat(),
            },
            {
                "trading_symbol": "VALID25DECFUT",
                "is_reserved": 0,
                "buy_allowed": 1,
                "sell_allowed": 1,
                "expiry_date": date.today().isoformat(),
            },
            {
                "trading_symbol": "RESERVED25DECFUT",
                "is_reserved": 1,
                "buy_allowed": 1,
                "sell_allowed": 1,
                "expiry_date": date.today().isoformat(),
            },
            {
                "trading_symbol": "DISABLED25DECFUT",
                "is_reserved": 0,
                "buy_allowed": 0,
                "sell_allowed": 0,
                "expiry_date": date.today().isoformat(),
            },
        ]
    )

    quality = GrowwClient._quality_mask(df)
    tradable = GrowwClient._tradable_mask(df)
    active = GrowwClient._active_expiry_mask(df)

    assert quality.tolist() == [False, True, True, True]
    assert tradable.tolist() == [True, True, False, False]
    assert active.tolist() == [True, True, True, True]
