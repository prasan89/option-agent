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
    """Score a candidate from evidence that is actually available.

    Historical candles do not provide live bid/ask depth and may not provide
    option greeks. Treating every missing field as a score of 50 artificially
    capped strong historical candidates at ~58 even when directional evidence
    was strong. We now normalize only across available evidence dimensions.
    """
    premium = float(row.get("mid") or row.get("ltp") or 0)
    bid = float(row.get("best_bid") or 0)
    ask = float(row.get("best_ask") or 0)
    has_book = premium > 0 and bid > 0 and ask >= bid
    theta_raw = row.get("theta")
    try:
        theta = abs(float(theta_raw)) if theta_raw is not None else None
    except (TypeError, ValueError):
        theta = None
    iv_raw = row.get("iv")
    try:
        iv = float(iv_raw) if iv_raw is not None else None
    except (TypeError, ValueError):
        iv = None

    direction = _clamp(abs(float(direction_score)))
    components: list[tuple[float, float]] = [(direction, 0.45)]
    if has_book:
        spread_pct = (ask - bid) / premium
        liquidity_score = _clamp(100.0 * (1.0 - spread_pct / 0.10))
        components.append((liquidity_score, 0.20))
    else:
        liquidity_score = None
    if theta is not None:
        theta_ratio = theta / max(premium, 0.01)
        theta_score = _clamp(100.0 * (1.0 - theta_ratio / 0.10))
        components.append((theta_score, 0.20))
    else:
        theta_score = None
    if iv is not None:
        # IV is retained as a neutral research factor until a calibrated
        # volatility regime model is available. Its absence must not penalize
        # a historical candidate.
        volatility_score = 50.0
        components.append((volatility_score, 0.15))
    else:
        volatility_score = None

    total_weight = sum(weight for _, weight in components)
    total = sum(score * weight for score, weight in components) / max(total_weight, 0.01)
    spread_pct = ((ask - bid) / premium) if has_book else None
    return {
        "direction_score": round(direction, 2),
        "liquidity_score": round(liquidity_score, 2) if liquidity_score is not None else None,
        "theta_score": round(theta_score, 2) if theta_score is not None else None,
        "volatility_score": round(volatility_score, 2) if volatility_score is not None else None,
        "total_score": round(total, 2),
        "spread_pct": round(spread_pct * 100.0, 3) if spread_pct is not None else None,
    }


def _direction_map(underlyings: list[dict[str, Any]]) -> dict[str, tuple[str, float]]:
    result: dict[str, tuple[str, float]] = {}
    for item in underlyings:
        name = str(item.get("underlying") or "")
        raw = float(item.get("activity_score") or 0)
        direction = str(item.get("direction") or "FLAT").upper()
        signed = raw if direction == "UP" else -raw if direction == "DOWN" else 0.0
        result[name] = ("BULLISH" if signed > 0 else "BEARISH" if signed < 0 else "NEUTRAL", signed)
    return result


def _group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        underlying = str(row.get("underlying") or "")
        if underlying:
            grouped.setdefault(underlying, []).append(row)
    return grouped


def _premium(row: dict[str, Any]) -> float:
    bid = float(row.get("best_bid") or 0)
    ask = float(row.get("best_ask") or 0)
    if bid > 0 and ask >= bid:
        return (bid + ask) / 2.0
    return float(row.get("ltp") or 0)


def _dte(row: dict[str, Any]) -> int:
    expiry_text = str(row.get("expiry_date") or "")[:10]
    try:
        return (date.fromisoformat(expiry_text) - date.today()).days
    except ValueError:
        return 0


