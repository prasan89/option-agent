from __future__ import annotations

from dataclasses import dataclass

from app.flow.models import FlowSignal, MarketSnapshot


@dataclass(slots=True)
class _State:
    price: float | None = None
    oi: float | None = None
    volume: float | None = None


class FlowDetector:
    """Deterministic, explainable flow inference from public quote/depth data.

    This does not identify traders or institutional participants. It estimates
    whether observed activity is more consistent with aggressive buying or
    selling using LTP-vs-book, depth imbalance, price movement, OI and volume.
    """

    def __init__(self) -> None:
        self._state: dict[str, _State] = {}

    @staticmethod
    def _pct_change(current: float | None, previous: float | None) -> float | None:
        if current is None or previous is None or previous == 0:
            return None
        return (current - previous) / abs(previous) * 100.0

    @staticmethod
    def _depth_imbalance(bid_qty: float, ask_qty: float) -> float:
        total = bid_qty + ask_qty
        if total <= 0:
            return 0.0
        return (bid_qty - ask_qty) / total

    def update(self, snapshot: MarketSnapshot) -> FlowSignal | None:
        if snapshot.ltp is None:
            return None

        previous = self._state.get(snapshot.token, _State())
        price_change = self._pct_change(snapshot.ltp, previous.price) or 0.0
        oi_change = self._pct_change(snapshot.oi, previous.oi)
        volume_change = self._pct_change(snapshot.volume, previous.volume)
        imbalance = self._depth_imbalance(snapshot.bid_qty, snapshot.ask_qty)

        aggressive_side = "NEUTRAL"
        if snapshot.best_ask is not None and snapshot.ltp >= snapshot.best_ask:
            aggressive_side = "BUY"
        elif snapshot.best_bid is not None and snapshot.ltp <= snapshot.best_bid:
            aggressive_side = "SELL"

        # Price/depth provide the directional core; OI and volume are
        # confirmation features, not proof of opening/closing positions.
        directional = price_change * 12.0 + imbalance * 35.0
        if aggressive_side == "BUY":
            directional += 20.0
        elif aggressive_side == "SELL":
            directional -= 20.0

        if volume_change is not None and volume_change > 0:
            directional += 8.0 if directional >= 0 else -8.0
        if oi_change is not None and abs(oi_change) >= 0.5:
            directional += 5.0 if directional >= 0 else -5.0

        score = min(100.0, abs(directional))
        side = "BUY" if directional >= 0 else "SELL"

        # Ignore tiny quote noise. Signals begin at 35/100.
        if score < 35.0:
            side = "NEUTRAL"

        evidence: list[str] = []
        if aggressive_side != "NEUTRAL":
            evidence.append(f"ltp_at_{aggressive_side.lower()}_book")
        if abs(imbalance) >= 0.20:
            evidence.append("depth_imbalance")
        if abs(price_change) >= 0.05:
            evidence.append("price_momentum")
        if volume_change is not None and volume_change >= 5.0:
            evidence.append("volume_expansion")
        if oi_change is not None and abs(oi_change) >= 0.5:
            evidence.append("oi_change")

        self._state[snapshot.token] = _State(snapshot.ltp, snapshot.oi, snapshot.volume)

        if side == "NEUTRAL":
            return None

        event = "AGGRESSIVE_BUY" if side == "BUY" else "AGGRESSIVE_SELL"
        if oi_change is not None and oi_change > 0.5:
            event += "_OI_RISING"
        elif oi_change is not None and oi_change < -0.5:
            event += "_OI_FALLING"

        confidence = "HIGH" if score >= 70 else "MEDIUM"
        return FlowSignal(
            token=snapshot.token,
            timestamp_ms=snapshot.timestamp_ms,
            side=side,
            score=score,
            confidence=confidence,
            event=event,
            price_change_pct=price_change,
            depth_imbalance=imbalance,
            oi_change_pct=oi_change,
            volume_change_pct=volume_change,
            evidence=evidence,
            metadata=snapshot.metadata or {},
        )
