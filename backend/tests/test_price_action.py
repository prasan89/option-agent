from datetime import datetime
from zoneinfo import ZoneInfo

from app.price_action.scanner import PriceActionScanner

IST = ZoneInfo("Asia/Kolkata")


def _daily_rows():
    rows = []
    for i in range(100):
        close = 100.0 + i * 0.05
        if i >= 96:
            close = 105.0 + (i - 96) * 2.0
        rows.append({
            "ts": str(int(datetime(2026, 9, 14, 15, 30, tzinfo=IST).timestamp()) - (99 - i) * 86400),
            "open": close - 0.2,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 100000.0,
        })
    return rows


def test_epoch_candle_date_and_completed_rows():
    rows = _daily_rows()
    assert PriceActionScanner._candle_date(rows[-1]["ts"]) == "2026-09-14"
    completed = PriceActionScanner._completed_daily_rows(rows, "2026-09-15")
    assert len(completed) == 100


def test_daily_setup_from_completed_candles():
    candidate = PriceActionScanner._daily_setup("TEST", _daily_rows())
    assert candidate is not None
    assert candidate["score"] >= 0
    assert candidate["ema20"] > 0
    assert candidate["ema50"] > 0
    assert candidate["status"] == "SETUP"
