from __future__ import annotations

import json
import logging
import threading
from typing import Any

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)


class IntelligenceScoreEngine:
    """Fuse microstructure flow signals into a ranked, explainable score."""

    INPUT_STREAM = "flow:signals"
    OUTPUT_STREAM = "intelligence:signals"

    def __init__(self) -> None:
        self._redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_id = "$"
        self._latest: dict[str, dict[str, Any]] = {}
        self._signals = 0
        self._errors = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            ranked = sorted(self._latest.values(), key=lambda x: x["intelligence_score"], reverse=True)
            return {"running": self.running, "signals": self._signals, "errors": self._errors, "top_signals": ranked[:50]}

    @staticmethod
    def _score(signal: dict[str, Any]) -> tuple[float, str, list[str]]:
        base = float(signal.get("score") or 0.0)
        confidence = str(signal.get("confidence") or "LOW").upper()
        multiplier = {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.5}.get(confidence, 0.5)
        score = base * multiplier
        evidence = list(signal.get("evidence") or [])
        if abs(float(signal.get("price_change_pct") or 0)) >= 0.15:
            score *= 1.15
            evidence.append("price_momentum")
        if abs(float(signal.get("depth_imbalance") or 0)) >= 0.25:
            score *= 1.10
            evidence.append("depth_imbalance")
        score = max(-100.0, min(100.0, score))
        bias = "BULLISH" if score >= 20 else "BEARISH" if score <= -20 else "NEUTRAL"
        return round(score, 2), bias, list(dict.fromkeys(evidence))

    def _process(self, raw: str) -> None:
        signal = json.loads(raw)
        score, bias, evidence = self._score(signal)
        enriched = {
            **signal,
            "intelligence_score": score,
            "bias": bias,
            "evidence": evidence,
            "research_only": True,
            "warning": "Inference from public market observations; not identification of institutional orders and not a trade recommendation.",
        }
        token = str(signal.get("token") or "")
        if not token:
            return
        with self._lock:
            self._latest[token] = enriched
            self._signals += 1
        self._redis.xadd(self.OUTPUT_STREAM, {"signal": json.dumps(enriched, separators=(",", ":"))}, maxlen=100_000, approximate=True)

    def _consume(self) -> None:
        try:
            while self._running:
                records = self._redis.xread({self.INPUT_STREAM: self._last_id}, count=100, block=1000)
                for _, entries in records:
                    for entry_id, values in entries:
                        self._last_id = entry_id
                        try:
                            self._process(values.get("signal", "{}"))
                        except Exception:
                            self._errors += 1
                            logger.exception("Failed to score flow signal")
        finally:
            self._running = False

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Intelligence score engine is already running")
        self._running = True
        self._last_id = "$"
        self._thread = threading.Thread(target=self._consume, name="intelligence-score", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False


intelligence_score_engine = IntelligenceScoreEngine()
