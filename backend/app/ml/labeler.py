from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


class OutcomeLabeler:
    """Create forward-looking labels without leaking future data into features."""

    HORIZONS = (1, 5, 15, 30)

    def __init__(self) -> None:
        self._prices: dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=240))
        self._rows: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)

    def observe(self, symbol: str, timestamp_ms: int, price: float, features: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not symbol or price <= 0 or timestamp_ms <= 0:
            return []
        history = self._prices[symbol]
        history.append((timestamp_ms, price))
        if features is not None:
            self._rows[symbol][timestamp_ms] = dict(features)

        labels: list[dict[str, Any]] = []
        for horizon in self.HORIZONS:
            target_ms = timestamp_ms + horizon * 60_000
            future = next(((ts, px) for ts, px in history if ts >= target_ms), None)
            if future is None:
                continue
            future_ts, future_price = future
            ret = (future_price - price) / price * 100.0
            labels.append({
                "symbol": symbol,
                "timestamp_ms": timestamp_ms,
                "horizon_minutes": horizon,
                "entry_price": price,
                "future_price": future_price,
                "return_pct": round(ret, 6),
                "direction": "UP" if ret > 0 else "DOWN" if ret < 0 else "FLAT",
                "future_timestamp_ms": future_ts,
                "label": 1 if ret > 0 else 0,
                "features": dict(self._rows[symbol].get(timestamp_ms, {})),
            })

        # Bound auxiliary feature history independently of price history.
        cutoff = timestamp_ms - 240 * 60_000
        self._rows[symbol] = {ts: row for ts, row in self._rows[symbol].items() if ts >= cutoff}
        return labels


def label_from_series(symbol: str, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Batch-label a chronological price series for backtests and datasets."""
    labeler = OutcomeLabeler()
    output: list[dict[str, Any]] = []
    for row in sorted(observations, key=lambda x: int(x.get("timestamp_ms", 0))):
        output.extend(labeler.observe(
            symbol,
            int(row["timestamp_ms"]),
            float(row["ltp"]),
            row.get("features") if isinstance(row.get("features"), dict) else None,
        ))
    return output
