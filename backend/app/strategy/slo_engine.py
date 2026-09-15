from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.signals.store import signal_store


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _iv_decimal(value: Any) -> float | None:
    if value is None:
        return None
    try:
        iv = float(value)
    except (TypeError, ValueError):
        return None
    return iv / 100.0 if iv > 1.0 else iv


def candidate_score(row: dict[str, Any], direction_score: float) -> dict[str, float]:
    premium = float(row.get("mid") or row.get("ltp") or 0)
    bid = float(row.get("best_bid") or 0)
    ask = float(row.get("best_ask") or 0)
    spread_pct = ((ask - bid) / premium) if premium > 0 and ask >= bid else 1.0
    theta = abs(float(row.get("theta") or 0))
    theta_ratio = theta / max(premium, 0.01)
    volatility_score = 50.0
    liquidity_score = _clamp(100.0 * (1.0 - spread_pct / 0.10))
    theta_score = _clamp(100.0 * (1.0 - theta_ratio / 0.10))
    direction = _clamp(abs(float(direction_score)))
    total = 0.45 * direction + 0.20 * liquidity_score + 0.20 * theta_score + 0.15 * volatility_score
    return {
        "direction_score": round(direction, 2),
        "liquidity_score": round(liquidity_score, 2),
        "theta_score": round(theta_score, 2),
        "volatility_score": round(volatility_score, 2),
        "total_score": round(total, 2),
        "spread_pct": round(spread_pct * 100.0, 3),
    }


def _current_results(rows: list[dict[str, Any]], underlyings: list[dict[str, Any]], min_score: float) -> list[dict[str, Any]]:
    direction_by_underlying: dict[str, tuple[str, float]] = {}
    for item in underlyings:
        name = str(item.get("underlying") or "")
        raw = float(item.get("activity_score") or 0)
        direction = str(item.get("direction") or "FLAT")
        signed = raw if direction == "UP" else -raw if direction == "DOWN" else 0.0
        direction_by_underlying[name] = (
            "BULLISH" if signed > 0 else "BEARISH" if signed < 0 else "NEUTRAL",
            signed,
        )

    results: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        underlying = str(row.get("underlying") or "")
        if underlying:
            grouped.setdefault(underlying, []).append(row)

    for underlying, candidates in grouped.items():
        bias, signed_direction = direction_by_underlying.get(underlying, ("NEUTRAL", 0.0))
        for row in candidates:
            option_type = str(row.get("instrument_type") or "").upper()
            if option_type not in {"CE", "PE"}:
                continue
            premium = (float(row.get("best_bid") or 0) + float(row.get("best_ask") or 0)) / 2.0
            if premium <= 0:
                premium = float(row.get("ltp") or 0)
            if premium <= 0:
                continue
            expiry_text = str(row.get("expiry_date") or "")[:10]
            try:
                dte = (date.fromisoformat(expiry_text) - date.today()).days
            except ValueError:
                dte = 0
            if dte < 7 or dte > 30:
                continue
            if int(row.get("volume") or 0) < 1000 or int(row.get("open_interest") or 0) < 5000:
                continue
            if bias == "BULLISH" and option_type != "CE":
                continue
            if bias == "BEARISH" and option_type != "PE":
                continue
            enriched = dict(row)
            enriched["mid"] = premium
            scores = candidate_score(enriched, signed_direction)
            if scores["total_score"] < min_score:
                continue
            stop = premium * 0.65
            target = premium * 1.80
            strike = float(row.get("strike_price") or 0)
            breakeven = strike + premium if option_type == "CE" else strike - premium
            results.append({
                "underlying": underlying,
                "spot": row.get("underlying_spot"),
                "direction": bias,
                "direction_score": round(signed_direction, 2),
                "signal": "BUY_CALL" if option_type == "CE" else "BUY_PUT",
                "symbol": row.get("symbol"),
                "option_type": option_type,
                "strike": strike,
                "expiry": expiry_text,
                "dte": dte,
                "premium": round(premium, 4),
                "stop_premium": round(stop, 4),
                "target_premium": round(target, 4),
                "breakeven": round(breakeven, 4),
                "delta": row.get("delta"),
                "gamma": row.get("gamma"),
                "theta": row.get("theta"),
                "vega": row.get("vega"),
                "iv": _iv_decimal(row.get("iv")),
                "volume": row.get("volume"),
                "open_interest": row.get("open_interest"),
                "data_sources": row.get("data_sources") or [],
                **scores,
                "status": "LIVE",
                "first_seen_at": None,
                "last_seen_at": None,
                "peak_score": scores["total_score"],
                "reason": "Flow direction agrees with option type; candidate clears the research score and liquidity filters.",
                "research_only": True,
            })
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results


def _history_rows(current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return today's persisted opportunities, including candidates that just fell below a filter."""
    try:
        history = signal_store.opportunity_history(limit=100)
    except Exception:
        return []
    current_keys = {
        (str(x.get("underlying")), str(x.get("symbol")), str(x.get("option_type")), str(x.get("strike")), str(x.get("expiry")))
        for x in current
    }
    output: list[dict[str, Any]] = []
    for item in history:
        key = (str(item.get("underlying")), str(item.get("symbol")), str(item.get("option_type")), str(item.get("strike")), str(item.get("expiry")))
        if key in current_keys:
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        merged = dict(payload)
        merged.update({
            "underlying": item.get("underlying"),
            "symbol": item.get("symbol"),
            "option_type": item.get("option_type"),
            "strike": item.get("strike"),
            "expiry": item.get("expiry"),
            "direction": item.get("direction"),
            "signal": item.get("signal"),
            "total_score": item.get("score"),
            "peak_score": item.get("peak_score"),
            "premium": item.get("premium"),
            "stop_premium": item.get("stop_premium"),
            "target_premium": item.get("target_premium"),
            "breakeven": item.get("breakeven"),
            "dte": item.get("dte"),
            "delta": item.get("delta"),
            "gamma": item.get("gamma"),
            "theta": item.get("theta"),
            "vega": item.get("vega"),
            "iv": item.get("iv"),
            "volume": item.get("volume"),
            "open_interest": item.get("open_interest"),
            "status": item.get("status") or "HISTORICAL",
            "first_seen_at": item.get("first_seen_at"),
            "last_seen_at": item.get("last_seen_at"),
            "reason": item.get("reason") or "Previously qualified today; it no longer appears in the current live filter set.",
            "research_only": True,
        })
        output.append(merged)
    return output


def build_results(rows: list[dict[str, Any]], underlyings: list[dict[str, Any]], min_score: float = 65.0) -> dict[str, Any]:
    current = _current_results(rows, underlyings, min_score)
    now = datetime.now(timezone.utc)

    # Persist immediately when a candidate qualifies. This is deliberately
    # research-only and does not place or queue any order.
    try:
        signal_store.upsert_opportunities(current, now=now)
        history = _history_rows(current)
    except Exception:
        history = []

    # Keep live candidates at the top. Historical candidates remain visible
    # for the rest of the trading day instead of vanishing on the next refresh.
    results = current + history
    results = results[:50]
    return {
        "timestamp": rows[0].get("timestamp_ms") if rows else None,
        "count": len(current),
        "history_count": len(history),
        "results": results,
        "live_results": current[:50],
        "history": history,
        "method": "SLO_OPTIONS_V1_LIVE_ADAPTER",
        "research_only": True,
        "trading": "DISABLED",
        "note": "Live opportunities are recalculated from current market data. Once a candidate qualifies, it is persisted for today's research history and remains visible even if it later fails a live filter.",
    }
