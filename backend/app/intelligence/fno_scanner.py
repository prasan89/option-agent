from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any

from app.services.groww_client import groww_client
from app.flow.intelligence import FlowIntelligence

logger = logging.getLogger(__name__)


class FNOScanner:
    """Read-only one-minute scanner for the complete current NSE F&O universe."""

    INTERVAL_SECONDS = 60
    BATCH_SIZE = 50

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = False
        self._latest: list[dict[str, Any]] = []
        self._checks = 0
        self._symbols_scanned = 0
        self._errors = 0
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
                "symbols_scanned_last_check": self._symbols_scanned,
                "errors": self._errors,
                "latest_signals": self._latest[:100],
            }

    @staticmethod
    def _today() -> date:
        return date.today()

    def _scan_once(self) -> None:
        instruments = groww_client.nifty_fno_instruments()
        if not instruments:
            # Use the instrument master for the complete F&O universe.
            master = groww_client.all_instruments()
            if master is None or len(master) == 0:
                raise RuntimeError("Groww instrument master returned no instruments")
            df = master.copy()
            if "segment" in df.columns:
                df = df[df["segment"].astype(str).str.upper() == "FNO"]
            instruments = df.fillna("").to_dict(orient="records")

        # One-minute scanner is intentionally capped by provider/API request
        # batches. The feed itself can subscribe to up to 1,000 instruments.
        symbols = [str(x.get("groww_symbol") or x.get("trading_symbol")) for x in instruments]
        symbols = [x for x in symbols if x and x != "nan"]
        scanned = 0
        signals: list[dict[str, Any]] = []

        for start in range(0, len(symbols), self.BATCH_SIZE):
            batch = symbols[start : start + self.BATCH_SIZE]
            try:
                quotes = groww_client.ltp(batch)
                for symbol, value in (quotes or {}).items():
                    signals.append({"symbol": symbol, "ltp": value})
                scanned += len(batch)
            except Exception:
                self._errors += 1
                logger.exception("F&O LTP batch failed")

        with self._lock:
            self._symbols_scanned = scanned
            self._latest = signals
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
