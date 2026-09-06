from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import settings
from app.services.groww_client import groww_client

logger = logging.getLogger(__name__)


class FNOScanner:
    """Read-only one-minute scanner and activity ranker for NSE F&O."""

    INTERVAL_SECONDS = 60
    BATCH_SIZE = 50
    OUTPUT_STREAM = "fno:rankings"
    MAX_RESULTS = 100

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = False
        self._latest: list[dict[str, Any]] = []
        self._checks = 0
        self._symbols_scanned = 0
        self._errors = 0
        self._previous: dict[str, float] = {}
        self._lock = threading.Lock()
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

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
                "symbols_scanned_last_check": self._symbols_scanned,
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
        return str(meta.get("groww_symbol") or meta.get("trading_symbol") or "").strip()

    def _scan_once(self) -> None:
        # Phase 3 scans the complete F&O master, not NIFTY only.
        instruments = groww_client.fno_instruments(active_only=True)
        if not instruments:
            raise RuntimeError("Groww NSE F&O instrument master returned no active instruments")

        metas: dict[str, dict[str, Any]] = {}
        for row in instruments:
            symbol = self._symbol(row)
            if symbol and symbol not in metas:
                metas[symbol] = row
        symbols = list(metas)

        rankings: list[dict[str, Any]] = []
        scanned = 0
        for start in range(0, len(symbols), self.BATCH_SIZE):
            batch = symbols[start:start + self.BATCH_SIZE]
            try:
                quotes = groww_client.ltp(batch) or {}
                scanned += len(batch)
                for symbol, raw in quotes.items():
                    price = self._ltp(raw)
                    if price is None or price <= 0:
                        continue
                    previous = self._previous.get(symbol)
                    change_pct = None if previous in (None, 0) else (price - previous) / abs(previous) * 100
                    rankings.append({
                        "symbol": symbol,
                        "underlying": metas.get(symbol, {}).get("underlying_symbol"),
                        "instrument_type": metas.get(symbol, {}).get("instrument_type"),
                        "expiry_date": metas.get(symbol, {}).get("expiry_date"),
                        "strike_price": metas.get(symbol, {}).get("strike_price"),
                        "ltp": round(price, 4),
                        "change_pct_since_last_scan": None if change_pct is None else round(change_pct, 4),
                    })
                    self._previous[symbol] = price
            except Exception:
                self._errors += 1
                logger.exception("F&O LTP batch failed")

        # Rank by absolute one-minute price movement. The first run is a
        # baseline; later runs provide the actionable activity ranking.
        for row in rankings:
            change = row["change_pct_since_last_scan"]
            row["activity_score"] = round(min(100.0, abs(change or 0.0) * 20.0), 2)
            row["direction"] = "UP" if (change or 0) > 0 else "DOWN" if (change or 0) < 0 else "FLAT"
        rankings.sort(key=lambda x: (x["activity_score"], abs(x["change_pct_since_last_scan"] or 0)), reverse=True)
        rankings = rankings[:self.MAX_RESULTS]

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "check": self._checks + 1,
            "symbols_available": len(symbols),
            "symbols_scanned": scanned,
            "rankings": rankings,
        }
        self._redis.xadd(self.OUTPUT_STREAM, {"scan": json.dumps(payload, separators=(",", ":"))}, maxlen=100_000, approximate=True)

        with self._lock:
            self._symbols_scanned = scanned
            self._latest = rankings
            self._checks += 1

    def _run(self) -> None:
        while self._running:
            started = time.monotonic()
            try:
                self._scan_once()
            except Exception:
                with self._lock:
                    self._errors += 1
                logger.exception("F&O scanner iteration failed")
            wait = max(0.0, self.INTERVAL_SECONDS - (time.monotonic() - started))
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
