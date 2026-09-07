from __future__ import annotations

from typing import Any


DEFAULT_MIN_SCORE = 65.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_price_action_signals(
    rows: list[dict[str, Any]],
    underlyings: list[dict[str, Any]],
    min_score: float = DEFAULT_MIN_SCORE,
) -> dict[str, Any]:
    """Apply the SLO Price Action decision/scoring concepts to agent rows.

    The source repository detects classical/harmonic/continuation patterns on
    daily candles, enriches them with EMA20/EMA50, volume and Fibonacci context,
    and arbitrates a dominant BUY/SELL/WATCH plan. Here the live/historical
    option-agent snapshot does not contain the complete daily stock OHLC series,
    so this adapter only promotes signals when equivalent price-action context
    is already present in the input row. It never invents a pattern.
    """
    output: list[dict[str, Any]] = []
    context_by_underlying = {str(x.get("underlying") or "").upper(): x for x in underlyings}

    for row in rows:
        symbol = str(row.get("symbol") or "")
        underlying = str(row.get("underlying") or "").upper()
        pattern = row.get("price_action_pattern") or row.get("pattern")
        signal = str(row.get("price_action_signal") or row.get("signal") or "WATCH").upper()
        score = _num(row.get("price_action_score"), _num(row.get("pattern_score"), 0.0))
        if not pattern or score < min_score or signal not in {"BUY", "SELL"}:
            continue

        context = context_by_underlying.get(underlying, {})
        direction = "BULLISH" if signal == "BUY" else "BEARISH"
        output.append({
            "symbol": symbol,
            "underlying": underlying,
            "direction": direction,
            "signal": signal,
            "pattern": pattern,
            "score": round(score, 2),
            "trigger_state": row.get("trigger_state") or "CONFIRMED",
            "trigger_level": row.get("buy_above") if signal == "BUY" else row.get("sell_below"),
            "price": row.get("ltp") or row.get("close") or row.get("close_5min"),
            "ema20": row.get("ema20"),
            "ema50": row.get("ema50"),
            "volume_ratio": row.get("volume_ratio") or row.get("vol_ratio_5min"),
            "fib_level": row.get("fib_level"),
            "reason": row.get("reason") or row.get("decision_reason") or f"Confirmed {pattern}",
            "underlying_session_change_pct": context.get("session_change_pct"),
            "data_sources": row.get("data_sources") or ["SLO_PRICE_ACTION_ADAPTER"],
            "research_only": True,
        })

    output.sort(key=lambda x: x["score"], reverse=True)
    return {
        "count": len(output),
        "results": output[:50],
        "method": "SLO_PRICE_ACTION_LIVE_ADAPTER",
        "research_only": True,
        "trading": "DISABLED",
        "note": "Signals use SLO Price Action terminology and arbitration fields. The adapter does not claim a price-action pattern unless pattern/scoring fields are available from the source context; complete daily OHLC pattern detection should be fed by a dedicated price-action scanner.",
    }
