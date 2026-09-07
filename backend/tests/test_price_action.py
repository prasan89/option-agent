from app.price_action.scanner import PriceActionScanner


def _daily_breakout_rows():
    rows = []
    for i in range(100):
        close = 100.0 + i * 0.05
        if i >= 96:
            close = 105.0 + (i - 96) * 2.0
        rows.append({"ts": str(i), "open": close - 0.2, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 100000.0})
    return rows


def test_price_action_parser_and_enrichment():
    rows = _daily_breakout_rows()
    patterns = PriceActionScanner._detect_patterns(rows)
    assert isinstance(patterns, list)
    metrics = PriceActionScanner._enrich(rows, 65.0)
    assert metrics["score"] >= 0
    assert metrics["ema20"] > 0
    assert metrics["ema50"] > 0


def test_price_action_trigger_for_breakout():
    pattern = {"name": "BREAKOUT", "direction": "BUY", "points": {"level": 100.0}}
    buy_above, sell_below = PriceActionScanner._trigger(pattern)
    assert buy_above == 100.0
    assert sell_below is None
