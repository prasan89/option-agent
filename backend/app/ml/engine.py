from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any

from app.core.event_bus import research_event_bus
from app.ml.labeler import OutcomeLabeler
from app.ml.model import baseline_predictor
from app.research.store import research_store

logger = logging.getLogger(__name__)


class MLEngine:
    """Create forward labels from live observations and train a research baseline."""

    INPUT_TOPIC = "dataset.observations"
    LABEL_TOPIC = "dataset.labels"
    LABEL_HORIZON_MINUTES = 5
    MAX_HISTORY = 50_000

    def __init__(self) -> None:
        self._running = False
        self._latest: dict[str, dict[str, Any]] = {}
        self._seen: set[tuple[str, int, str]] = set()
        self._history = deque(maxlen=self.MAX_HISTORY)
        self._labeler = OutcomeLabeler()
        self._observations = 0
        self._labels = 0
        self._errors = 0
        self._lock = threading.Lock()
        self._subscription: str | None = None

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
                "labels": self._labels,
                "errors": self._errors,
                "label_horizon_minutes": self.LABEL_HORIZON_MINUTES,
                "database_available": db,
                "model_ready": baseline_predictor.ready,
                "training": baseline_predictor.training_stats,
                "persisted": counts,
            }

    @staticmethod
    def _timestamp(row: dict[str, Any]) -> int:
        if row.get("timestamp_ms"):
            return int(row["timestamp_ms"])
        value = str(row.get("timestamp") or "")
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000) if value else 0

    @staticmethod
    def _features(row: dict[str, Any]) -> dict[str, float]:
        def num(key: str, fallback: float = 0.0) -> float:
            try:
                value = row.get(key, fallback)
                return float(value) if value is not None else fallback
            except (TypeError, ValueError):
                return fallback
        return {
            "score": num("flow_score", num("score")),
            "price_change_pct": num("price_change_pct", num("change_pct_1m")),
            "depth_imbalance": num("depth_imbalance"),
            "oi_change_pct": num("oi_change_pct"),
            "volume_change_pct": num("volume_change_pct"),
            "intelligence_score": num("intelligence_score"),
        }

    def _process_row(self, row: dict[str, Any], labels_out: list[dict[str, Any]]) -> None:
        symbol = str(row.get("symbol") or row.get("token") or "")
        try:
            price = float(row.get("ltp") or 0)
        except (TypeError, ValueError):
            return
        ts = self._timestamp(row)
        source = str(row.get("source") or "unknown")
        if not symbol or price <= 0 or ts <= 0:
            return
        key = (symbol, ts, source)
        if key in self._seen:
            return
        self._seen.add(key)
        self._history.append(row)
        self._latest[symbol] = row
        labels = self._labeler.observe(symbol, ts, price, self._features(row))
        labels_out.extend(label for label in labels if label.get("horizon_minutes") == self.LABEL_HORIZON_MINUTES)
        self._observations += 1

    def _on_observation(self, payload: dict[str, Any]) -> None:
        try:
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if rows is None:
                rows = [payload]
            labels: list[dict[str, Any]] = []
            with self._lock:
                for row in rows:
                    if isinstance(row, dict):
                        self._process_row(row, labels)
            if labels:
                inserted = research_store.insert_labels(labels)
                with self._lock:
                    self._labels += inserted
                research_event_bus.publish(self.LABEL_TOPIC, {"batch": True, "labels": labels})
        except Exception:
            with self._lock:
                self._errors += 1
            logger.exception("Failed to process ML observation batch")

    def start(self) -> None:
        if self.running:
            raise RuntimeError("ML engine is already running")
        self._running = True
        self._subscription = research_event_bus.subscribe(self.INPUT_TOPIC, self._on_observation)

    def stop(self) -> None:
        self._running = False
        if self._subscription:
            research_event_bus.unsubscribe(self._subscription)
            self._subscription = None

    def latest(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._latest.values())[-limit:]

    def train(self) -> dict[str, Any]:
        records = research_store.labels(self.LABEL_HORIZON_MINUTES)
        rows = [r["features"] for r in records]
        labels = [int(r["label"]) for r in records]
        result = baseline_predictor.fit(rows, labels)
        return {**result, "label_rows": len(rows), "horizon_minutes": self.LABEL_HORIZON_MINUTES}

    def predict(self, row: dict[str, Any]) -> dict[str, Any]:
        return baseline_predictor.predict(self._features(row)).__dict__


ml_engine = MLEngine()
