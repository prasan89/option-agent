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
    """Read-only Groww stream publisher using the local event bus.

    The Groww SDK creates and connects its NATS client synchronously by calling
    an asyncio event loop. FastAPI/Uvicorn already owns the main event loop,
    so the SDK must be constructed on a dedicated background thread.
    """

    TOPIC = "market.raw"
    START_TIMEOUT_SECONDS = 20

    def __init__(self) -> None:
        self._feed: GrowwFeed | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._initializing = False
        self._startup_stage = "IDLE"
        self._ready = threading.Event()
        self._startup_error: Exception | None = None
        self._instruments: list[dict[str, str]] = []
        self._events = 0
        self._errors = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def instruments(self) -> list[dict[str, str]]:
        return self._instruments

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "initializing": self._initializing,
            "startup_stage": self._startup_stage,
            "instruments": len(self._instruments),
            "events": self._events,
            "errors": self._errors,
            "startup_error": str(self._startup_error) if self._startup_error else None,
        }

    def _set_startup_stage(self, stage: str) -> None:
        with self._lock:
            self._startup_stage = stage
        logger.info("Groww feed startup stage: %s", stage)

    def _publish(self, feed_type: str, meta: dict[str, Any], payload: Any) -> None:
        event = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "provider": "groww",
            "feed_type": feed_type,
            "meta": meta,
            "payload": payload,
        }
        try:
            research_event_bus.publish(self.TOPIC, event)
            self._events += 1
        except Exception:
            self._errors += 1
            logger.exception("Failed to publish Groww event")

    def _run_feed(self, instruments: list[dict[str, str]]) -> None:
        """Create, subscribe and consume the SDK feed on one dedicated thread."""
        try:
            self._set_startup_stage("GET_CLIENT")
            client = groww_client._get_client()
            logger.info("Groww feed: GrowwAPI client obtained")

            self._set_startup_stage("CREATE_FEED")
            feed = GrowwFeed(client)
            self._feed = feed
            self._instruments = instruments
            logger.info(
                "Groww feed: GrowwFeed created, instruments=%d",
                len(instruments),
            )

            self._set_startup_stage("SUBSCRIBE_LTP")
            feed.subscribe_ltp(
                instruments,
                on_data_received=lambda meta: self._publish("ltp", meta, feed.get_ltp()),
            )
            logger.info("Groww feed: LTP subscription completed")

            self._set_startup_stage("SUBSCRIBE_MARKET_DEPTH")
            feed.subscribe_market_depth(
                instruments,
                on_data_received=lambda meta: self._publish(
                    "market_depth", meta, feed.get_market_depth()
                ),
            )
            logger.info("Groww feed: market-depth subscription completed")

            self._set_startup_stage("READY")
            with self._lock:
                self._running = True
                self._initializing = False
                self._startup_error = None
            self._ready.set()
            logger.info(
                "Groww feed READY; starting consume() for %d instruments",
                len(instruments),
            )

            self._set_startup_stage("CONSUME")
            feed.consume()
            logger.info("Groww feed: consume() returned")
        except Exception as exc:
            with self._lock:
                self._startup_error = exc
                self._errors += 1
                self._running = False
                self._initializing = False
            self._ready.set()
            logger.exception(
                "Groww feed stopped during stage %s",
                self._startup_stage,
            )
        finally:
            with self._lock:
                self._running = False
                self._initializing = False

    def start(self, instruments: list[dict[str, str]]) -> None:
        if self.running or self._initializing:
            raise RuntimeError("Groww feed is already running or starting")
        if not instruments:
            raise ValueError("At least one instrument is required")
        if not groww_client.configured:
            raise RuntimeError("Groww credentials are not configured")

        self._ready.clear()
        self._startup_error = None
        self._startup_stage = "STARTING"
        self._instruments = instruments
        self._initializing = True
        self._thread = threading.Thread(
            target=self._run_feed,
            args=(instruments,),
            name="groww-feed",
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(timeout=self.START_TIMEOUT_SECONDS):
            with self._lock:
                self._initializing = False
            raise RuntimeError(
                "Groww feed did not initialize within "
                f"{self.START_TIMEOUT_SECONDS} seconds; "
                f"last startup stage: {self._startup_stage}"
            )
        if self._startup_error:
            raise RuntimeError(
                f"Groww feed failed during {self._startup_stage}: {self._startup_error}"
            ) from self._startup_error

    def stop(self) -> None:
        with self._lock:
            self._running = False
        # GrowwFeed.consume is SDK-blocking and has no reliable blocking-stop
        # primitive in the current SDK wrapper. The daemon thread exits with
        # the application process.


feed_service = GrowwFeedService()
