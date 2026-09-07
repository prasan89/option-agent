from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any

import redis

from app.core.config import settings
from app.dataset.collector import historical_dataset_collector
from app.flow.engine import flow_engine
from app.intelligence.fno_scanner import fno_scanner
from app.intelligence.score_engine import intelligence_score_engine
from app.ml.engine import ml_engine
from app.services.groww_client import groww_client
from app.services.groww_feed import feed_service
from app.signals.monitor import signal_monitor

logger = logging.getLogger(__name__)


class ResearchPipeline:
    """Coordinate the read-only market-data and research signal pipeline."""

    FEED_LIMIT = 1000

    def __init__(self) -> None:
        self._running = False
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._feed_symbols = 0
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "auto_start": settings.auto_start_pipeline,
            "feed": feed_service.running,
            "feed_instruments": len(feed_service.instruments),
            "flow": flow_engine.running,
            "scanner": fno_scanner.running,
            "intelligence_score": intelligence_score_engine.running,
            "dataset": historical_dataset_collector.running,
            "ml": ml_engine.running,
            "signal_monitor": signal_monitor.running,
            "feed_symbols_selected": self._feed_symbols,
            "last_error": self._last_error,
            "trading": "DISABLED",
        }

    @staticmethod
    def _feed_instruments() -> list[dict[str, str]]:
        """Select active NIFTY option contracts from the nearest listed expiry."""
        rows = groww_client.nifty_fno_instruments()
        today = date.today().isoformat()
        eligible = []
        for row in rows:
            expiry = str(row.get("expiry_date") or "")[:10]
            instrument_type = str(row.get("instrument_type") or "").upper()
            token = str(row.get("exchange_token") or "")
            if not token or expiry < today or instrument_type not in {"CE", "PE"}:
                continue
            eligible.append(row)
        if not eligible:
            raise RuntimeError("No active NIFTY option contracts available for Groww feed")
        expiries = sorted({str(row.get("expiry_date"))[:10] for row in eligible})
        nearest = expiries[0]
        selected = [row for row in eligible if str(row.get("expiry_date"))[:10] == nearest]
        selected.sort(key=lambda row: (float(row.get("strike_price") or 0), str(row.get("instrument_type"))))
        return [
            {"exchange": "NSE", "segment": "FNO", "exchange_token": str(row["exchange_token"])}
            for row in selected[: ResearchPipeline.FEED_LIMIT]
        ]

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return self.stats
            if not groww_client.configured:
                raise RuntimeError("Groww credentials are not configured")

            # Fail fast on infrastructure dependencies before starting market streams.
            self._redis.ping()
            signal_monitor.start()
            self._last_error = None

            try:
                if not feed_service.running:
                    instruments = self._feed_instruments()
                    feed_service.start(instruments)
                    self._feed_symbols = len(instruments)
                if not flow_engine.running:
                    flow_engine.start()
                if not intelligence_score_engine.running:
                    intelligence_score_engine.start()
                if not fno_scanner.running:
                    fno_scanner.start()
                if not historical_dataset_collector.running:
                    historical_dataset_collector.start()
                if not ml_engine.running:
                    ml_engine.start()
                self._running = True
                return self.stats
            except Exception:
                signal_monitor.stop()
                raise

    def stop(self) -> dict[str, Any]:
        with self._lock:
            signal_monitor.stop()
            ml_engine.stop()
            historical_dataset_collector.stop()
            fno_scanner.stop()
            intelligence_score_engine.stop()
            flow_engine.stop()
            self._running = False
            return self.stats

    def startup(self) -> None:
        if not settings.auto_start_pipeline:
            return
        try:
            self.start()
            logger.info("Automatic research pipeline started")
        except Exception as exc:
            self._last_error = str(exc)
            logger.exception("Automatic research pipeline could not start")


research_pipeline = ResearchPipeline()
