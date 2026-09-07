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
    """Build a post-market research snapshot from Groww historical candles.

    This deliberately uses historical OHLCV/OI data instead of pretending that
    the live feed is still running after the exchange closes.
    """

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
        self._checks = 0
        self._requests = 0
        self._errors = 0
        self._last_error: str | None = None
        self._started_at: str | None = None
        self._completed_at: str | None = None

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "mode": "POST_MARKET_HISTORICAL",
                "target_date": self._target_date,
                "checks": self._checks,
                "requests": self._requests,
                "errors": self._errors,
                "last_error": self._last_error,
                "started_at": self._started_at,
                "completed_at": self._completed_at,
                "contracts": len(self._rows),
                "underlyings": len(self._underlyings),
                "opportunities": len(self._results),
                "latest_rows": self._rows[:100],
                "underlying_rankings": self._underlyings[:50],
                "results": self._results[:50],
            }

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
            typ = str(row.get("instrument_type") or "").upper()
            underlying = str(row.get("underlying_symbol") or "").strip().upper()
            expiry_text = str(row.get("expiry_date") or "")[:10]
            token = str(row.get("exchange_token") or "")
            if typ not in {"CE", "PE"} or not underlying or not token:
                continue
            try:
                expiry = date.fromisoformat(expiry_text)
            except ValueError:
                continue
            dte = (expiry - target).days
            if cls.MIN_DTE <= dte <= cls.MAX_DTE:
                grouped.setdefault(underlying, []).append(row)

        selected: list[dict[str, Any]] = []
        for underlying, contracts in sorted(grouped.items()):
            expiries = sorted({str(r.get("expiry_date"))[:10] for r in contracts})
            if not expiries:
                continue
            expiry_text = min(expiries, key=lambda x: abs((date.fromisoformat(x) - target).days - 14))
            expiry_rows = [r for r in contracts if str(r.get("expiry_date"))[:10] == expiry_text]
            strikes = sorted({float(r.get("strike_price") or 0) for r in expiry_rows if float(r.get("strike_price") or 0) > 0})
            if not strikes:
                continue
            center = strikes[len(strikes) // 2]
            nearby = sorted(strikes, key=lambda strike: (abs(strike - center), strike))[: cls.STRIKES_PER_SIDE]
            for strike in nearby:
                for typ in ("CE", "PE"):
                    match = next((r for r in expiry_rows if float(r.get("strike_price") or 0) == strike and str(r.get("instrument_type") or "").upper() == typ), None)
                    if match:
                        selected.append(match)

        return selected[: cls.MAX_CONTRACTS]

    @staticmethod
    def _candle_summary(candles: Any) -> dict[str, float] | None:
        if not isinstance(candles, list):
            return None
        parsed: list[tuple[float, float, float | None, str]] = []
        for candle in candles:
            if not isinstance(candle, (list, tuple)) or len(candle) < 6:
                continue
            try:
                opened = float(candle[1])
                closed = float(candle[4])
                volume = float(candle[5] or 0)
            except (TypeError, ValueError):
                continue
            oi: float | None = None
            if len(candle) >= 7 and candle[6] not in (None, ""):
                try:
                    oi = float(candle[6])
                except (TypeError, ValueError):
                    oi = None
            parsed.append((opened, closed, oi, str(candle[0])))
        if not parsed:
            return None
        first = parsed[0]
        last = parsed[-1]
        if first[0] == 0:
            return None
        return {
            "session_open": first[0],
            "session_close": last[1],
            "session_high": max(float(c[1]) for c in candles if isinstance(c, (list, tuple)) and len(c) >= 3),
            "session_low": min(float(c[3]) for c in candles if isinstance(c, (list, tuple)) and len(c) >= 4),
            "volume": sum(float(c[5] or 0) for c in candles if isinstance(c, (list, tuple)) and len(c) >= 6),
            "open_interest": last[2] if last[2] is not None else 0.0,
            "oi_change": (last[2] - first[2]) if last[2] is not None and first[2] is not None else None,
            "session_change_pct": (last[1] - first[0]) / abs(first[0]) * 100.0,
            "last_timestamp": last[3],
        }

    @staticmethod
    def _groww_symbol(row: dict[str, Any]) -> str:
        value = str(row.get("groww_symbol") or "").strip()
        if value:
            return value
        underlying = str(row.get("underlying_symbol") or "").upper()
        expiry = date.fromisoformat(str(row.get("expiry_date"))[:10])
        strike = float(row.get("strike_price") or 0)
        typ = str(row.get("instrument_type") or "").upper()
        return f"NSE-{underlying}-{expiry.strftime('%d%b%y')}-{strike:g}-{typ}"

    @staticmethod
    def _score(change: float) -> float:
        return round(min(100.0, abs(change) * 20.0), 2)

    def _historical_contract(self, row: dict[str, Any], target: date) -> dict[str, Any] | None:
        symbol = self._groww_symbol(row)
        response = groww_client.historical_candles(
            symbol,
            f"{target.isoformat()} 09:15:00",
            f"{target.isoformat()} 15:40:00",
            segment="FNO",
            candle_interval="5minute",
        )
        summary = self._candle_summary(response.get("candles") if isinstance(response, dict) else None)
        with self._lock:
            self._requests += 1
        if not summary:
            return None
        change = float(summary["session_change_pct"])
        expiry = str(row.get("expiry_date"))[:10]
        return {
            "symbol": row.get("trading_symbol"),
            "groww_symbol": symbol,
            "groww_exchange_symbol": f"NSE_{row.get('trading_symbol')}",
            "underlying": str(row.get("underlying_symbol") or "").upper(),
            "exchange": "NSE",
            "segment": "FNO",
            "instrument_type": str(row.get("instrument_type") or "").upper(),
            "expiry_date": expiry,
            "strike_price": row.get("strike_price"),
            "exchange_token": row.get("exchange_token"),
            "lot_size": row.get("lot_size"),
            "ltp": round(float(summary["session_close"]), 4),
            "session_open": round(float(summary["session_open"]), 4),
            "session_high": round(float(summary["session_high"]), 4),
            "session_low": round(float(summary["session_low"]), 4),
            "change_pct_since_last_minute": round(change, 4),
            "session_change_pct": round(change, 4),
            "activity_score": self._score(change),
            "direction": "UP" if change > 0 else "DOWN" if change < 0 else "FLAT",
            "volume": int(summary["volume"]),
            "open_interest": int(summary["open_interest"]),
            "oi_change_pct": None if summary["oi_change"] is None or not summary["open_interest"] else round(float(summary["oi_change"]) / max(abs(float(summary["open_interest"]) - float(summary["oi_change"])), 1) * 100.0, 4),
            "oi_change": summary["oi_change"],
            "timestamp_ms": int(datetime.fromisoformat(summary["last_timestamp"].replace("Z", "+00:00")).timestamp() * 1000) if "T" in summary["last_timestamp"] else int(datetime.combine(target, time(15, 40), tzinfo=IST).timestamp() * 1000),
            "data_sources": ["GROWW_HISTORICAL_CANDLES"],
            "data_completeness": "HISTORICAL_SESSION_OHLCV_OI",
            "research_session": target.isoformat(),
        }

    def _underlying_snapshot(self, underlying: str, target: date) -> dict[str, Any] | None:
        try:
            response = groww_client.historical_candles(
                f"NSE-{underlying}",
                f"{target.isoformat()} 09:15:00",
                f"{target.isoformat()} 15:40:00",
                segment="CASH",
                candle_interval="5minute",
            )
            summary = self._candle_summary(response.get("candles") if isinstance(response, dict) else None)
            with self._lock:
                self._requests += 1
            if not summary:
                return None
            change = float(summary["session_change_pct"])
            return {
                "underlying": underlying,
                "activity_score": self._score(change),
                "direction": "UP" if change > 0 else "DOWN" if change < 0 else "FLAT",
                "session_change_pct": round(change, 4),
                "spot": round(float(summary["session_close"]), 4),
                "session_open": round(float(summary["session_open"]), 4),
                "session_high": round(float(summary["session_high"]), 4),
                "session_low": round(float(summary["session_low"]), 4),
                "data_sources": ["GROWW_HISTORICAL_UNDERLYING"],
                "data_completeness": "HISTORICAL_SESSION_OHLC",
            }
        except Exception as exc:
            logger.warning("Historical underlying fetch failed for %s: %s", underlying, exc)
            with self._lock:
                self._errors += 1
            return None

    def _enrich_top_contracts(self, rows: list[dict[str, Any]]) -> None:
        for row in sorted(rows, key=lambda r: float(r.get("activity_score") or 0), reverse=True)[: self.QUOTE_ENRICH_LIMIT]:
            try:
                quote = groww_client.quote(str(row.get("symbol")))
                if isinstance(quote, dict):
                    for key in ("open_interest", "volume", "implied_volatility", "average_price", "last_trade_time"):
                        if key in quote:
                            if key == "volume" and int(row.get("volume") or 0) > 0:
                                continue
                            row[key] = quote[key]
                    depth = quote.get("depth") or {}
                    buy = depth.get("buy") or []
                    sell = depth.get("sell") or []
                    if buy:
                        row["best_bid"] = buy[0].get("price")
                    if sell:
                        row["best_ask"] = sell[0].get("price")
                    sources = set(row.get("data_sources") or [])
                    sources.add("QUOTE")
                    row["data_sources"] = sorted(sources)
            except Exception as exc:
                logger.debug("Historical quote enrichment failed for %s: %s", row.get("symbol"), exc)
                with self._lock:
                    self._errors += 1
            time_module.sleep(self.INTER_REQUEST_SECONDS)

    def _run(self, target: date) -> None:
        started = datetime.now(IST).isoformat()
        with self._lock:
            self._running = True
            self._target_date = target.isoformat()
            self._started_at = started
            self._completed_at = None
            self._last_error = None
            self._rows = []
            self._underlyings = []
            self._results = []
            self._checks += 1
        try:
            instruments = groww_client.fno_instruments(active_only=True)
            selected = self._select_contracts(instruments, target)
            rows: list[dict[str, Any]] = []
            for row in selected:
                try:
                    result = self._historical_contract(row, target)
                    if result:
                        rows.append(result)
                except Exception as exc:
                    with self._lock:
                        self._errors += 1
                    logger.warning("Historical contract fetch failed for %s: %s", row.get("trading_symbol"), exc)
                time_module.sleep(self.INTER_REQUEST_SECONDS)

            # Use actual underlying historical candles for direction instead of
            # inferring index direction from option-premium moves.
            underlyings: list[dict[str, Any]] = []
            names = sorted({str(r.get("underlying") or "") for r in rows if r.get("underlying")})[: self.MAX_UNDERLYINGS]
            for name in names:
                item = self._underlying_snapshot(name, target)
                if item:
                    underlyings.append(item)
                time_module.sleep(self.INTER_REQUEST_SECONDS)

            by_underlying = {x["underlying"]: x for x in underlyings}
            for row in rows:
                context = by_underlying.get(str(row.get("underlying") or ""))
                if context:
                    row["underlying_spot"] = context["spot"]
                    row["underlying_session_change_pct"] = context["session_change_pct"]

            self._enrich_top_contracts(rows)
            results = build_results(rows, underlyings, min_score=65.0)
            for result in results.get("results", []):
                result["method"] = "SLO_OPTIONS_V1_POST_MARKET_HISTORICAL"

            underlyings.sort(key=lambda x: float(x.get("activity_score") or 0), reverse=True)
            rows.sort(key=lambda x: float(x.get("activity_score") or 0), reverse=True)
            with self._lock:
                self._rows = rows
                self._underlyings = underlyings
                self._results = results.get("results", [])
                self._completed_at = datetime.now(IST).isoformat()
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
                self._errors += 1
            logger.exception("Post-market historical session analysis failed")
        finally:
            self._running = False

    def ensure_for_session(self, target: date | None = None) -> dict[str, Any]:
        target = target or self._target_session_date()
        target_text = target.isoformat()
        with self._lock:
            already_complete = self._target_date == target_text and self._completed_at is not None
            running = self.running and self._target_date == target_text
        if already_complete or running:
            return self.stats
        if not groww_client.configured:
            return self.stats
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.stats
            self._thread = threading.Thread(target=self._run, args=(target,), name="historical-session", daemon=True)
            self._thread.start()
        return self.stats


historical_session_analyzer = HistoricalSessionAnalyzer()
