from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from growwapi import GrowwFeed

from app.core.event_bus import research_event_bus
from app.services.groww_client import groww_client

logger = logging.getLogger(__name__)


class GrowwFeedService:
    """Read-only Groww stream publisher using the local event bus."""

    TOPIC = "market.raw"

    def __init__(self) -> None:
        self._feed: GrowwFeed | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._instruments: list[dict[str, str]] = []
        self._events = 0
        self._errors = 0

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def instruments(self) -> list[dict[str, str]]:
        return self._instruments

    @property
    def stats(self) -> dict[str, Any]:
        return {"running": self.running, "instruments": len(self._instruments), "events": self._events, "errors": self._errors}

    def _publish(self, feed_type: str, meta: dict[str, Any], payload: Any) -> None:
        event = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "provider": "groww", "feed_type": feed_type, "meta": meta, "payload": payload,
        }
        try:
            research_event_bus.publish(self.TOPIC, event)
            self._events += 1
        except Exception:
            self._errors += 1
            logger.exception("Failed to publish Groww event")

    def start(self, instruments: list[dict[str, str]]) -> None:
        if self.running:
            raise RuntimeError("Groww feed is already running")
        if not instruments:
            raise ValueError("At least one instrument is required")
        if not groww_client.configured:
            raise RuntimeError("Groww credentials are not configured")
        client = groww_client._get_client()
        self._feed = GrowwFeed(client)
        self._instruments = instruments
        self._running = True

        self._feed.subscribe_ltp(instruments, on_data_received=lambda meta: self._publish("ltp", meta, self._feed.get_ltp() if self._feed else {}))
        self._feed.subscribe_market_depth(instruments, on_data_received=lambda meta: self._publish("market_depth", meta, self._feed.get_market_depth() if self._feed else {}))

        def consume() -> None:
            try:
                self._feed.consume()  # blocking SDK call
            except Exception:
                self._errors += 1
                logger.exception("Groww feed stopped with an error")
            finally:
                self._running = False

        self._thread = threading.Thread(target=consume, name="groww-feed", daemon=True)
        self._thread.start()
        logger.info("Started Groww feed for %d instruments", len(instruments))

    def stop(self) -> None:
        self._running = False
        # GrowwFeed.consume is SDK-blocking; daemon thread exits with the app.


feed_service = GrowwFeedService()
