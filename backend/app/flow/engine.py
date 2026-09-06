from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import redis

from app.core.config import settings
from app.flow.detector import FlowDetector
from app.flow.models import MarketSnapshot

logger = logging.getLogger(__name__)


class FlowEngine:
    """Consumes the Phase-1 Redis market stream and publishes flow signals."""

    INPUT_STREAM = "market:raw"
    OUTPUT_STREAM = "flow:signals"

    def __init__(self) -> None:
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self._thread: threading.Thread | None = None
        self._running = False
        self._detector = FlowDetector()
        self._last_id = "$"
        self._signals = 0
        self._errors = 0

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, int | str | bool]:
        return {
            "running": self.running,
            "signals": self._signals,
            "errors": self._errors,
            "input_stream": self.INPUT_STREAM,
            "output_stream": self.OUTPUT_STREAM,
        }

    @staticmethod
    def _extract_token(meta: dict[str, Any], payload: dict[str, Any]) -> str | None:
        return str(meta.get("feed_key")) if meta.get("feed_key") else None

    @staticmethod
    def _extract_ltp(payload: dict[str, Any], token: str) -> float | None:
        try:
            return float(payload["ltp"]["NSE"]["FNO"][token]["ltp"])
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _extract_depth(payload: dict[str, Any], token: str) -> tuple[float | None, float | None, float, float]:
        try:
            book = payload["NSE"]["FNO"][token]
        except (KeyError, TypeError):
            return None, None, 0.0, 0.0

        bids = [v for v in book.get("buyBook", {}).values() if isinstance(v, dict)]
        asks = [v for v in book.get("sellBook", {}).values() if isinstance(v, dict)]
        best_bid = max((float(v["price"]) for v in bids if "price" in v), default=None)
        best_ask = min((float(v["price"]) for v in asks if "price" in v), default=None)
        bid_qty = sum(float(v.get("qty", 0)) for v in bids)
        ask_qty = sum(float(v.get("qty", 0)) for v in asks)
        return best_bid, best_ask, bid_qty, ask_qty

    def _process(self, raw: str) -> None:
        event = json.loads(raw)
        meta = event.get("meta", {})
        payload = event.get("payload", {})
        token = self._extract_token(meta, payload)
        if not token:
            return

        feed_type = event.get("feed_type")
        state = getattr(self, "_pending", {}).setdefault(token, {})
        if not hasattr(self, "_pending"):
            self._pending = {}
            state = self._pending.setdefault(token, {})

        if feed_type == "ltp":
            state["ltp"] = self._extract_ltp(payload, token)
        elif feed_type == "market_depth":
            state["best_bid"], state["best_ask"], state["bid_qty"], state["ask_qty"] = self._extract_depth(payload, token)
        else:
            return

        # A signal needs both a recent price and depth observation.
        if state.get("ltp") is None or state.get("best_bid") is None or state.get("best_ask") is None:
            return

        snapshot = MarketSnapshot(
            token=token,
            timestamp_ms=int(event.get("received_at", "0").replace("-", "").replace(":", "")[:13]) if False else int(time.time() * 1000),
            ltp=state.get("ltp"),
            best_bid=state.get("best_bid"),
            best_ask=state.get("best_ask"),
            bid_qty=state.get("bid_qty", 0.0),
            ask_qty=state.get("ask_qty", 0.0),
        )
        signal = self._detector.update(snapshot)
        if signal is not None:
            self._redis.xadd(self.OUTPUT_STREAM, {"signal": json.dumps(signal.as_dict(), separators=(",", ":"))}, maxlen=100_000, approximate=True)
            self._signals += 1

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Flow engine is already running")
        self._running = True
        self._last_id = "$"
        self._thread = threading.Thread(target=self._consume, name="flow-engine", daemon=True)
        self._thread.start()

    def _consume(self) -> None:
        try:
            while self._running:
                records = self._redis.xread({self.INPUT_STREAM: self._last_id}, count=100, block=1000)
                for _, entries in records:
                    for entry_id, values in entries:
                        self._last_id = entry_id
                        try:
                            self._process(values.get("event", "{}"))
                        except Exception:
                            self._errors += 1
                            logger.exception("Failed to process market event")
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False


flow_engine = FlowEngine()
