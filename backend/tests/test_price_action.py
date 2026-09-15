from datetime import datetime
from zoneinfo import ZoneInfo

from app.price_action.patterns import PriceActionPatternDetector
from app.price_action.scanner import PriceActionScanner

IST = ZoneInfo("Asia/Kolkata")


def _rows():
    rows = []
    base = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    for i in range(100):
        close = 100.0 + (0.01 * (i % 5))
        high = close + 0.4
        low = close - 0.4
        volume = 100_000.0
        if i == 99:
            close = 102.0
            high = 102.2
            low = 101.7
            volume = 300_000.0
        rows.append({
            "ts": str(int(base.timestamp() + i * 300)),
            "open": close - 0.1,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return rows


def _ohlcv(values):
    base = datetime(2026, 9, 15, 9, 15, tzinfo=IST)
    return [{
        "ts": str(int(base.timestamp() + i * 300)),
        "open": v - 0.1,
        "high": v + 0.2,
        "low": v - 0.2,
        "close": v,
        "volume": 100_000.0,
    } for i, v in enumerate(values)]


def test_epoch_candle_date():
    ts = str(int(datetime(2026, 9, 15, 10, 0, tzinfo=IST).timestamp()))
    assert PriceActionScanner._candle_date(ts) == "2026-09-15"


def test_historical_five_minute_breakout_is_detected():
    rows = _rows()
    signals = PriceActionScanner._historical_signals("TEST", rows)
    assert signals
    signal = signals[-1]
    assert signal["status"] == "CONFIRMED"
    assert signal["signal"] == "BUY"
    assert signal["pattern"] == "5M RANGE BREAKOUT"
    assert signal["data_sources"] == ["GROWW_HISTORICAL_5MIN"]


def test_latest_setup_can_be_built_from_historical_five_minute_data():
    rows = _rows()
    setup = PriceActionScanner._latest_setup("TEST", rows)
    assert setup is not None
    assert setup["status"] == "SETUP"
    assert setup["trigger_state"] == "WAITING_5M_CONFIRMATION"


def test_detector_range_breakout():
    rows = _ohlcv([100 + (i % 3) * 0.1 for i in range(80)] + [102.0])
    found = PriceActionPatternDetector.detect(rows, len(rows) - 1)
    assert any(x["pattern"] == "5M RANGE BREAKOUT" and x["signal"] == "BUY" for x in found)


def test_detector_exposes_all_supported_pattern_families():
    supported = PriceActionScanner.PATTERN_NAMES
    assert "5M RANGE BREAKOUT" in supported
    assert "5M RANGE BREAKDOWN" in supported
    assert "HEAD & SHOULDERS" in supported
    assert "INVERSE HEAD & SHOULDERS" in supported
    assert "TRIANGLE BREAKOUT" in supported
    assert "TRIANGLE BREAKDOWN" in supported
    assert "RISING WEDGE BREAKOUT" in supported
    assert "FALLING WEDGE BREAKDOWN" in supported
    assert "BULL FLAG BREAKOUT" in supported
    assert "BEAR FLAG BREAKDOWN" in supported
    assert "ROUNDING BOTTOM BREAKOUT" in supported
    assert "ROUNDING TOP BREAKDOWN" in supported


def test_detector_is_safe_on_short_and_flat_data():
    rows = _ohlcv([100.0] * 20)
    assert PriceActionPatternDetector.detect(rows, len(rows) - 1) == []
    assert PriceActionPatternDetector.setup_candidates(rows, len(rows) - 1) == []


def test_detector_candidate_shape():
    rows = _ohlcv([100 + (0.01 * (i % 5)) for i in range(100)] + [102.0])
    found = PriceActionPatternDetector.detect(rows, len(rows) - 1)
    assert found
    candidate = found[0]
    assert {"pattern", "signal", "trigger_level", "quality", "detail"}.issubset(candidate)
