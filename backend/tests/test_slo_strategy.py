from app.strategy.slo_engine import build_results


def test_slo_strategy_builds_bullish_call_candidate():
    rows = [{
        "symbol": "NIFTYCE",
        "underlying": "NIFTY",
        "instrument_type": "CE",
        "expiry_date": "2099-01-20",
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
