from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

import redis
from growwapi import GrowwFeed

from app.core.config import settings
from app.services.groww_client import groww_client

logger = logging.getLogger(__name__)


class GrowwFeedService:
    """Phase-1 read-only streaming collector.

    Groww exposes LTP and aggregated market depth for subscribed instruments.
    Raw snapshots are written to a Redis Stream so later flow detection can
    replay the same market observations.
    """

    STREAM_KEY = "market:raw"

    def __init__(self) -> None:
        self._feed: GrowwFeed | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._instruments: list[dict[str, str]] = []
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def instruments(self) -> list[dict[str, str]]:
        return self._instruments

    def _write_event(self, feed_type: str, meta: dict[str, Any], payload: Any) -> None:
        event = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "provider": "groww",
            "feed_type": feed_type,
            "meta": meta,
            "payload": payload,
        }
        try:
            self._redis.xadd(
                self.STREAM_KEY,
                {"event": json.dumps(event, separators=(",", ":"))},
                maxlen=100_000,
                approximate=True,
            )
        except Exception:
            logger.exception("Failed to write Groww event to Redis")

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

        def on_ltp(meta: dict[str, Any]) -> None:
            self._write_event("ltp", meta, self._feed.get_ltp() if self._feed else {})

        def on_depth(meta: dict[str, Any]) -> None:
            self._write_event("market_depth", meta, self._feed.get_market_depth() if self._feed else {})

        self._feed.subscribe_ltp(instruments, on_data_received=on_ltp)
        self._feed.subscribe_market_depth(instruments, on_data_received=on_depth)

        def consume() -> None:
            try:
                self._feed.consume()  # blocking SDK call
            except Exception:
                logger.exception("Groww feed stopped with an error")
            finally:
                self._running = False

        self._thread = threading.Thread(target=consume, name="groww-feed", daemon=True)
        self._thread.start()
        logger.info("Started Groww feed for %d instruments", len(instruments))


feed_service = GrowwFeedService()
