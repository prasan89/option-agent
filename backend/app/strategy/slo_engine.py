from __future__ import annotations

from datetime import date
from typing import Any


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def candidate_score(row: dict[str, Any], direction_score: float) -> dict[str, float]:
    """SLO Options V1 candidate scoring adapted to option-agent live-feed rows."""
    premium = float(row.get("mid") or row.get("ltp") or 0)
    bid = float(row.get("best_bid") or 0)
    ask = float(row.get("best_ask") or 0)
    spread_pct = ((ask - bid) / premium) if premium > 0 and ask >= bid else 1.0
    theta = abs(float(row.get("theta") or 0))
    theta_ratio = theta / max(premium, 0.01)
    iv = row.get("iv")
    volatility_score = 50.0 if iv is None else _clamp(100.0 - abs(float(iv) - 0.20) * 100.0)
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


def build_results(rows: list[dict[str, Any]], underlyings: list[dict[str, Any]], min_score: float = 65.0) -> dict[str, Any]:
    direction_by_underlying: dict[str, tuple[str, float]] = {}
    for item in underlyings:
        name = str(item.get("underlying") or "")
        raw = float(item.get("activity_score") or 0)
        direction = str(item.get("direction") or "FLAT")
        signed = raw if direction == "UP" else -raw if direction == "DOWN" else 0.0
        direction_by_underlying[name] = ("BULLISH" if signed > 0 else "BEARISH" if signed < 0 else "NEUTRAL", signed)

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
            premium = float(row.get("best_bid") or 0) + float(row.get("best_ask") or 0)
            premium = premium / 2.0
            if premium <= 0:
                premium = float(row.get("ltp") or 0)
            expiry_text = str(row.get("expiry_date") or "")[:10]
            try:
                dte = (date.fromisoformat(expiry_text) - date.today()).days
            except ValueError:
                dte = 0
            # Keep the source strategy's 7-30 day research window when possible.
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
            target = premium * 1.50
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
                "iv": row.get("iv"),
                "volume": row.get("volume"),
                "open_interest": row.get("open_interest"),
                "data_sources": row.get("data_sources") or [],
                **scores,
                "reason": "Flow direction agrees with option type; SLO candidate score clears the V1 threshold.",
                "research_only": True,
            })

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return {
        "timestamp": rows[0].get("timestamp_ms") if rows else None,
        "count": len(results),
        "results": results[:50],
        "method": "SLO_OPTIONS_V1_LIVE_ADAPTER",
        "research_only": True,
        "trading": "DISABLED",
        "note": "SLO scoring is applied to option-agent live-feed/enrichment data. Direction is sourced from the live option-flow activity ranking; this is not a validated trading edge.",
    }
