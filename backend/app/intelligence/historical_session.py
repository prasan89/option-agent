from __future__ import annotations

import logging
import threading
import time as time_module
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.groww_client import groww_client
from app.strategy.slo_engine import build_results

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class HistoricalSessionAnalyzer:
    """Analyze a completed NSE session from Groww historical candles."""

    MARKET_OPEN = time(9, 15)
    MARKET_CLOSE = time(15, 40)
    MIN_DTE = 7
    MAX_DTE = 30
    STRIKES_PER_SIDE = 5
    MAX_CONTRACTS = 250
    MAX_UNDERLYINGS = 50
    QUOTE_ENRICH_LIMIT = 50
    INTER_REQUEST_SECONDS = 0.21

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self._target_date: str | None = None
        self._rows: list[dict[str, Any]] = []
        self._underlyings: list[dict[str, Any]] = []
        self._results: list[dict[str, Any]] = []
        self._selected_contracts = 0
        self._requests = 0
        self._errors = 0
        self._checks = 0
        self._last_error: str | None = None
        self._started_at: str | None = None
        self._completed_at: str | None = None

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"running": self.running, "mode": "POST_MARKET_HISTORICAL", "target_date": self._target_date, "checks": self._checks, "selected_contracts": self._selected_contracts, "contracts": len(self._rows), "underlyings": len(self._underlyings), "opportunities": len(self._results), "requests": self._requests, "errors": self._errors, "last_error": self._last_error, "started_at": self._started_at, "completed_at": self._completed_at, "latest_rows": self._rows[:100], "underlying_rankings": self._underlyings[:50], "results": self._results[:50]}

    @staticmethod
    def _target_session_date(now: datetime | None = None) -> date:
        now = now or datetime.now(IST)
        target = now.date()
        if now.weekday() >= 5 or now.time() < HistoricalSessionAnalyzer.MARKET_OPEN:
            target -= timedelta(days=1)
            while target.weekday() >= 5:
                target -= timedelta(days=1)
        return target

    @classmethod
    def _select_contracts(cls, instruments: list[dict[str, Any]], target: date) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in instruments:
            typ = str(row.get("instrument_type") or "").upper(); underlying = str(row.get("underlying_symbol") or "").strip().upper(); expiry_text = str(row.get("expiry_date") or "")[:10]
            if typ not in {"CE", "PE"} or not underlying or not row.get("exchange_token"): continue
            try: dte = (date.fromisoformat(expiry_text) - target).days
            except ValueError: continue
            if cls.MIN_DTE <= dte <= cls.MAX_DTE: grouped.setdefault(underlying, []).append(row)
        selected: list[dict[str, Any]] = []
        for underlying, contracts in sorted(grouped.items()):
            expiries = sorted({str(r.get("expiry_date"))[:10] for r in contracts})
            if not expiries: continue
            expiry = min(expiries, key=lambda x: abs((date.fromisoformat(x) - target).days - 14))
            expiry_rows = [r for r in contracts if str(r.get("expiry_date"))[:10] == expiry]
            strikes = sorted({float(r.get("strike_price") or 0) for r in expiry_rows if float(r.get("strike_price") or 0) > 0})
            if not strikes: continue
            center = strikes[len(strikes) // 2]
            nearby = sorted(strikes, key=lambda x: (abs(x - center), x))[: cls.STRIKES_PER_SIDE]
            for strike in nearby:
                for typ in ("CE", "PE"):
                    match = next((r for r in expiry_rows if float(r.get("strike_price") or 0) == strike and str(r.get("instrument_type") or "").upper() == typ), None)
                    if match: selected.append(match)
        return selected[: cls.MAX_CONTRACTS]

    @staticmethod
    def _candle_summary(candles: Any) -> dict[str, Any] | None:
        if not isinstance(candles, list): return None
        valid: list[tuple[Any, float, float, float, float, float, float | None]] = []
        for candle in candles:
            if not isinstance(candle, (list, tuple)) or len(candle) < 6: continue
            try: ts, opened, high, low, closed, volume = candle[0], *map(float, candle[1:6])
            except (TypeError, ValueError): continue
            oi = None
            if len(candle) >= 7 and candle[6] not in (None, ""):
                try: oi = float(candle[6])
                except (TypeError, ValueError): pass
            valid.append((ts, opened, high, low, closed, volume, oi))
        if not valid or valid[0][1] == 0: return None
        first, last = valid[0], valid[-1]
        return {"session_open": first[1], "session_close": last[4], "session_high": max(x[2] for x in valid), "session_low": min(x[3] for x in valid), "volume": sum(x[5] for x in valid), "open_interest": last[6] if last[6] is not None else 0.0, "oi_change": last[6] - first[6] if last[6] is not None and first[6] is not None else None, "session_change_pct": (last[4] - first[1]) / abs(first[1]) * 100.0, "last_timestamp": str(last[0])}

    @staticmethod
    def _groww_symbol(row: dict[str, Any]) -> str:
        value = str(row.get("groww_symbol") or "").strip()
        if value: return value
        expiry = date.fromisoformat(str(row.get("expiry_date"))[:10])
        return f"NSE-{str(row.get('underlying_symbol')).upper()}-{expiry.strftime('%d%b%y')}-{float(row.get('strike_price') or 0):g}-{str(row.get('instrument_type')).upper()}"

    @staticmethod
    def _score(change: float) -> float: return round(min(100.0, abs(change) * 20.0), 2)

    def _fetch_contract(self, row: dict[str, Any], target: date) -> dict[str, Any] | None:
        response = groww_client.historical_candles(self._groww_symbol(row), f"{target} 09:15:00", f"{target} 15:40:00", "FNO", "5minute")
        with self._lock: self._requests += 1
        summary = self._candle_summary(response.get("candles") if isinstance(response, dict) else None)
        if not summary: return None
        change = float(summary["session_change_pct"])
        return {"symbol": row.get("trading_symbol"), "groww_symbol": self._groww_symbol(row), "underlying": str(row.get("underlying_symbol") or "").upper(), "instrument_type": str(row.get("instrument_type") or "").upper(), "expiry_date": str(row.get("expiry_date"))[:10], "strike_price": row.get("strike_price"), "exchange_token": row.get("exchange_token"), "lot_size": row.get("lot_size"), "ltp": round(summary["session_close"], 4), "session_open": round(summary["session_open"], 4), "session_high": round(summary["session_high"], 4), "session_low": round(summary["session_low"], 4), "change_pct_since_last_minute": round(change, 4), "session_change_pct": round(change, 4), "activity_score": self._score(change), "direction": "UP" if change > 0 else "DOWN" if change < 0 else "FLAT", "volume": int(summary["volume"]), "open_interest": int(summary["open_interest"]), "oi_change": summary["oi_change"], "timestamp_ms": int(datetime.combine(target, time(15, 40), tzinfo=IST).timestamp() * 1000), "data_sources": ["GROWW_HISTORICAL_CANDLES"], "data_completeness": "HISTORICAL_SESSION_OHLCV_OI", "research_session": target.isoformat()}

    def _fetch_underlying(self, underlying: str, target: date) -> dict[str, Any] | None:
        try:
            response = groww_client.historical_candles(f"NSE-{underlying}", f"{target} 09:15:00", f"{target} 15:40:00", "CASH", "5minute")
            with self._lock: self._requests += 1
            summary = self._candle_summary(response.get("candles") if isinstance(response, dict) else None)
            if not summary: return None
            change = float(summary["session_change_pct"])
            return {"underlying": underlying, "activity_score": self._score(change), "direction": "UP" if change > 0 else "DOWN" if change < 0 else "FLAT", "session_change_pct": round(change, 4), "spot": round(summary["session_close"], 4), "session_open": round(summary["session_open"], 4), "session_high": round(summary["session_high"], 4), "session_low": round(summary["session_low"], 4), "data_sources": ["GROWW_HISTORICAL_UNDERLYING"]}
        except Exception:
            with self._lock: self._errors += 1
            logger.exception("Historical underlying fetch failed for %s", underlying)
            return None

    def _quote_enrich(self, rows: list[dict[str, Any]]) -> None:
        for row in sorted(rows, key=lambda x: float(x.get("activity_score") or 0), reverse=True)[: self.QUOTE_ENRICH_LIMIT]:
            try:
                quote = groww_client.quote(str(row.get("symbol")))
                if isinstance(quote, dict):
                    depth = quote.get("depth") or {}; buy, sell = depth.get("buy") or [], depth.get("sell") or []
                    if buy: row["best_bid"] = buy[0].get("price")
                    if sell: row["best_ask"] = sell[0].get("price")
                    if quote.get("implied_volatility") is not None: row["iv"] = quote["implied_volatility"]
                    sources = set(row.get("data_sources") or []); sources.add("QUOTE"); row["data_sources"] = sorted(sources)
            except Exception:
                with self._lock: self._errors += 1
            time_module.sleep(self.INTER_REQUEST_SECONDS)

    def _run(self, target: date) -> None:
        with self._lock:
            self._running = True; self._target_date = target.isoformat(); self._started_at = datetime.now(IST).isoformat(); self._completed_at = None; self._last_error = None; self._rows = []; self._underlyings = []; self._results = []; self._requests = 0; self._errors = 0; self._checks += 1
        try:
            selected = self._select_contracts(groww_client.fno_instruments(active_only=True), target)
            with self._lock: self._selected_contracts = len(selected)
            rows: list[dict[str, Any]] = []
            for item in selected:
                try:
                    result = self._fetch_contract(item, target)
                    if result: rows.append(result)
                except Exception:
                    with self._lock: self._errors += 1
                    logger.exception("Historical contract fetch failed for %s", item.get("trading_symbol"))
                time_module.sleep(self.INTER_REQUEST_SECONDS)
            underlyings: list[dict[str, Any]] = []
            for name in sorted({str(r.get("underlying") or "") for r in rows if r.get("underlying")})[: self.MAX_UNDERLYINGS]:
                item = self._fetch_underlying(name, target)
                if item: underlyings.append(item)
                time_module.sleep(self.INTER_REQUEST_SECONDS)
            context = {x["underlying"]: x for x in underlyings}
            for row in rows:
                if row.get("underlying") in context:
                    row["underlying_spot"] = context[row["underlying"]]["spot"]; row["underlying_session_change_pct"] = context[row["underlying"]]["session_change_pct"]
            self._quote_enrich(rows)
            result = build_results(rows, underlyings, min_score=65.0)
            for item in result.get("results", []): item["method"] = "SLO_OPTIONS_V1_POST_MARKET_HISTORICAL"
            rows.sort(key=lambda x: float(x.get("activity_score") or 0), reverse=True); underlyings.sort(key=lambda x: float(x.get("activity_score") or 0), reverse=True)
            with self._lock: self._rows = rows; self._underlyings = underlyings; self._results = result.get("results", []); self._completed_at = datetime.now(IST).isoformat()
        except Exception as exc:
            with self._lock: self._last_error = str(exc); self._errors += 1
            logger.exception("Post-market historical analysis failed")
        finally: self._running = False

    def ensure_for_session(self, target: date | None = None) -> dict[str, Any]:
        target = target or self._target_session_date(); target_text = target.isoformat(); should_start = False
        with self._lock:
            if self._target_date == target_text and (self._completed_at or self.running): return_value = True
            elif self._thread is not None and self._thread.is_alive(): return_value = True
            elif not groww_client.configured: return_value = True
            else:
                self._thread = threading.Thread(target=self._run, args=(target,), name="historical-session", daemon=True); self._thread.start(); return_value = False; should_start = True
        return self.stats


historical_session_analyzer = HistoricalSessionAnalyzer()
