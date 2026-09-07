from __future__ import annotations

import logging
import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)
Callback = Callable[[dict[str, Any]], None]


@dataclass
class _Subscription:
    topic: str
    callback: Callback
    queue: queue.Queue[dict[str, Any]]
    stop: threading.Event
    thread: threading.Thread


class EventBus:
    """Small in-process pub/sub bus for the single BTP trial instance.

    Each subscriber gets a bounded queue and worker, so a slow DB operation
    cannot block Groww feed callbacks. The abstraction can later be backed by
    Redis/Kafka without changing pipeline components.
    """

    QUEUE_SIZE = 5000

    def __init__(self) -> None:
        self._subscriptions: dict[str, _Subscription] = {}
        self._lock = threading.Lock()
        self._published = 0
        self._dropped = 0

    def subscribe(self, topic: str, callback: Callback) -> str:
        token = uuid.uuid4().hex
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self.QUEUE_SIZE)
        stop = threading.Event()

        def worker() -> None:
            while not stop.is_set():
                try:
                    event = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    callback(event)
                except Exception:
                    logger.exception("Event subscriber failed for topic %s", topic)
                finally:
                    q.task_done()

        thread = threading.Thread(target=worker, name=f"event-{topic}", daemon=True)
        subscription = _Subscription(topic, callback, q, stop, thread)
        with self._lock:
            self._subscriptions[token] = subscription
        thread.start()
        return token

    def unsubscribe(self, token: str) -> None:
        with self._lock:
            subscription = self._subscriptions.pop(token, None)
        if subscription:
            subscription.stop.set()

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        with self._lock:
            subscriptions = [s for s in self._subscriptions.values() if s.topic == topic]
            self._published += 1
        for subscription in subscriptions:
            try:
                subscription.queue.put_nowait(event)
            except queue.Full:
                with self._lock:
                    self._dropped += 1
                logger.warning("Dropping event for overloaded topic %s", topic)

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "subscriptions": len(self._subscriptions),
                "published": self._published,
                "dropped": self._dropped,
            }


research_event_bus = EventBus()
