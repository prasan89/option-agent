from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from app.core.event_bus import research_event_bus
from app.research.store import research_store

logger = logging.getLogger(__name__)


class HistoricalDatasetCollector:
    """Persist full-universe model observations without one DB connection per row."""

    INPUT_TOPICS = ("fno.rankings", "intelligence.signals")
    OUTPUT_TOPIC = "dataset.observations"

    def __init__(self) -> None:
        self._running = False
        self._observations = 0
        self._errors = 0
        self._subscriptions: list[str] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        try:
            counts = research_store.counts()
            db = True
        except Exception:
            counts = {}
            db = False
        with self._lock:
            return {
                "running": self.running,
                "observations": self._observations,
                "errors": self._errors,
                "database_available": db,
                "persisted": counts,
            }

    @staticmethod
    def _timestamp(payload: dict[str, Any]) -> int:
        raw = payload.get("timestamp_ms")
        if raw:
            return int(raw)
        value = str(payload.get("timestamp") or "")
        if not value:
            return 0
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)

    @classmethod
    def _scanner_row(cls, row: dict[str, Any], timestamp_ms: int) -> dict[str, Any]:
        return {
            "timestamp_ms": timestamp_ms,
            "source": "fno_scanner",
            "symbol": row.get("symbol"),
            "underlying": row.get("underlying"),
            "instrument_type": row.get("instrument_type"),
            "expiry_date": row.get("expiry_date"),
            "strike_price": row.get("strike_price"),
            "ltp": row.get("ltp"),
            "change_pct_1m": row.get("change_pct_since_last_scan"),
            "activity_score": row.get("activity_score"),
            "direction": row.get("direction"),
            "evidence": ["full_fno_ltp_scan", row.get("data_completeness", "PRICE_ONLY")],
        }

    def _save_batch(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        try:
            inserted = research_store.insert_observations(rows)
            with self._lock:
                self._observations += inserted
            # One event containing all observations prevents the 5,000-event
            # subscriber queue from overflowing on an 8k+ contract scan.
            research_event_bus.publish(self.OUTPUT_TOPIC, {"batch": True, "rows": rows})
        except Exception:
            with self._lock:
                self._errors += 1
            logger.exception("Dataset batch persistence failed")

    def _on_rankings(self, payload: dict[str, Any]) -> None:
        timestamp_ms = self._timestamp(payload)
        observations = payload.get("observations") or payload.get("rankings") or []
        rows = [
            self._scanner_row(row, timestamp_ms)
            for row in observations
            if isinstance(row, dict) and row.get("symbol") and row.get("ltp")
        ]
        self._save_batch(rows)

    def _on_intelligence(self, signal: dict[str, Any]) -> None:
        if not signal.get("token"):
            return
        row = {
            "timestamp_ms": signal.get("timestamp_ms"),
            "source": "intelligence_score",
            "symbol": signal.get("token"),
            "underlying": signal.get("underlying"),
            "instrument_type": signal.get("instrument_type"),
            "expiry_date": signal.get("expiry_date"),
            "strike_price": signal.get("strike_price"),
            "ltp": signal.get("ltp"),
            "flow_event": signal.get("event"),
            "flow_score": signal.get("score"),
            "intelligence_score": signal.get("intelligence_score"),
            "confidence": signal.get("confidence"),
            "bias": signal.get("bias"),
            "price_change_pct": signal.get("price_change_pct"),
            "depth_imbalance": signal.get("depth_imbalance"),
            "oi_change_pct": signal.get("oi_change_pct"),
            "volume_change_pct": signal.get("volume_change_pct"),
            "evidence": signal.get("evidence", []),
        }
        self._save_batch([row])

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Historical dataset collector is already running")
        research_store.init()
        self._running = True
        self._subscriptions = [
            research_event_bus.subscribe("fno.rankings", self._on_rankings),
            research_event_bus.subscribe("intelligence.signals", self._on_intelligence),
        ]

    def stop(self) -> None:
        self._running = False
        for token in self._subscriptions:
            research_event_bus.unsubscribe(token)
        self._subscriptions = []

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return research_store.recent_observations(limit)


historical_dataset_collector = HistoricalDatasetCollector()
