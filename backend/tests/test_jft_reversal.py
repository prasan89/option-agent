from datetime import datetime, timedelta

from app.jft.reversal import detect_reversals


def candle(i, o, h, l, c):
    ts = datetime(2026, 9, 16, 9, 15) + timedelta(minutes=5 * i)
    return {"ts": ts.isoformat() + "+05:30", "open": o, "high": h, "low": l, "close": c, "volume": 1000.0}


def baseline_rows():
    # Non-zero normal bodies so the "big" candles have a measurable baseline.
    return [candle(i, 100.0, 101.0, 99.0, 100.5) for i in range(23)]


def test_three_big_red_then_green_generates_buy_call():
    rows = baseline_rows()
    rows += [
        candle(23, 100.0, 100.5, 96.0, 96.0),
        candle(24, 96.0, 96.5, 92.0, 92.0),
        candle(25, 92.0, 92.5, 88.0, 88.0),
        candle(26, 88.0, 94.0, 87.5, 94.0),
    ]
    signals = detect_reversals("TEST", rows)
    assert len(signals) == 1
    assert signals[0]["signal"] == "BUY CALL"
    assert signals[0]["pattern"] == "JFT 3 BIG RED -> GREEN REVERSAL"
    assert signals[0]["stop_level"] == 87.5


def test_three_big_green_then_red_generates_buy_put():
    rows = baseline_rows()
    rows += [
        candle(23, 100.0, 104.0, 99.5, 104.0),
        candle(24, 104.0, 108.0, 103.5, 108.0),
        candle(25, 108.0, 112.0, 107.5, 112.0),
        candle(26, 112.0, 112.5, 106.0, 106.0),
    ]
    signals = detect_reversals("TEST", rows)
    assert len(signals) == 1
    assert signals[0]["signal"] == "BUY PUT"
    assert signals[0]["pattern"] == "JFT 3 BIG GREEN -> RED REVERSAL"
    assert signals[0]["stop_level"] == 112.5
