from __future__ import annotations

import json
import logging
import threading
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)


class HistoricalDatasetCollector:
    """Persist model-ready market observations without placing trades."""

    INPUT_STREAMS = ("fno:rankings", "intelligence:signals")
    OUTPUT_STREAM = "dataset:observations"
    MAXLEN = 1_000_000

    def __init__(self) -> None:
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_ids = {name: "$" for name in self.INPUT_STREAMS}
        self._observations = 0
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
                "errors": self._errors,
                "output_stream": self.OUTPUT_STREAM,
                "retention_maxlen": self.MAXLEN,
            }

    @staticmethod
    def _append(redis_client: redis.Redis, record: dict[str, Any]) -> None:
        redis_client.xadd(
            HistoricalDatasetCollector.OUTPUT_STREAM,
            {"observation": json.dumps(record, separators=(",", ":"))},
            maxlen=HistoricalDatasetCollector.MAXLEN,
            approximate=True,
        )

    def _consume_rankings(self, entries: list[tuple[str, dict[str, Any]]]) -> None:
        for entry_id, values in entries:
            self._last_ids["fno:rankings"] = entry_id
            payload = json.loads(values.get("scan", "{}"))
            timestamp = payload.get("timestamp")
            for row in payload.get("rankings", []):
                if not isinstance(row, dict) or not row.get("symbol"):
                    continue
                self._append(self._redis, {
                    "timestamp": timestamp,
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
                    "label_status": "UNLABELED",
                })
                self._observations += 1

    def _consume_intelligence(self, entries: list[tuple[str, dict[str, Any]]]) -> None:
        for entry_id, values in entries:
            self._last_ids["intelligence:signals"] = entry_id
            signal = json.loads(values.get("signal", "{}"))
            if not signal.get("token"):
                continue
            self._append(self._redis, {
                "timestamp_ms": signal.get("timestamp_ms"),
                "source": "intelligence_score",
                "symbol": signal.get("token"),
                "side": signal.get("side"),
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
                "label_status": "UNLABELED",
            })
            self._observations += 1

    def _run(self) -> None:
        try:
            while self._running:
                try:
                    records = self._redis.xread(self._last_ids, count=100, block=1000)
                    for stream, entries in records:
                        if stream == "fno:rankings":
                            self._consume_rankings(entries)
                        elif stream == "intelligence:signals":
                            self._consume_intelligence(entries)
                    with self._lock:
                        self._observations = self._observations
                except Exception:
                    with self._lock:
                        self._errors += 1
                    logger.exception("Historical dataset collection failed")
        finally:
            self._running = False

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Historical dataset collector is already running")
        self._running = True
        self._last_ids = {name: "$" for name in self.INPUT_STREAMS}
        self._thread = threading.Thread(target=self._run, name="dataset-collector", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False


historical_dataset_collector = HistoricalDatasetCollector()
