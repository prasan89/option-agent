from __future__ import annotations

import logging
import threading
import time
from datetime import date
from typing import Any, Callable

from app.flow.intelligence import FlowIntelligence
from app.services.groww_client import groww_client

logger = logging.getLogger(__name__)


class IntelligenceScheduler:
    """Poll NIFTY option-chain intelligence at a fixed one-minute cadence."""

    INTERVAL_SECONDS = 60

    def __init__(self, fetch_chain: Callable[[date], dict[str, Any]]) -> None:
        self._fetch_chain = fetch_chain
        self._analyzer = FlowIntelligence()
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_snapshot: dict[str, Any] | None = None
        self._latest: dict[str, Any] | None = None
        self._checks = 0
        self._errors = 0

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def latest(self) -> dict[str, Any] | None:
        return self._latest

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "interval_seconds": self.INTERVAL_SECONDS,
            "checks": self._checks,
            "errors": self._errors,
            "latest": self._latest,
        }

    @staticmethod
    def _next_expiry() -> date:
        """Resolve the nearest active NIFTY option expiry from the instrument master."""
        rows = groww_client.nifty_fno_instruments()
        today = date.today()
        expiries: list[date] = []
        for row in rows:
            value = str(row.get("expiry_date") or "")[:10]
            try:
                expiry = date.fromisoformat(value)
            except ValueError:
                continue
            if expiry >= today:
                expiries.append(expiry)
        if not expiries:
            raise RuntimeError("No active NIFTY option expiry available")
        return min(expiries)

    def _run_once(self) -> None:
        expiry = self._next_expiry()
        chain = self._fetch_chain(expiry)
        self._latest = self._analyzer.analyze(chain, self._last_snapshot)
        self._last_snapshot = {"rows": self._latest.get("rows", [])}
        self._checks += 1

    def _consume(self) -> None:
        while self._running:
            started = time.monotonic()
            try:
                self._run_once()
            except Exception:
                self._errors += 1
                logger.exception("One-minute option-chain intelligence check failed")
            elapsed = time.monotonic() - started
            wait = max(0.0, self.INTERVAL_SECONDS - elapsed)
            end = time.monotonic() + wait
            while self._running and time.monotonic() < end:
                time.sleep(min(0.5, end - time.monotonic()))

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Intelligence scheduler is already running")
        self._running = True
        self._thread = threading.Thread(target=self._consume, name="intelligence-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False


scheduler: IntelligenceScheduler | None = None
