from datetime import datetime

from app.intelligence.historical_session import HistoricalSessionAnalyzer


def test_candle_summary_aggregates_session():
    candles = [
        ["2026-09-08T09:15:00", 100.0, 102.0, 99.0, 101.0, 1000, 5000],
        ["2026-09-08T15:35:00", 110.0, 112.0, 108.0, 110.0, 2000, 5500],
    ]
    result = HistoricalSessionAnalyzer._candle_summary(candles)
    assert result is not None
    assert result["session_open"] == 100.0
    assert result["session_close"] == 110.0
    assert result["session_high"] == 112.0
    assert result["session_low"] == 99.0
    assert result["volume"] == 3000.0
    assert result["open_interest"] == 5500
    assert result["oi_change"] == 500
    assert result["session_change_pct"] == 10.0


def test_target_session_before_open_uses_previous_weekday():
    now = datetime.fromisoformat("2026-09-08T08:30:00+05:30")
    assert HistoricalSessionAnalyzer._target_session_date(now).isoformat() == "2026-09-07"
