from __future__ import annotations

import json
import logging
import threading
from typing import Any

import redis

from app.core.config import settings
from app.ml.model import baseline_predictor

logger = logging.getLogger(__name__)


class MLEngine:
    """Build labels from historical observations and train a baseline model."""

    INPUT_STREAM = "dataset:observations"
    LABEL_STREAM = "dataset:labels"

    def __init__(self) -> None:
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_id = "$"
        self._latest: dict[str, dict[str, Any]] = {}
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
            return {"running": self.running, "observations": self._observations, "labels": self._labels, "errors": self._errors, "model_ready": baseline_predictor._ready}

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
                            ts = int(row.get("timestamp_ms") or 0)
                            if symbol and price > 0 and ts:
                                with self._lock:
                                    self._latest[symbol] = row
                                    self._observations += 1
                        except Exception:
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
            return list(self._latest.values())[:limit]

    def train(self) -> dict[str, Any]:
        """Train only when labeled records are explicitly supplied to this engine."""
        rows = []
        labels = []
        for _, values in self._redis.xrange(self.LABEL_STREAM, count=50_000):
            try:
                record = json.loads(values.get("label", "{}"))
                features = record.get("features")
                outcome = record.get("label")
                if isinstance(features, dict) and outcome in (0, 1):
                    rows.append(features)
                    labels.append(outcome)
            except Exception:
                continue
        result = baseline_predictor.fit(rows, labels)
        return {**result, "label_rows": len(rows)}

    def predict(self, row: dict[str, Any]) -> dict[str, Any]:
        prediction = baseline_predictor.predict(row)
        return prediction.__dict__


ml_engine = MLEngine()
