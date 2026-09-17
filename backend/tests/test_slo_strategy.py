from datetime import date, timedelta

from app.strategy.slo_engine import build_results


TEST_EXPIRY = (date.today() + timedelta(days=14)).isoformat()


def test_slo_strategy_builds_bullish_call_candidate():
    rows = [{
        "symbol": "NIFTYCE",
        "underlying": "NIFTY",
        "instrument_type": "CE",
        "expiry_date": TEST_EXPIRY,
        "strike_price": 25000,
        "ltp": 100,
        "best_bid": 99,
        "best_ask": 101,
        "delta": 0.55,
        "gamma": 0.01,
        "theta": -1.0,
        "vega": 5.0,
        "iv": 15.0,
        "volume": 50000,
        "open_interest": 100000,
        "data_sources": ["GROWW_FEED", "OPTION_CHAIN"],
    }]
    underlyings = [{"underlying": "NIFTY", "activity_score": 90, "direction": "UP"}]

    result = build_results(rows, underlyings)

    assert result["research_only"] is True
    assert result["count"] == 1
    assert result["results"][0]["signal"] == "BUY_CALL"
    assert result["results"][0]["iv"] == 0.15
    assert result["results"][0]["stop_premium"] == 65.0
    assert result["results"][0]["target_premium"] == 150.0


def test_historical_candidate_is_not_capped_by_missing_live_greeks_or_depth():
    rows = [{
        "symbol": "AUROPHARMA26CE1700",
        "underlying": "AUROPHARMA",
        "instrument_type": "CE",
        "expiry_date": TEST_EXPIRY,
        "strike_price": 1700,
        "ltp": 24.35,
        "volume": 1298550,
        "open_interest": 0,
        "data_sources": ["GROWW_HISTORICAL_CANDLES"],
    }]
    underlyings = [{"underlying": "AUROPHARMA", "activity_score": 68.54, "direction": "UP"}]

    result = build_results(rows, underlyings)

    assert result["count"] == 1
    candidate = result["results"][0]
    assert candidate["signal"] == "BUY_CALL"
    assert candidate["status"] == "LIVE"
    assert candidate["total_score"] == 68.54
    assert candidate["liquidity_score"] is None
    assert candidate["theta_score"] is None
    assert candidate["volatility_score"] is None
