from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.core.event_bus import research_event_bus
from app.flow.detector import FlowDetector
from app.flow.models import MarketSnapshot

logger = logging.getLogger(__name__)


class FlowEngine:
    """Consumes raw market events and publishes explainable flow signals."""

    INPUT_TOPIC = "market.raw"
    OUTPUT_TOPIC = "flow.signals"

    def __init__(self) -> None:
        self._running = False
        self._detector = FlowDetector()
        self._pending: dict[str, dict[str, Any]] = {}
        self._subscription: str | None = None
        self._signals = 0
        self._errors = 0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, int | bool]:
        return {"running": self.running, "signals": self._signals, "errors": self._errors}

    @staticmethod
    def _extract_token(meta: dict[str, Any]) -> str | None:
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

    @staticmethod
    def _event_timestamp_ms(event: dict[str, Any]) -> int:
        value = str(event.get("received_at") or "")
        if value:
            try:
                return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                pass
        return 0

    def _process(self, event: dict[str, Any]) -> None:
        meta = event.get("meta", {})
        payload = event.get("payload", {})
        token = self._extract_token(meta)
        if not token:
            return
        state = self._pending.setdefault(token, {})
        feed_type = event.get("feed_type")
        if feed_type == "ltp":
            state["ltp"] = self._extract_ltp(payload, token)
        elif feed_type == "market_depth":
            state["best_bid"], state["best_ask"], state["bid_qty"], state["ask_qty"] = self._extract_depth(payload, token)
        else:
            return
        state["timestamp_ms"] = self._event_timestamp_ms(event) or state.get("timestamp_ms", 0)
        if state.get("ltp") is None or state.get("best_bid") is None or state.get("best_ask") is None:
            return
        snapshot = MarketSnapshot(token=token, timestamp_ms=int(state.get("timestamp_ms") or 0), ltp=state["ltp"], best_bid=state["best_bid"], best_ask=state["best_ask"], bid_qty=state.get("bid_qty", 0.0), ask_qty=state.get("ask_qty", 0.0), metadata={"provider": "groww", "feed_type": feed_type})
        signal = self._detector.update(snapshot)
        if signal is not None:
            research_event_bus.publish(self.OUTPUT_TOPIC, signal.as_dict())
            self._signals += 1

    def _on_event(self, event: dict[str, Any]) -> None:
        try:
            self._process(event)
        except Exception:
            self._errors += 1
            logger.exception("Failed to process market event")

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Flow engine is already running")
        self._running = True
        self._subscription = research_event_bus.subscribe(self.INPUT_TOPIC, self._on_event)

    def stop(self) -> None:
        self._running = False
        if self._subscription:
            research_event_bus.unsubscribe(self._subscription)
            self._subscription = None


flow_engine = FlowEngine()
