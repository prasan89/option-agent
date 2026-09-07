from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.config import settings
from app.ml.engine import ml_engine
from app.signals.store import signal_store

logger = logging.getLogger(__name__)


class SignalMonitor:
    """Every five minutes persist newly qualified research signals."""

    INTERVAL_SECONDS = 300
    MIN_SCORE = 60.0
    INPUT_STREAM = "intelligence:signals"

    def __init__(self) -> None:
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self._thread: threading.Thread | None = None
        self._running = False
        self._checks = 0
        self._signals_generated = 0
        self._errors = 0
        self._last_check: str | None = None
        self._last_id = "$"
        self._db_available = False
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
                "minimum_score": self.MIN_SCORE,
                "checks": self._checks,
                "signals_generated": self._signals_generated,
                "errors": self._errors,
                "last_check": self._last_check,
                "database_available": self._db_available,
                "persisted_signals": self._safe_count(),
            }

    @staticmethod
    def _candidate(raw: dict[str, Any]) -> dict[str, Any] | None:
        score = float(raw.get("intelligence_score") or 0.0)
        confidence = str(raw.get("confidence") or "LOW").upper()
        if abs(score) < SignalMonitor.MIN_SCORE or confidence == "LOW":
            return None
        symbol = str(raw.get("symbol") or raw.get("trading_symbol") or raw.get("token") or "")
        if not symbol:
            return None
        ts = str(raw.get("timestamp_ms") or raw.get("timestamp") or "")
        direction = "UP" if score > 0 else "DOWN"
        bias = str(raw.get("bias") or ("BULLISH" if score > 0 else "BEARISH"))
        key = f"{symbol}:{ts}:{direction}"
        return {
            "signal_key": key,
            "created_at": datetime.now(timezone.utc),
            "symbol": symbol,
            "underlying": raw.get("underlying") or raw.get("underlying_symbol"),
            "instrument_type": raw.get("instrument_type"),
            "expiry_date": raw.get("expiry_date"),
            "strike_price": raw.get("strike_price"),
            "ltp": raw.get("ltp"),
            "direction": direction,
            "bias": bias,
            "score": round(score, 2),
            "confidence": confidence,
            "ml_probability_up": None,
            "ml_probability_down": None,
            "event": raw.get("event"),
            "evidence": raw.get("evidence") or [],
            "payload": raw,
        }

    def _safe_count(self) -> int | None:
        try:
            value = signal_store.count()
            self._db_available = True
            return value
        except Exception:
            self._db_available = False
            return None

    def run_once(self) -> dict[str, Any]:
        entries = self._redis.xread({self.INPUT_STREAM: self._last_id}, count=1000, block=100)
        candidates: list[dict[str, Any]] = []
        for _, records in entries:
            for entry_id, values in records:
                self._last_id = entry_id
                try:
                    raw = json.loads(values.get("signal", "{}"))
                    candidate = self._candidate(raw)
                    if candidate:
                        if ml_engine.stats.get("model_ready"):
                            prediction = ml_engine.predict(raw)
                            candidate["ml_probability_up"] = prediction.get("probability_up")
                            candidate["ml_probability_down"] = prediction.get("probability_down")
                        candidates.append(candidate)
                except Exception:
                    self._errors += 1
                    logger.exception("Failed to build dashboard signal")
        inserted = signal_store.insert_many(candidates)
        self._db_available = True
        with self._lock:
            self._checks += 1
            self._signals_generated += inserted
            self._last_check = datetime.now(timezone.utc).isoformat()
        return {"checked": True, "candidates": len(candidates), "inserted": inserted}

    def _run(self) -> None:
        while self._running:
            started = time.monotonic()
            try:
                self.run_once()
            except Exception:
                with self._lock:
                    self._errors += 1
                self._db_available = False
                logger.exception("Signal monitor iteration failed")
            wait = max(0.0, self.INTERVAL_SECONDS - (time.monotonic() - started))
            end = time.monotonic() + wait
            while self._running and time.monotonic() < end:
                time.sleep(min(0.5, end - time.monotonic()))
        self._running = False

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Signal monitor is already running")
        signal_store.init()
        self._db_available = True
        self._last_id = "$"
        self._running = True
        self._thread = threading.Thread(target=self._run, name="signal-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False


signal_monitor = SignalMonitor()
