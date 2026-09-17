from datetime import datetime, timezone

from app.signals.monitor import SignalMonitor


def test_signal_time_prefers_timestamp_ms():
    raw = {"timestamp_ms": 1789213500000}
    result = SignalMonitor._signal_time(raw)
    assert result == datetime.fromtimestamp(1789213500000 / 1000, tz=timezone.utc)


def test_candidate_persists_signal_generated_at():
    raw = {
        "symbol": "NSE-TEST-CE",
        "timestamp_ms": 1789213500000,
        "intelligence_score": 75,
        "confidence": "HIGH",
        "instrument_type": "CE",
    }
    result = SignalMonitor._candidate(raw)
    assert result is not None
    assert result["created_at"] == datetime.fromtimestamp(1789213500000 / 1000, tz=timezone.utc)
    assert result["signal_generated_at"] == result["created_at"].isoformat()
