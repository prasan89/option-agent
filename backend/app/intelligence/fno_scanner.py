from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.core.event_bus import research_event_bus
from app.services.groww_client import groww_client

logger = logging.getLogger(__name__)


class FNOScanner:
    """Read-only one-minute scanner for the active NSE F&O universe."""

    INTERVAL_SECONDS = 60
    BATCH_SIZE = 50
    MAX_DIAGNOSTIC_REQUESTS = 40
    OUTPUT_TOPIC = "fno.rankings"
    MAX_RESULTS = 100

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = False
        self._latest: list[dict[str, Any]] = []
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
                "checks": self._checks,
                "symbols_available_last_check": self._symbols_available,
                "symbols_scanned_last_check": self._symbols_scanned,
                "quotes_received_last_check": self._quotes_received,
                "successful_batches_last_check": self._successful_batches,
                "failed_batches_last_check": self._failed_batches,
                "invalid_symbols_last_check": self._invalid_symbols,
                "invalid_symbols_cached": len(self._invalid_cache),
                "diagnostic_requests_last_check": self._diagnostic_requests,
                "errors": self._errors,
                "latest_rankings": self._latest[:self.MAX_RESULTS],
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
        return "Groww LTP HTTP 400" in str(exc)

    def _fetch_batch(self, batch: list[str], diagnostic_budget: list[int]) -> tuple[dict[str, Any], int, bool]:
        """Fetch LTPs without unbounded recursive splitting on a bad request."""
        if not batch:
            return {}, 0, True

        try:
            return groww_client.ltp(batch) or {}, 0, True
        except Exception as exc:
            if not self._is_bad_request(exc):
                logger.warning(
                    "Groww F&O LTP batch failed; batch_size=%d sample=%s error=%s",
                    len(batch),
                    ", ".join(batch[:3]),
                    exc,
                )
                return {}, 0, False

            if len(batch) == 1:
                symbol = batch[0]
                logger.warning(
                    "Groww rejected individual F&O symbol; symbol=%s; it will be retried next scan",
                    symbol,
                )
                return {}, 0, False

            if diagnostic_budget[0] <= 0:
                logger.warning(
                    "Groww F&O LTP batch rejected; diagnostic budget exhausted; batch_size=%d sample=%s",
                    len(batch),
                    ", ".join(batch[:3]),
                )
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

    def _scan_once(self) -> None:
        instruments = groww_client.fno_instruments(active_only=True)
        if not instruments:
            raise RuntimeError("Groww NSE F&O instrument master returned no active instruments")

        metas = {self._symbol(row): row for row in instruments if self._symbol(row)}
        with self._lock:
            invalid_cache = set(self._invalid_cache)
        symbols = [symbol for symbol in metas if symbol not in invalid_cache]

        rankings: list[dict[str, Any]] = []
        scanned = 0
        quotes_received = 0
        successful_batches = 0
        failed_batches = 0
        invalid_symbols = 0
        diagnostic_budget = [self.MAX_DIAGNOSTIC_REQUESTS]

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
                symbol = str(returned_symbol)
                canonical = symbol[4:] if symbol.startswith("NSE_") else symbol
                meta = metas.get(canonical)
                price = self._ltp(raw)
                if meta is None or price is None or price <= 0:
                    continue
                previous = self._previous.get(canonical)
                change = None if previous in (None, 0) else (price - previous) / abs(previous) * 100
                rankings.append(
                    {
                        "symbol": canonical,
                        "groww_exchange_symbol": symbol,
                        "underlying": meta.get("underlying_symbol"),
                        "exchange": meta.get("exchange", "NSE"),
                        "instrument_type": meta.get("instrument_type"),
                        "expiry_date": meta.get("expiry_date"),
                        "strike_price": meta.get("strike_price"),
                        "exchange_token": meta.get("exchange_token"),
                        "lot_size": meta.get("lot_size"),
                        "ltp": round(price, 4),
                        "change_pct_since_last_scan": None if change is None else round(change, 4),
                    }
                )
                self._previous[canonical] = price

        for row in rankings:
            change = row["change_pct_since_last_scan"]
            row["activity_score"] = round(min(100, abs(change or 0) * 20), 2)
            row["direction"] = "UP" if (change or 0) > 0 else "DOWN" if (change or 0) < 0 else "FLAT"

        rankings.sort(
            key=lambda x: (x["activity_score"], abs(x["change_pct_since_last_scan"] or 0)),
            reverse=True,
        )
        rankings = rankings[: self.MAX_RESULTS]

        with self._lock:
            check = self._checks + 1
            self._symbols_available = len(metas)
            self._symbols_scanned = scanned
            self._quotes_received = quotes_received
            self._successful_batches = successful_batches
            self._failed_batches = failed_batches
            self._invalid_symbols = invalid_symbols
            self._diagnostic_requests = self.MAX_DIAGNOSTIC_REQUESTS - diagnostic_budget[0]
            self._latest = rankings
            self._checks += 1
            self._errors += invalid_symbols

        if diagnostic_budget[0] < self.MAX_DIAGNOSTIC_REQUESTS:
            logger.warning(
                "Groww F&O LTP diagnostics used %d extra requests; invalid_cached=%d",
                self.MAX_DIAGNOSTIC_REQUESTS - diagnostic_budget[0],
                len(self._invalid_cache),
            )

        research_event_bus.publish(
            self.OUTPUT_TOPIC,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "check": check,
                "symbols_available": len(metas),
                "symbols_scanned": scanned,
                "quotes_received": quotes_received,
                "successful_batches": successful_batches,
                "failed_batches": failed_batches,
                "invalid_symbols": invalid_symbols,
                "rankings": rankings,
            },
        )

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
