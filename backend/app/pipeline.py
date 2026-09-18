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
from app.jft.scanner import jft_scanner
from app.ml.engine import ml_engine
from app.price_action.scanner import price_action_scanner
from app.research.store import research_store
from app.services.groww_client import groww_client
from app.services.groww_feed import feed_service
from app.signals.monitor import signal_monitor

logger = logging.getLogger(__name__)


class ResearchPipeline:
    """Coordinate the single-instance, PostgreSQL-backed research pipeline."""

    FEED_LIMIT = 1000
    FEED_STRIKES_PER_SIDE = 5

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
            "feed_starting": feed_service.starting,
            "feed_instruments": len(feed_service.instruments),
            "flow": flow_engine.running,
            "scanner": fno_scanner.running,
            "price_action": price_action_scanner.running,
            "jft": jft_scanner.running,
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

    @classmethod
    def _feed_instruments(cls) -> list[dict[str, str]]:
        rows = groww_client.fno_instruments(active_only=True)
        today = date.today().isoformat()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            expiry = str(row.get("expiry_date") or "")[:10]
            typ = str(row.get("instrument_type") or "").upper()
            underlying = str(row.get("underlying_symbol") or "").strip().upper()
            token = str(row.get("exchange_token") or "")
            if not token or not underlying or expiry < today or typ not in {"CE", "PE"}:
                continue
            grouped.setdefault(underlying, []).append(row)

        selected_rows: list[dict[str, Any]] = []
        for underlying, contracts in sorted(grouped.items()):
            nearest_expiry = min(str(r.get("expiry_date"))[:10] for r in contracts)
            nearest = [r for r in contracts if str(r.get("expiry_date"))[:10] == nearest_expiry]
            strikes = sorted({float(r.get("strike_price") or 0) for r in nearest if float(r.get("strike_price") or 0) > 0})
            if not strikes:
                continue
            center = strikes[len(strikes) // 2]
            nearby = sorted(strikes, key=lambda strike: (abs(strike - center), strike))[: cls.FEED_STRIKES_PER_SIDE]
            for strike in nearby:
                for typ in ("CE", "PE"):
                    matches = [r for r in nearest if float(r.get("strike_price") or 0) == strike and str(r.get("instrument_type") or "").upper() == typ]
                    if matches:
                        selected_rows.append(matches[0])

        if not selected_rows:
            raise RuntimeError("No active NSE option contracts available for Groww live feed")
        selected_rows = selected_rows[: cls.FEED_LIMIT]
        return [{
            "exchange": "NSE", "segment": "FNO", "exchange_token": str(row["exchange_token"]),
            "trading_symbol": str(row.get("trading_symbol") or ""), "underlying_symbol": str(row.get("underlying_symbol") or ""),
            "instrument_type": str(row.get("instrument_type") or ""), "expiry_date": str(row.get("expiry_date") or ""),
            "strike_price": str(row.get("strike_price") or ""), "lot_size": str(row.get("lot_size") or ""),
        } for row in selected_rows]

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return self.stats
            if not groww_client.configured:
                raise RuntimeError("Groww credentials are not configured")
            research_store.init()
            self._last_error = None
            started: list[Any] = []
            failures: list[str] = []

            def start_component(name: str, component: Any) -> None:
                try:
                    if not component.running:
                        component.start()
                        started.append(component)
                except Exception as exc:
                    failures.append(f"{name}: {exc}")
                    logger.exception("Research component %s failed to start; continuing with other components", name)

            start_component("flow", flow_engine)
            start_component("intelligence_score", intelligence_score_engine)
            start_component("dataset", historical_dataset_collector)
            start_component("ml", ml_engine)
            start_component("signal_monitor", signal_monitor)

            # Market-data feed failure must not prevent historical Price Action,
            # JFT, or the HTTP dashboard from starting.
            try:
                if not feed_service.running and not feed_service.starting:
                    instruments = self._feed_instruments()
                    feed_service.start(instruments)
                    self._feed_symbols = len(instruments)
                    started.append(feed_service)
            except Exception as exc:
                failures.append(f"feed: {exc}")
                logger.exception("Groww feed failed to start; historical scanners will continue")

            start_component("fno_scanner", fno_scanner)
            start_component("price_action", price_action_scanner)
            start_component("jft", jft_scanner)

            self._last_error = " | ".join(failures) if failures else None
            self._running = True
            return self.stats

    def stop(self) -> dict[str, Any]:
        with self._lock:
            jft_scanner.stop()
            price_action_scanner.stop()
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