def _make_result(row: dict[str, Any], underlying: str, bias: str, signed_direction: float, scores: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    option_type = str(row.get("instrument_type") or "").upper()
    premium = _premium(row)
    strike = float(row.get("strike_price") or 0)
    breakeven = strike + premium if option_type == "CE" else strike - premium
    return {
        "underlying": underlying,
        "spot": row.get("underlying_spot"),
        "direction": bias,
        "direction_score": round(signed_direction, 2),
        "signal": "BUY_CALL" if option_type == "CE" else "BUY_PUT",
        "symbol": row.get("symbol"),
        "option_type": option_type,
        "strike": strike,
        "expiry": str(row.get("expiry_date") or "")[:10],
        "dte": _dte(row),
        "premium": round(premium, 4),
        "stop_premium": round(premium * 0.65, 4),
        "target_premium": round(premium * 1.80, 4),
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
        "status": status,
        "first_seen_at": None,
        "last_seen_at": None,
        "peak_score": scores["total_score"],
        "reason": reason,
        "research_only": True,
    }


def _current_results(rows: list[dict[str, Any]], underlyings: list[dict[str, Any]], min_score: float) -> tuple[list[dict[str, Any]], dict[str, int]]:
    direction_by_underlying = _direction_map(underlyings)
    grouped = _group_rows(rows)
    diagnostics = {"rows": len(rows), "option_rows": 0, "premium_rows": 0, "dte_rows": 0, "liquid_rows": 0, "direction_rows": 0, "score_rows": 0}
    results: list[dict[str, Any]] = []

    for underlying, candidates in grouped.items():
        bias, signed_direction = direction_by_underlying.get(underlying, ("NEUTRAL", 0.0))
        for row in candidates:
            option_type = str(row.get("instrument_type") or "").upper()
            if option_type not in {"CE", "PE"}:
                continue
            diagnostics["option_rows"] += 1
            premium = _premium(row)
            if premium <= 0:
                continue
            diagnostics["premium_rows"] += 1
            dte = _dte(row)
            if dte < 7 or dte > 30:
                continue
            diagnostics["dte_rows"] += 1
            volume = int(row.get("volume") or 0)
            oi_raw = row.get("open_interest")
            try:
                oi = int(oi_raw) if oi_raw not in (None, "") else None
            except (TypeError, ValueError):
                oi = None
            if volume < 1000:
                continue
            # Historical candle feeds can omit OI and surface it as zero.
            # Zero/None is therefore treated as unavailable rather than as a
            # hard rejection. A known positive OI below the threshold remains
            # a liquidity rejection.
            if oi is not None and oi > 0 and oi < 5000:
                continue
            diagnostics["liquid_rows"] += 1
            if (bias == "BULLISH" and option_type != "CE") or (bias == "BEARISH" and option_type != "PE"):
                continue
            diagnostics["direction_rows"] += 1
            enriched = dict(row)
            enriched["mid"] = premium
            scores = candidate_score(enriched, signed_direction)
            if scores["total_score"] < min_score:
                continue
            diagnostics["score_rows"] += 1
            results.append(_make_result(
                row, underlying, bias, signed_direction, scores, "LIVE",
                "Flow direction agrees with option type; candidate clears the research score and liquidity filters.",
            ))

    results.sort(key=lambda x: (x["total_score"], x["direction_score"]), reverse=True)
    return results, diagnostics


def _fallback_watch(rows: list[dict[str, Any]], underlyings: list[dict[str, Any]], min_score: float) -> list[dict[str, Any]]:
    """Return the best actionable research watch when strict filters produce zero rows.

    This deliberately does not pretend the candidate is fully qualified. It keeps the
    dashboard useful while preserving the strict score/risk gates for actual selection.
    """
    direction_by_underlying = _direction_map(underlyings)
    candidates: list[dict[str, Any]] = []
    for underlying, rows_for_underlying in _group_rows(rows).items():
        bias, signed_direction = direction_by_underlying.get(underlying, ("NEUTRAL", 0.0))
        if bias == "NEUTRAL":
            continue
        for row in rows_for_underlying:
            option_type = str(row.get("instrument_type") or "").upper()
            if option_type not in {"CE", "PE"}:
                continue
            if (bias == "BULLISH" and option_type != "CE") or (bias == "BEARISH" and option_type != "PE"):
                continue
            premium = _premium(row)
            dte = _dte(row)
            if premium <= 0 or dte < 1 or dte > 45:
                continue
            enriched = dict(row)
            enriched["mid"] = premium
            scores = candidate_score(enriched, signed_direction)
            watch_score = min(float(min_score) - 0.1, max(40.0, float(scores["total_score"])))
            scores["total_score"] = round(watch_score, 2)
            candidates.append(_make_result(
                row, underlying, bias, signed_direction, scores, "WATCH",
                "Fallback research watch: no strict SLO opportunity currently clears every filter. This is not a trade-ready signal.",
            ))

    candidates.sort(key=lambda x: (
        x["direction_score"],
        float(x.get("volume") or 0),
        float(x.get("open_interest") or 0),
        x["total_score"],
    ), reverse=True)
    return candidates[:1]


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
    current, diagnostics = _current_results(rows, underlyings, min_score)
    fallback = _fallback_watch(rows, underlyings, min_score) if not current else []
    now = datetime.now(timezone.utc)

    persist_rows = current + fallback
    try:
        signal_store.upsert_opportunities(persist_rows, now=now)
        history = _history_rows(persist_rows)
    except Exception:
        history = []

    results = (current + fallback + history)[:50]
    return {
        "timestamp": rows[0].get("timestamp_ms") if rows else None,
        "count": len(current),
        "history_count": len(history),
        "watch_count": len(fallback),
        "results": results,
        "live_results": current[:50],
        "watch_results": fallback,
        "history": history,
        "diagnostics": diagnostics,
        "method": "SLO_OPTIONS_V2_LIVE_ADAPTER",
        "research_only": True,
        "trading": "DISABLED",
        "note": "Strict SLO opportunities are preserved. If none qualify, one clearly-labelled WATCH candidate is shown instead of silently displaying an empty engine.",
    }
