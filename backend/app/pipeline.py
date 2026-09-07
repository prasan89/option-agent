from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any

from app.core.config import settings
from app.core.event_bus import research_event_bus
from app.dataset.collector import historical_dataset_collector
from app.flow.engine import flow_engine
from app.intelligence.fno_scanner import fno_scanner
from app.intelligence.score_engine import intelligence_score_engine
from app.ml.engine import ml_engine
from app.research.store import research_store
from app.services.groww_client import groww_client
from app.services.groww_feed import feed_service
from app.signals.monitor import signal_monitor

logger = logging.getLogger(__name__)


class ResearchPipeline:
    """Coordinate the single-instance, PostgreSQL-backed research pipeline."""

    FEED_LIMIT = 1000

    def __init__(self) -> None:
        self._running = False
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._feed_symbols = 0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        try:
            research_store.init()
            db = "CONNECTED"
        except Exception:
            db = "NOT_CONNECTED"
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
            "event_bus": research_event_bus.stats,
            "database": db,
            "trading": "DISABLED",
        }

    @staticmethod
    def _feed_instruments() -> list[dict[str, str]]:
        rows = groww_client.nifty_fno_instruments()
        today = date.today().isoformat()
        eligible: list[dict[str, Any]] = []
        for row in rows:
            expiry = str(row.get("expiry_date") or "")[:10]
            typ = str(row.get("instrument_type") or "").upper()
            token = str(row.get("exchange_token") or "")
            if token and expiry >= today and typ in {"CE", "PE"}:
                eligible.append(row)
        if not eligible:
            raise RuntimeError("No active NIFTY option contracts available for Groww feed")

        nearest = sorted({str(r.get("expiry_date"))[:10] for r in eligible})[0]
        selected = [
            r for r in eligible if str(r.get("expiry_date"))[:10] == nearest
        ]
        selected.sort(
            key=lambda r: (
                float(r.get("strike_price") or 0),
                str(r.get("instrument_type") or ""),
            )
        )
        return [
            {
                "exchange": "NSE",
                "segment": "FNO",
                "exchange_token": str(r["exchange_token"]),
            }
            for r in selected[: ResearchPipeline.FEED_LIMIT]
        ]

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return self.stats
            if not groww_client.configured:
                raise RuntimeError("Groww credentials are not configured")

            research_store.init()
            self._last_error = None

            started: list[Any] = []
            try:
                # Start every event consumer before the producer. This avoids
                # losing the first market events while subscribers initialize.
                if not flow_engine.running:
                    flow_engine.start()
                    started.append(flow_engine)
                if not intelligence_score_engine.running:
                    intelligence_score_engine.start()
                    started.append(intelligence_score_engine)
                if not fno_scanner.running:
                    fno_scanner.start()
                    started.append(fno_scanner)
                if not historical_dataset_collector.running:
                    historical_dataset_collector.start()
                    started.append(historical_dataset_collector)
                if not ml_engine.running:
                    ml_engine.start()
                    started.append(ml_engine)
                if not signal_monitor.running:
                    signal_monitor.start()
                    started.append(signal_monitor)

                if not feed_service.running:
                    instruments = self._feed_instruments()
                    feed_service.start(instruments)
                    self._feed_symbols = len(instruments)
                    started.append(feed_service)

                self._running = True
                return self.stats
            except Exception:
                # Roll back components started by this attempt. The feed has
                # its own dedicated daemon thread and will not touch Uvicorn's
                # event loop.
                for component in reversed(started):
                    try:
                        component.stop()
                    except Exception:
                        logger.exception("Failed to roll back pipeline component")
                self._running = False
                raise

    def stop(self) -> dict[str, Any]:
        with self._lock:
            signal_monitor.stop()
            ml_engine.stop()
            historical_dataset_collector.stop()
            fno_scanner.stop()
            intelligence_score_engine.stop()
            flow_engine.stop()
            feed_service.stop()
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
