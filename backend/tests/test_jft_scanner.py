from app.jft.scanner import JFTScanner


def test_classic_pivot_levels():
    levels = JFTScanner.pivot_levels(110.0, 100.0, 105.0)
    assert levels["pivot"] == 105.0
    assert levels["r2"] == 115.0
    assert levels["r3"] == 120.0
    assert levels["s2"] == 95.0
    assert levels["s3"] == 90.0


def test_r3_close_cross_generates_buy_call_with_r2_stop():
    rows = []
    for i in range(80):
        rows.append({"ts": f"2026-09-{14 if i < 40 else 15:02d}T09:{15 + (i % 10):02d}:00+05:30", "open": 100.0, "high": 110.0, "low": 100.0, "close": 105.0, "volume": 1000.0})
    # Replace the previous session with a valid H/L/C and current session with a crossing candle.
    for row in rows[:40]:
        row.update(high=110.0, low=100.0, close=105.0)
    for row in rows[40:]:
        row.update(high=121.0, low=119.0, close=119.0)
    rows[40]["open"] = 119.0
    rows[40]["close"] = 119.0
    rows[41]["open"] = 119.0
    rows[41]["close"] = 121.0
    signals = JFTScanner._signals_for("TEST", rows)
    assert any(s["signal"] == "BUY CALL" and s["trigger"] == "R3" and s["stop_reference"] == "R2" for s in signals)


def test_s3_close_cross_generates_buy_put_with_s2_stop():
    rows = []
    for i in range(80):
        rows.append({"ts": f"2026-09-{14 if i < 40 else 15:02d}T09:{15 + (i % 10):02d}:00+05:30", "open": 100.0, "high": 110.0, "low": 100.0, "close": 105.0, "volume": 1000.0})
    for row in rows[:40]:
        row.update(high=110.0, low=100.0, close=105.0)
    for row in rows[40:]:
        row.update(high=91.0, low=89.0, close=91.0)
    rows[40]["open"] = 91.0
    rows[40]["close"] = 91.0
    rows[41]["open"] = 91.0
    rows[41]["close"] = 89.0
    signals = JFTScanner._signals_for("TEST", rows)
    assert any(s["signal"] == "BUY PUT" and s["trigger"] == "S3" and s["stop_reference"] == "S2" for s in signals)
