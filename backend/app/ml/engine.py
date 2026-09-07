from __future__ import annotations

import json
import logging
import threading
from collections import deque
from typing import Any

import redis

from app.core.config import settings
from app.ml.labeler import OutcomeLabeler
from app.ml.model import baseline_predictor

logger = logging.getLogger(__name__)


class MLEngine:
    """Collect observations, create forward labels, and train a research-only baseline."""

    INPUT_STREAM = "dataset:observations"
    LABEL_STREAM = "dataset:labels"
    LABEL_HORIZON_MINUTES = 5
    MAX_HISTORY = 50_000

    def __init__(self) -> None:
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_id = "$"
        self._latest: dict[str, dict[str, Any]] = {}
        self._seen: set[tuple[str, int]] = set()
        self._history = deque(maxlen=self.MAX_HISTORY)
        self._labeler = OutcomeLabeler()
        self._observations = 0
        self._labels = 0
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
                "observations": self._observations,
                "labels": self._labels,
                "errors": self._errors,
                "label_horizon_minutes": self.LABEL_HORIZON_MINUTES,
                "label_stream": self.LABEL_STREAM,
                "model_ready": baseline_predictor.ready,
                "training": baseline_predictor.training_stats,
            }

    @staticmethod
    def _timestamp(row: dict[str, Any]) -> int:
        if row.get("timestamp_ms"):
            return int(row["timestamp_ms"])
        # Scanner timestamps are ISO strings; normalize them to milliseconds.
        from datetime import datetime
        value = str(row.get("timestamp") or "")
        if value:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        return 0

    @staticmethod
    def _features(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "score": row.get("flow_score", row.get("score", 0.0)),
            "price_change_pct": row.get("price_change_pct", row.get("change_pct_1m", row.get("change_pct_since_last_scan", 0.0))),
            "depth_imbalance": row.get("depth_imbalance", 0.0),
            "oi_change_pct": row.get("oi_change_pct", 0.0),
            "volume_change_pct": row.get("volume_change_pct", 0.0),
            "intelligence_score": row.get("intelligence_score", 0.0),
        }

    def _publish_labels(self, labels: list[dict[str, Any]]) -> None:
        for label in labels:
            if label.get("horizon_minutes") != self.LABEL_HORIZON_MINUTES:
                continue
            if not isinstance(label.get("features"), dict):
                continue
            self._redis.xadd(
                self.LABEL_STREAM,
                {"label": json.dumps(label, separators=(",", ":"))},
                maxlen=1_000_000,
                approximate=True,
            )
            self._labels += 1

    def _consume(self) -> None:
        try:
            while self._running:
                records = self._redis.xread({self.INPUT_STREAM: self._last_id}, count=100, block=1000)
                for _, entries in records:
                    for entry_id, values in entries:
                        self._last_id = entry_id
                        try:
                            row = json.loads(values.get("observation", "{}"))
                            symbol = str(row.get("symbol") or "")
                            price = float(row.get("ltp") or 0)
                            ts = self._timestamp(row)
                            if not symbol or price <= 0 or ts <= 0:
                                continue
                            key = (symbol, ts)
                            if key in self._seen:
                                continue
                            self._seen.add(key)
                            self._history.append(row)
                            self._latest[symbol] = row
                            labels = self._labeler.observe(symbol, ts, price, self._features(row))
                            self._publish_labels(labels)
                            with self._lock:
                                self._observations += 1
                        except Exception:
                            with self._lock:
                                self._errors += 1
                            logger.exception("Failed to process ML observation")
        finally:
            self._running = False

    def start(self) -> None:
        if self.running:
            raise RuntimeError("ML engine is already running")
        self._running = True
        self._last_id = "$"
        self._thread = threading.Thread(target=self._consume, name="ml-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def latest(self, limit: int = 25) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._latest.values())[-limit:]

    def train(self) -> dict[str, Any]:
        """Train on chronological 5-minute outcomes with a final holdout."""
        records: list[dict[str, Any]] = []
        for _, values in self._redis.xrange(self.LABEL_STREAM, count=1_000_000):
            try:
                record = json.loads(values.get("label", "{}"))
                if record.get("horizon_minutes") == self.LABEL_HORIZON_MINUTES and isinstance(record.get("features"), dict) and record.get("label") in (0, 1):
                    records.append(record)
            except Exception:
                continue
        records.sort(key=lambda x: int(x.get("timestamp_ms", 0)))
        rows = [record["features"] for record in records]
        labels = [int(record["label"]) for record in records]
        result = baseline_predictor.fit(rows, labels)
        return {**result, "label_rows": len(rows), "horizon_minutes": self.LABEL_HORIZON_MINUTES}

    def predict(self, row: dict[str, Any]) -> dict[str, Any]:
        prediction = baseline_predictor.predict(self._features(row))
        return prediction.__dict__


ml_engine = MLEngine()
