from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MarketSnapshot:
    token: str
    timestamp_ms: int
    ltp: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    oi: float | None = None
    volume: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class FlowSignal:
    token: str
    timestamp_ms: int
    side: str
    score: float
    confidence: str
    event: str
    price_change_pct: float
    depth_imbalance: float
    oi_change_pct: float | None
    volume_change_pct: float | None
    evidence: list[str]
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "timestamp_ms": self.timestamp_ms,
            "side": self.side,
            "score": round(self.score, 4),
            "confidence": self.confidence,
            "event": self.event,
            "price_change_pct": round(self.price_change_pct, 6),
            "depth_imbalance": round(self.depth_imbalance, 4),
            "oi_change_pct": None if self.oi_change_pct is None else round(self.oi_change_pct, 6),
            "volume_change_pct": None if self.volume_change_pct is None else round(self.volume_change_pct, 6),
            "evidence": self.evidence,
            "metadata": self.metadata,
        }
