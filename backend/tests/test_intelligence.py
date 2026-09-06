from app.flow.intelligence import FlowIntelligence


def test_chain_analysis_detects_bullish_call_build_up():
    engine = FlowIntelligence()
    previous = {"rows": [
        {"strike": 25000, "option_type": "CE", "oi": 1000, "ltp": 100},
        {"strike": 25000, "option_type": "PE", "oi": 1500, "ltp": 100},
    ]}
    current = {"options": [
        {"strike": 25000, "call": {"oi": 1300, "ltp": 110, "volume": 500},
         "put": {"oi": 1500, "ltp": 98, "volume": 300}},
    ]}
    result = engine.analyze(current, previous)
    assert result["bias"] == "BULLISH"
    assert result["pcr_oi"] is not None
    assert any(r["event"] == "LONG_BUILDUP" for r in result["regimes"])


def test_chain_analysis_detects_bearish_put_buying():
    engine = FlowIntelligence()
    previous = {"rows": [{"strike": 25000, "option_type": "PE", "oi": 1000, "ltp": 100}]}
    current = {"options": [{"strike": 25000, "put": {"oi": 1300, "ltp": 112, "volume": 500}}]}
    result = engine.analyze(current, previous)
    assert result["bias"] == "BEARISH"
    assert result["regimes"][0]["event"] == "PUT_BUYING"
