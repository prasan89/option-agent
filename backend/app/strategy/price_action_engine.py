from __future__ import annotations

from datetime import date
from typing import Any

from app.services.groww_client import groww_client

DEFAULT_MIN_SCORE = 65.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_price_action_fields(row: dict[str, Any]) -> dict[str, Any] | None:
    pattern = row.get("price_action_pattern") or row.get("pattern")
    signal = str(row.get("price_action_signal") or row.get("signal") or "WATCH").upper()
    score = _num(row.get("price_action_score"), _num(row.get("pattern_score"), 0.0))
    if not pattern or score < DEFAULT_MIN_SCORE or signal not in {"BUY", "SELL"}:
        return None
    return {"pattern": pattern, "signal": signal, "score": score}


def build_price_action_signals(rows: list[dict[str, Any]], underlyings: list[dict[str, Any]], min_score: float = DEFAULT_MIN_SCORE) -> dict[str, Any]:
    """Build dashboard signals from fields produced by a price-action scanner."""
    output: list[dict[str, Any]] = []
    context_by_underlying = {str(x.get("underlying") or "").upper(): x for x in underlyings}
    for row in rows:
        fields = _safe_price_action_fields(row)
        if not fields or fields["score"] < min_score:
            continue
        underlying = str(row.get("underlying") or "").upper()
        context = context_by_underlying.get(underlying, {})
        signal = fields["signal"]
        output.append({
            "symbol": row.get("symbol"),
            "underlying": underlying,
            "direction": "BULLISH" if signal == "BUY" else "BEARISH",
            "signal": signal,
            "pattern": fields["pattern"],
            "score": round(fields["score"], 2),
            "status": row.get("status") or "CONFIRMED",
            "trigger_state": row.get("trigger_state") or "CONFIRMED",
            "trigger_level": row.get("buy_above") if signal == "BUY" else row.get("sell_below"),
            "price": row.get("price") or row.get("close_5min") or row.get("close") or row.get("ltp"),
            "ema20": row.get("ema20"),
            "ema50": row.get("ema50"),
            "volume_ratio": row.get("volume_ratio") or row.get("vol_ratio_5min"),
            "fib_level": row.get("fib_level"),
            "reason": row.get("reason") or row.get("decision_reason") or f"Confirmed {fields['pattern']}",
            "underlying_session_change_pct": context.get("session_change_pct"),
            "data_sources": row.get("data_sources") or ["SLO_PRICE_ACTION"],
            "research_only": True,
        })
    output.sort(key=lambda x: x["score"], reverse=True)
    return {
        "count": len(output),
        "results": output[:50],
        "method": "SLO_PRICE_ACTION_ADAPTER",
        "research_only": True,
        "trading": "DISABLED",
        "note": "The reference SLO Price Action project detects daily price-action patterns, enriches them with EMA20/EMA50, volume and Fibonacci context, and confirms levels with 5-minute candles. option-agent displays those derived signals without inventing patterns from option prices.",
    }


def build_from_candles(df: Any, symbol: str, min_score: float = DEFAULT_MIN_SCORE) -> list[dict[str, Any]]:
    """Run the core SLO-style pattern engine directly on a pandas-like OHLCV frame."""
    if df is None or len(df) < 80:
        return []
    frame = df.copy().sort_values("date").reset_index(drop=True)
    high = frame.high.astype(float).tolist()
    low = frame.low.astype(float).tolist()
    close = frame.close.astype(float)
    volume = frame.volume.astype(float)
    window = 3
    pivots: list[tuple[int, float, str]] = []
    for i in range(window, len(frame) - window):
        if high[i] >= max(high[i-window:i+window+1]): pivots.append((i, high[i], "H"))
        if low[i] <= min(low[i-window:i+window+1]): pivots.append((i, low[i], "L"))
    clean: list[tuple[int, float, str]] = []
    for p in sorted(pivots):
        if clean and clean[-1][2] == p[2]:
            if (p[2] == "H" and p[1] > clean[-1][1]) or (p[2] == "L" and p[1] < clean[-1][1]): clean[-1] = p
        else: clean.append(p)
    patterns: list[dict[str, Any]] = []
    last = float(close.iloc[-1])
    avg20 = float(volume.tail(20).mean())
    vol_ratio = float(volume.iloc[-1] / avg20) if avg20 else 0.0
    if len(clean) >= 2:
        point = clean[-2]
        if point[2] == "H" and last > point[1]:
            raw = min(90.0, 65.0 + (10 if vol_ratio >= 1.5 else 0) + (5 if abs(last-point[1])/max(abs(point[1]),1e-9) <= .02 else 0))
            patterns.append({"pattern": "BREAKOUT", "signal": "BUY", "score": raw, "trigger": point[1], "status": "CONFIRMED"})
        if point[2] == "L" and last < point[1]:
            raw = min(90.0, 65.0 + (10 if vol_ratio >= 1.5 else 0) + (5 if abs(last-point[1])/max(abs(point[1]),1e-9) <= .02 else 0))
            patterns.append({"pattern": "BREAKDOWN", "signal": "SELL", "score": raw, "trigger": point[1], "status": "CONFIRMED"})
    if len(clean) >= 5:
        a,b,c,d,e = clean[-5:]
        kinds = [p[2] for p in (a,b,c,d,e)]
        if kinds == ["H","L","H","L","H"] and c[1] > a[1] and c[1] > e[1] and abs(a[1]-e[1])/max(abs(c[1]),1e-9) <= .06:
            neckline = (b[1]+d[1])/2
            if last <= neckline: patterns.append({"pattern":"HEAD AND SHOULDERS","signal":"SELL","score":78.0,"trigger":neckline,"status":"CONFIRMED"})
        if kinds == ["L","H","L","H","L"] and c[1] < a[1] and c[1] < e[1] and abs(a[1]-e[1])/max(abs(c[1]),1e-9) <= .06:
            neckline = (b[1]+d[1])/2
            if last >= neckline: patterns.append({"pattern":"INVERSE HEAD AND SHOULDERS","signal":"BUY","score":78.0,"trigger":neckline,"status":"CONFIRMED"})
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
    trend_bonus = 20.0
    fib_level = None
    recent = frame.tail(50)
    span = float(recent.high.max()) - float(recent.low.min())
    if span > 0:
        ratio = (last - float(recent.low.min())) / span
        fib_level = min((0.382,0.5,0.618,0.786), key=lambda x: abs(x-ratio))
    results=[]
    for p in patterns:
        score = min(100.0, p["score"]*0.55 + trend_bonus + min(15.0,max(0.0,vol_ratio*10.0)) + 8.0)
        if score < min_score: continue
        results.append({"symbol":symbol,"signal":p["signal"],"direction":"BULLISH" if p["signal"]=="BUY" else "BEARISH","pattern":p["pattern"],"score":round(score,2),"status":p["status"],"trigger_state":"TRIGGERED TODAY","trigger_level":p["trigger"],"price":last,"ema20":round(ema20,2),"ema50":round(ema50,2),"volume_ratio":round(vol_ratio,2),"fib_level":fib_level,"reason":f"Confirmed {p['pattern']}; enriched SLO-style score cleared {min_score:.0f}.","research_only":True})
    return sorted(results,key=lambda x:x["score"],reverse=True)
