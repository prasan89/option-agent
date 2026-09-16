from __future__ import annotations

from statistics import median
from typing import Any


LOOKBACK = 20
BODY_MULTIPLIER = 1.10
CONFIRM_BODY_MULTIPLIER = 0.75


def _body(row: dict[str, Any]) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _signal(underlying: str, candles: list[dict[str, Any]], bullish: bool) -> dict[str, Any]:
    confirm = candles[-1]
    third = candles[-2]
    price = float(confirm["close"])
    extreme = min(float(x["low"]) for x in candles) if bullish else max(float(x["high"]) for x in candles)
    return {
        "underlying": underlying,
        "symbol": underlying,
        "signal": "BUY CALL" if bullish else "BUY PUT",
        "option_action": "BUY CE" if bullish else "BUY PE",
        "direction": "BULLISH" if bullish else "BEARISH",
        "pattern": "JFT 3 BIG RED -> GREEN REVERSAL" if bullish else "JFT 3 BIG GREEN -> RED REVERSAL",
        "trigger": "REVERSAL",
        "trigger_level": round(float(third["high"] if bullish else third["low"]), 4),
        "stop_level": round(extreme, 4),
        "stop_reference": "4-CANDLE EXTREME",
        "pivot": None, "r1": None, "r2": None, "r3": None,
        "s1": None, "s2": None, "s3": None,
        "price": price,
        "close_5min": price,
        "time": str(confirm["ts"]),
        "created_at": str(confirm["ts"]),
        "reason": (
            f"Three consecutive large bearish 5M candles followed by a bullish confirmation "
            f"closing above candle 3 high ({float(third['high']):.2f}); BUY CALL. "
            f"Stop = 4-candle low ({extreme:.2f})."
            if bullish else
            f"Three consecutive large bullish 5M candles followed by a bearish confirmation "
            f"closing below candle 3 low ({float(third['low']):.2f}); BUY PUT. "
            f"Stop = 4-candle high ({extreme:.2f})."
        ),
        "reversal": {
            "setup_candles": 3,
            "confirmation": "close_above_candle_3_high" if bullish else "close_below_candle_3_low",
            "body_multiplier": BODY_MULTIPLIER,
            "confirmation_body_multiplier": CONFIRM_BODY_MULTIPLIER,
        },
        "data_sources": ["GROWW_HISTORICAL_5MIN"],
        "research_only": True,
        "trading": "DISABLED",
    }


def detect_reversals(underlying: str, current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect one bullish and one bearish three-candle reversal per session.

    A bullish event is exactly: three consecutive red candles whose bodies are
    at least 1.10x the median body of the preceding 20 candles, followed by a
    green candle whose body is at least 0.75x that baseline and whose close is
    above candle three's high. The bearish rule is the exact inverse.
    """
    if len(current) < LOOKBACK + 4:
        return []
    results: list[dict[str, Any]] = []
    bull_fired = False
    bear_fired = False
    for i in range(LOOKBACK + 3, len(current)):
        baseline_rows = current[i - LOOKBACK - 3:i - 3]
        setup = current[i - 3:i]
        confirm = current[i]
        baseline = median(_body(x) for x in baseline_rows)
        if baseline <= 0:
            continue
        bodies = [_body(x) for x in setup]
        big_red = all(float(x["close"]) < float(x["open"]) and b >= baseline * BODY_MULTIPLIER for x, b in zip(setup, bodies))
        big_green = all(float(x["close"]) > float(x["open"]) and b >= baseline * BODY_MULTIPLIER for x, b in zip(setup, bodies))
        confirm_body = _body(confirm)
        if not bull_fired and big_red and float(confirm["close"]) > float(confirm["open"]) and float(confirm["close"]) > float(setup[-1]["high"]) and confirm_body >= baseline * CONFIRM_BODY_MULTIPLIER:
            results.append(_signal(underlying, setup + [confirm], True))
            bull_fired = True
        if not bear_fired and big_green and float(confirm["close"]) < float(confirm["open"]) and float(confirm["close"]) < float(setup[-1]["low"]) and confirm_body >= baseline * CONFIRM_BODY_MULTIPLIER:
            results.append(_signal(underlying, setup + [confirm], False))
            bear_fired = True
        if bull_fired and bear_fired:
            break
    return results


def install_reversal_detection(scanner_cls: type) -> None:
    """Attach reversal detection to the existing JFT scanner without changing its lifecycle."""
    original = scanner_cls._signals_for
    if getattr(original, "_jft_reversal_installed", False):
        return

    def combined(cls, underlying: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        signals = list(original(underlying, rows))
        sessions = cls._sessions(rows)
        for current in sessions.values():
            signals.extend(detect_reversals(underlying, current))
        return sorted(signals, key=lambda x: str(x.get("time", "")))

    combined._jft_reversal_installed = True
    scanner_cls._signals_for = classmethod(combined)
