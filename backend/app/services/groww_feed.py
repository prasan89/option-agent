from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dt_time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from growwapi import GrowwFeed

from app.core.event_bus import research_event_bus
from app.services.groww_client import groww_client

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class GrowwFeedService:
    """Read-only Groww stream publisher with a REST LTP safety net.

    GrowwFeed is the primary source. During market hours, if the WebSocket has
    not delivered a tick recently, the service falls back to Groww's documented
    LTP REST endpoint so the research pipeline can continue producing events.
    """

    TOPIC = "market.raw"
    START_TIMEOUT_SECONDS = 5
    FALLBACK_INTERVAL_SECONDS = 5
    FALLBACK_AFTER_SECONDS = 10
    FALLBACK_BATCH_SIZE = 50
    MARKET_OPEN = dt_time(9, 15)
    MARKET_CLOSE = dt_time(15, 40)

    def __init__(self) -> None:
        self._feed: GrowwFeed | None = None
        self._thread: threading.Thread | None = None
        self._fallback_thread: threading.Thread | None = None
        self._running = False
        self._initializing = False
        self._ready = threading.Event()
        self._startup_error: Exception | None = None
        self._startup_stage = "IDLE"
        self._instruments: list[dict[str, str]] = []
        self._events = 0
        self._fallback_events = 0
        self._fallback_requests = 0
        self._fallback_errors = 0
        self._errors = 0
        self._last_event_at: float | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def starting(self) -> bool:
        return self._initializing and self._thread is not None and self._thread.is_alive()

    @property
    def instruments(self) -> list[dict[str, str]]:
        return self._instruments

    @property
    def stats(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            last_event_age = None if self._last_event_at is None else round(max(0.0, now - self._last_event_at), 1)
            return {
                "running": self.running,
                "starting": self.starting,
                "initializing": self._initializing,
                "startup_stage": self._startup_stage,
                "instruments": len(self._instruments),
                "events": self._events,
                "fallback_events": self._fallback_events,
                "fallback_requests": self._fallback_requests,
                "fallback_errors": self._fallback_errors,
                "last_event_age_seconds": last_event_age,
                "errors": self._errors,
                "startup_error": str(self._startup_error) if self._startup_error else None,
            }

    @classmethod
    def _market_open(cls) -> bool:
        now = datetime.now(IST)
        return now.weekday() < 5 and cls.MARKET_OPEN <= now.time() <= cls.MARKET_CLOSE

    def _stage(self, stage: str) -> None:
        with self._lock:
            self._startup_stage = stage
        logger.info("Groww feed startup stage: %s", stage)

    def _publish(self, feed_type: str, meta: dict[str, Any], payload: Any, fallback: bool = False) -> None:
        event = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "provider": "groww",
            "feed_type": feed_type,
            "meta": meta,
            "payload": payload,
            "source": "REST_LTP_FALLBACK" if fallback else "GROWW_FEED",
        }
        try:
            research_event_bus.publish(self.TOPIC, event)
            with self._lock:
                self._events += 1
                if fallback:
                    self._fallback_events += 1
                self._last_event_at = time.time()
        except Exception:
            with self._lock:
                self._errors += 1
            logger.exception("Failed to publish Groww event")

    def _poll_rest_ltp(self) -> None:
        while self.running:
            try:
                # There is no meaningful live LTP stream outside the NSE session.
                if not self._market_open():
                    time.sleep(self.FALLBACK_INTERVAL_SECONDS)
                    continue

                with self._lock:
                    last_event = self._last_event_at
                if last_event is not None and time.time() - last_event < self.FALLBACK_AFTER_SECONDS:
                    time.sleep(self.FALLBACK_INTERVAL_SECONDS)
                    continue

                symbols = [str(row.get("trading_symbol") or "") for row in self._instruments]
                symbols = [symbol for symbol in symbols if symbol]
                for start in range(0, len(symbols), self.FALLBACK_BATCH_SIZE):
                    batch = symbols[start : start + self.FALLBACK_BATCH_SIZE]
                    if not batch:
                        continue
                    with self._lock:
                        self._fallback_requests += 1
                    payload = groww_client.ltp(batch)
                    now_ms = int(time.time() * 1000)
                    by_symbol = {str(row.get("trading_symbol")): row for row in self._instruments}
                    for exchange_symbol, value in (payload or {}).items():
                        symbol = str(exchange_symbol)
                        if symbol.startswith("NSE_"):
                            symbol = symbol[4:]
                        row = by_symbol.get(symbol)
                        if row is None:
                            continue
                        token = str(row.get("exchange_token") or "")
                        try:
                            ltp = float(value)
                        except (TypeError, ValueError):
                            continue
                        if not token or ltp <= 0:
                            continue
                        fallback_payload = {
                            "NSE": {
                                "FNO": {
                                    token: {"tsInMillis": now_ms, "ltp": ltp}
                                }
                            }
                        }
                        self._publish(
                            "ltp",
                            {
                                "exchange": "NSE",
                                "segment": "FNO",
                                "feed_type": "ltp",
                                "feed_key": token,
                            },
                            fallback_payload,
                            fallback=True,
                        )
            except Exception as exc:
                with self._lock:
                    self._fallback_errors += 1
                logger.warning("Groww REST LTP fallback failed: %s", exc)
            time.sleep(self.FALLBACK_INTERVAL_SECONDS)

    def _run_feed(self, instruments: list[dict[str, str]]) -> None:
        try:
            self._stage("GET_CLIENT")
            client = groww_client._get_client()

            self._stage("CREATE_FEED")
            feed = GrowwFeed(client)
            self._feed = feed
            self._instruments = instruments

            # Start the fallback independently so a slow/hung websocket
            # subscription cannot leave the entire research pipeline blind.
            with self._lock:
                self._running = True
                self._initializing = True
                self._startup_error = None
            self._fallback_thread = threading.Thread(
                target=self._poll_rest_ltp,
                name="groww-ltp-fallback",
                daemon=True,
            )
            self._fallback_thread.start()

            sdk_instruments = [
                {
                    "exchange": str(row["exchange"]),
                    "segment": str(row["segment"]),
                    "exchange_token": str(row["exchange_token"]),
                }
                for row in instruments
            ]

            self._stage("SUBSCRIBE_LTP")
            feed.subscribe_ltp(
                sdk_instruments,
                on_data_received=lambda meta: self._publish("ltp", meta, feed.get_ltp()),
            )

            # Depth is useful enrichment but must never prevent LTP from
            # becoming operational. A depth subscription failure is isolated.
            self._stage("SUBSCRIBE_MARKET_DEPTH")
            try:
                feed.subscribe_market_depth(
                    sdk_instruments,
                    on_data_received=lambda meta: self._publish(
                        "market_depth", meta, feed.get_market_depth()
                    ),
                )
            except Exception:
                with self._lock:
                    self._errors += 1
                logger.exception("Groww market-depth subscription failed; LTP remains active")

            with self._lock:
                self._running = True
                self._initializing = False
                self._startup_error = None
                self._startup_stage = "READY"
            self._ready.set()
            logger.info("Groww feed READY for %d instruments", len(instruments))

            self._stage("CONSUME")
            feed.consume()
            logger.info("Groww feed consume() returned")
        except Exception as exc:
            with self._lock:
                self._startup_error = exc
                self._errors += 1
                self._running = False
                self._initializing = False
            self._ready.set()
            logger.exception("Groww feed stopped during stage %s", self._startup_stage)
        finally:
            with self._lock:
                self._running = False
                self._initializing = False

    def start(self, instruments: list[dict[str, str]]) -> None:
        if self.running or self.starting:
            return
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
            logger.warning(
                "Groww feed is still initializing after %ss; stage=%s. "
                "REST LTP fallback remains available during market hours.",
                self.START_TIMEOUT_SECONDS,
                self._startup_stage,
            )
            return

        if self._startup_error:
            raise RuntimeError(
                f"Groww feed failed during stage {self._startup_stage}: {self._startup_error}"
            ) from self._startup_error

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._initializing = False
        # GrowwFeed.consume is SDK-blocking and has no reliable blocking-stop
        # primitive in the current SDK wrapper. Daemon threads exit with the app process.


feed_service = GrowwFeedService()
