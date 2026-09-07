from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from app.core.event_bus import research_event_bus
from app.services.groww_client import groww_client

logger = logging.getLogger(__name__)


class FNOScanner:
    """Read-only one-minute scanner for the near-term NSE F&O universe."""

    INTERVAL_SECONDS = 60
    BATCH_SIZE = 50
    MAX_DIAGNOSTIC_REQUESTS = 40
    OUTPUT_TOPIC = "fno.rankings"
    MAX_RESULTS = 100
    MAX_UNDERLYING_RESULTS = 50
    EXPIRY_MONTHS_AHEAD = 2

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = False
        self._latest: list[dict[str, Any]] = []
        self._latest_underlyings: list[dict[str, Any]] = []
        self._checks = 0
        self._symbols_scanned = 0
        self._symbols_available = 0
        self._quotes_received = 0
        self._successful_batches = 0
        self._failed_batches = 0
        self._invalid_symbols = 0
        self._errors = 0
        self._diagnostic_requests = 0
        self._previous: dict[str, float] = {}
        self._invalid_cache: set[str] = set()
        self._last_timestamp: str | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "interval_seconds": self.INTERVAL_SECONDS,
                "expiry_months_ahead": self.EXPIRY_MONTHS_AHEAD,
                "checks": self._checks,
                "ranking_ready": self._checks >= 2,
                "symbols_available_last_check": self._symbols_available,
                "symbols_scanned_last_check": self._symbols_scanned,
                "quotes_received_last_check": self._quotes_received,
                "successful_batches_last_check": self._successful_batches,
                "failed_batches_last_check": self._failed_batches,
                "invalid_symbols_last_check": self._invalid_symbols,
                "invalid_symbols_cached": len(self._invalid_cache),
                "diagnostic_requests_last_check": self._diagnostic_requests,
                "unique_underlyings_last_check": len({str(x.get("underlying") or "") for x in self._latest if x.get("underlying")}),
                "errors": self._errors,
                "last_scan_timestamp": self._last_timestamp,
                "top_underlyings": self._latest_underlyings[: self.MAX_UNDERLYING_RESULTS],
                "latest_rankings": self._latest[: self.MAX_RESULTS],
            }

    @staticmethod
    def _ltp(value: Any) -> float | None:
        if isinstance(value, dict):
            for key in ("ltp", "last_price", "price", "value"):
                if key in value:
                    return FNOScanner._ltp(value[key])
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _symbol(meta: dict[str, Any]) -> str:
        return str(meta.get("trading_symbol") or "").strip()

    @staticmethod
    def _is_bad_request(exc: Exception) -> bool:
        text = str(exc)
        return "Groww LTP HTTP 400" in text and ("GA001" in text or "Bad Request" in text)

    @classmethod
    def _expiry_allowed(cls, value: Any, today: date | None = None) -> bool:
        today = today or date.today()
        raw = str(value or "").strip()[:10]
        try:
            expiry = date.fromisoformat(raw)
        except ValueError:
            return False
        start_index = today.year * 12 + today.month - 1
        expiry_index = expiry.year * 12 + expiry.month - 1
        return start_index <= expiry_index <= start_index + cls.EXPIRY_MONTHS_AHEAD

    @staticmethod
    def _quality_allowed(symbol: str) -> bool:
        value = symbol.upper()
        return bool(value) and "NSETEST" not in value and not value.startswith("NIFTYFPI")

    def _fetch_batch(self, batch: list[str], diagnostic_budget: list[int]) -> tuple[dict[str, Any], int, bool]:
        if not batch:
            return {}, 0, True
        try:
            return groww_client.ltp(batch) or {}, 0, True
        except Exception as exc:
            if not self._is_bad_request(exc):
                logger.warning("Groww F&O LTP batch failed; batch_size=%d sample=%s error=%s", len(batch), ", ".join(batch[:3]), exc)
                return {}, 0, False
            if len(batch) == 1:
                with self._lock:
                    self._invalid_cache.add(batch[0])
                return {}, 1, False
            if diagnostic_budget[0] <= 0:
                return {}, 0, False
            midpoint = len(batch) // 2
            diagnostic_budget[0] -= 1
            left_quotes, left_invalid, left_ok = self._fetch_batch(batch[:midpoint], diagnostic_budget)
            if diagnostic_budget[0] <= 0:
                right_quotes, right_invalid, right_ok = {}, 0, False
            else:
                diagnostic_budget[0] -= 1
                right_quotes, right_invalid, right_ok = self._fetch_batch(batch[midpoint:], diagnostic_budget)
            left_quotes.update(right_quotes)
            return left_quotes, left_invalid + right_invalid, left_ok or right_ok

    @staticmethod
    def _contract_score(change: float | None) -> float:
        """Price-only score until depth/volume/OI are available for the full universe."""
        return round(min(100.0, abs(change or 0.0) * 20.0), 2)

    @classmethod
    def _aggregate_underlyings(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            underlying = str(row.get("underlying") or "").strip()
            if underlying:
                grouped[underlying].append(row)
        output: list[dict[str, Any]] = []
        for underlying, contracts in grouped.items():
            active = [r for r in contracts if r.get("change_pct_since_last_scan") is not None]
            if not active:
                continue
            ordered = sorted(active, key=lambda r: abs(float(r.get("change_pct_since_last_scan") or 0)), reverse=True)
            top = ordered[:5]
            top_abs = [abs(float(r.get("change_pct_since_last_scan") or 0)) for r in top]
            weighted = sum(top_abs) / len(top_abs) if top_abs else 0.0
            signed = sum(float(r.get("change_pct_since_last_scan") or 0) for r in top)
            score = min(100.0, weighted * 35.0 + min(len(active), 20) * 0.75)
            up = sum(1 for r in active if float(r.get("change_pct_since_last_scan") or 0) > 0)
            down = sum(1 for r in active if float(r.get("change_pct_since_last_scan") or 0) < 0)
            output.append({
                "underlying": underlying,
                "activity_score": round(score, 2),
                "direction": "UP" if signed > 0 else "DOWN" if signed < 0 else "FLAT",
                "contracts_active": len(active),
                "contracts_up": up,
                "contracts_down": down,
                "max_change_pct": round(float(top[0].get("change_pct_since_last_scan") or 0), 4),
                "top_contracts": [
                    {"symbol": r["symbol"], "change_pct": r["change_pct_since_last_scan"], "instrument_type": r.get("instrument_type"), "expiry_date": r.get("expiry_date"), "strike_price": r.get("strike_price"), "ltp": r.get("ltp"), "activity_score": r.get("activity_score")} for r in top
                ],
                "data_sources": ["LTP"],
                "data_completeness": "PRICE_ONLY",
            })
        output.sort(key=lambda x: (x["activity_score"], abs(x["max_change_pct"])), reverse=True)
        return output[: cls.MAX_UNDERLYING_RESULTS]

    def _scan_once(self) -> None:
        instruments = groww_client.fno_instruments(active_only=True)
        if not instruments:
            raise RuntimeError("Groww NSE F&O instrument master returned no active instruments")
        metas: dict[str, dict[str, Any]] = {}
        for row in instruments:
            symbol = self._symbol(row)
            if not symbol or not self._quality_allowed(symbol) or not self._expiry_allowed(row.get("expiry_date")):
                continue
            metas[symbol] = row
        if not metas:
            raise RuntimeError("Groww NSE F&O universe has no contracts inside the near-term expiry window")

        with self._lock:
            invalid_cache = set(self._invalid_cache)
        symbols = [symbol for symbol in metas if symbol not in invalid_cache]
        rankings: list[dict[str, Any]] = []
        scanned = quotes_received = successful_batches = failed_batches = invalid_symbols = 0
        diagnostic_budget = [self.MAX_DIAGNOSTIC_REQUESTS]
        scan_timestamp = datetime.now(timezone.utc).isoformat()

        for start in range(0, len(symbols), self.BATCH_SIZE):
            batch = symbols[start : start + self.BATCH_SIZE]
            quotes, invalid, ok = self._fetch_batch(batch, diagnostic_budget)
            scanned += len(batch)
            invalid_symbols += invalid
            if quotes:
                successful_batches += 1
                quotes_received += len(quotes)
            if not ok and not quotes:
                failed_batches += 1
            for returned_symbol, raw in quotes.items():
                exchange_symbol = str(returned_symbol)
                canonical = exchange_symbol[4:] if exchange_symbol.startswith("NSE_") else exchange_symbol
                meta = metas.get(canonical)
                price = self._ltp(raw)
                if meta is None or price is None or price <= 0:
                    continue
                previous = self._previous.get(canonical)
                change = None if previous in (None, 0) else (price - previous) / abs(previous) * 100.0
                self._previous[canonical] = price
                rankings.append({
                    "symbol": canonical,
                    "groww_exchange_symbol": exchange_symbol,
                    "underlying": meta.get("underlying_symbol"),
                    "exchange": meta.get("exchange", "NSE"),
                    "segment": meta.get("segment", "FNO"),
                    "instrument_type": meta.get("instrument_type"),
                    "expiry_date": meta.get("expiry_date"),
                    "strike_price": meta.get("strike_price"),
                    "exchange_token": meta.get("exchange_token"),
                    "lot_size": meta.get("lot_size"),
                    "ltp": round(price, 4),
                    "change_pct_since_last_scan": None if change is None else round(change, 4),
                    "data_completeness": "PRICE_ONLY",
                })

        for row in rankings:
            change = row["change_pct_since_last_scan"]
            row["activity_score"] = self._contract_score(change)
            row["direction"] = "UP" if (change or 0) > 0 else "DOWN" if (change or 0) < 0 else "FLAT"

        ranking_ready = self._checks >= 1
        if ranking_ready:
            rankings.sort(key=lambda x: (x["activity_score"], abs(x["change_pct_since_last_scan"] or 0)), reverse=True)
        else:
            rankings = []
        underlying_rankings = self._aggregate_underlyings(rankings if ranking_ready else [])
        top_contracts = rankings[: self.MAX_RESULTS]

        with self._lock:
            check = self._checks + 1
            self._symbols_available = len(metas)
            self._symbols_scanned = scanned
            self._quotes_received = quotes_received
            self._successful_batches = successful_batches
            self._failed_batches = failed_batches
            self._invalid_symbols = invalid_symbols
            self._diagnostic_requests = self.MAX_DIAGNOSTIC_REQUESTS - diagnostic_budget[0]
            self._latest = top_contracts
            self._latest_underlyings = underlying_rankings
            self._checks += 1
            self._last_timestamp = scan_timestamp
            self._errors += invalid_symbols

        research_event_bus.publish(self.OUTPUT_TOPIC, {
            "timestamp": scan_timestamp,
            "check": check,
            "ranking_ready": ranking_ready,
            "symbols_available": len(metas),
            "symbols_scanned": scanned,
            "quotes_received": quotes_received,
            "successful_batches": successful_batches,
            "failed_batches": failed_batches,
            "invalid_symbols": invalid_symbols,
            "unique_underlyings": len({str(r.get("underlying") or "") for r in rankings if r.get("underlying")}),
            "observations": rankings,
            "rankings": top_contracts,
            "underlying_rankings": underlying_rankings,
        })

    def _run(self) -> None:
        while self._running:
            started = time.monotonic()
            try:
                self._scan_once()
            except Exception:
                with self._lock:
                    self._errors += 1
                logger.exception("F&O scanner iteration failed")
            wait = max(0, self.INTERVAL_SECONDS - (time.monotonic() - started))
            end = time.monotonic() + wait
            while self._running and time.monotonic() < end:
                time.sleep(min(0.5, end - time.monotonic()))
        self._running = False

    def start(self) -> None:
        if self.running:
            raise RuntimeError("F&O scanner is already running")
        if not groww_client.configured:
            raise RuntimeError("Groww credentials are not configured")
        self._running = True
        self._thread = threading.Thread(target=self._run, name="fno-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False


fno_scanner = FNOScanner()
