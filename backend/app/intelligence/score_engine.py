from __future__ import annotations

import logging
import threading
from typing import Any

from app.core.event_bus import research_event_bus

logger = logging.getLogger(__name__)


class IntelligenceScoreEngine:
    """Fuse microstructure flow signals into a ranked, explainable score."""

    INPUT_TOPIC = "flow.signals"
    OUTPUT_TOPIC = "intelligence.signals"

    def __init__(self) -> None:
        self._running = False
        self._latest: dict[str, dict[str, Any]] = {}
        self._signals = 0
        self._errors = 0
        self._lock = threading.Lock()
        self._subscription: str | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            ranked = sorted(self._latest.values(), key=lambda x: abs(float(x.get("intelligence_score", 0))), reverse=True)
            return {"running": self.running, "signals": self._signals, "errors": self._errors, "top_signals": ranked[:50]}

    @staticmethod
    def _score(signal: dict[str, Any]) -> tuple[float, str, list[str]]:
        base = float(signal.get("score") or 0.0)
        confidence = str(signal.get("confidence") or "LOW").upper()
        score = base * {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.5}.get(confidence, 0.5)
        evidence = list(signal.get("evidence") or [])
        if abs(float(signal.get("price_change_pct") or 0)) >= 0.15:
            score *= 1.15; evidence.append("price_momentum")
        if abs(float(signal.get("depth_imbalance") or 0)) >= 0.25:
            score *= 1.10; evidence.append("depth_imbalance")
        score = max(-100.0, min(100.0, score))
        bias = "BULLISH" if score >= 20 else "BEARISH" if score <= -20 else "NEUTRAL"
        return round(score, 2), bias, list(dict.fromkeys(evidence))

    def _on_signal(self, signal: dict[str, Any]) -> None:
        try:
            score, bias, evidence = self._score(signal)
            enriched = {**signal, "intelligence_score": score, "bias": bias, "evidence": evidence,
                        "research_only": True,
                        "warning": "Inference from public market observations; not identification of institutional orders and not a trade recommendation."}
            token = str(signal.get("token") or "")
            if not token: return
            with self._lock:
                self._latest[token] = enriched; self._signals += 1
            research_event_bus.publish(self.OUTPUT_TOPIC, enriched)
        except Exception:
            self._errors += 1
            logger.exception("Failed to score flow signal")

    def start(self) -> None:
        if self.running: raise RuntimeError("Intelligence score engine is already running")
        self._running = True
        self._subscription = research_event_bus.subscribe(self.INPUT_TOPIC, self._on_signal)

    def stop(self) -> None:
        self._running = False
        if self._subscription:
            research_event_bus.unsubscribe(self._subscription); self._subscription = None


intelligence_score_engine = IntelligenceScoreEngine()
