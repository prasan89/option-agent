from __future__ import annotations

from typing import Any


class PriceActionPatternDetector:
    """Dependency-free structural pattern detector for 5-minute OHLCV candles.

    Patterns are confirmed on candle close. The detector returns candidates
    with a trigger level and quality score; indicator and volume confirmation
    are applied by the scanner.
    """

    @staticmethod
    def _close(rows: list[dict[str, Any]], i: int) -> float:
        return float(rows[i]["close"])

    @staticmethod
    def _high(rows: list[dict[str, Any]], i: int) -> float:
        return float(rows[i]["high"])

    @staticmethod
    def _low(rows: list[dict[str, Any]], i: int) -> float:
        return float(rows[i]["low"])

    @staticmethod
    def _range(rows: list[dict[str, Any]], start: int, end: int) -> tuple[float, float]:
        return min(float(r["low"]) for r in rows[start:end]), max(float(r["high"]) for r in rows[start:end])

    @classmethod
    def _pivots(cls, rows: list[dict[str, Any]], start: int, end: int, span: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
        highs: list[tuple[int, float]] = []
        lows: list[tuple[int, float]] = []
        for i in range(start + span, end - span):
            h = cls._high(rows, i)
            l = cls._low(rows, i)
            if h >= max(cls._high(rows, j) for j in range(i - span, i + span + 1)):
                highs.append((i, h))
            if l <= min(cls._low(rows, j) for j in range(i - span, i + span + 1)):
                lows.append((i, l))
        return highs, lows

    @staticmethod
    def _norm_slope(points: list[tuple[int, float]]) -> float:
        if len(points) < 2:
            return 0.0
        dx = points[-1][0] - points[0][0]
        if dx <= 0:
            return 0.0
        base = max(abs(sum(p[1] for p in points) / len(points)), 1e-9)
        return (points[-1][1] - points[0][1]) / dx / base * 100.0

    @staticmethod
    def _near(a: float, b: float, tolerance: float) -> bool:
        return abs(a - b) / max(abs(b), 1e-9) <= tolerance

    @classmethod
    def _head_shoulders(cls, rows: list[dict[str, Any]], i: int, inverse: bool = False) -> list[dict[str, Any]]:
        if i < 45:
            return []
        start = max(0, i - 100)
        highs, lows = cls._pivots(rows, start, i, 2)
        pivots = lows if inverse else highs
        if len(pivots) < 3:
            return []
        for left, head, right in zip(pivots[-6:-2], pivots[-5:-1], pivots[-4:]):
            if inverse:
                head_is_extreme = head[1] < left[1] and head[1] < right[1]
            else:
                head_is_extreme = head[1] > left[1] and head[1] > right[1]
            shoulder_avg = (left[1] + right[1]) / 2.0
            shoulder_ok = abs(left[1] - right[1]) / max(abs(shoulder_avg), 1e-9) < 0.025
            if not head_is_extreme or not shoulder_ok or right[0] >= i - 1:
                continue
            if inverse:
                neckline = (max(cls._high(rows, j) for j in range(left[0], head[0] + 1)) + max(cls._high(rows, j) for j in range(head[0], right[0] + 1))) / 2.0
                crossed = cls._close(rows, i - 1) <= neckline and cls._close(rows, i) > neckline
                signal, name = "BUY", "INVERSE HEAD & SHOULDERS"
            else:
                neckline = (min(cls._low(rows, j) for j in range(left[0], head[0] + 1)) + min(cls._low(rows, j) for j in range(head[0], right[0] + 1))) / 2.0
                crossed = cls._close(rows, i - 1) >= neckline and cls._close(rows, i) < neckline
                signal, name = "SELL", "HEAD & SHOULDERS"
            if crossed:
                return [{"pattern": name, "signal": signal, "trigger_level": neckline, "quality": 88.0, "detail": "Three-pivot shoulder/head/shoulder structure confirmed by neckline close."}]
        return []

    @classmethod
    def _converging_pattern(cls, rows: list[dict[str, Any]], i: int) -> list[dict[str, Any]]:
        if i < 35:
            return []
        start = i - 36
        highs, lows = cls._pivots(rows, start, i, 2)
        if len(highs) < 2 or len(lows) < 2:
            return []
        hs, ls = cls._norm_slope(highs[-4:]), cls._norm_slope(lows[-4:])
        close, previous = cls._close(rows, i), cls._close(rows, i - 1)
        upper = max(highs[-1][1], cls._high(rows, i - 1))
        lower = min(lows[-1][1], cls._low(rows, i - 1))
        # Triangle: falling/flat highs and rising/flat lows. Wedge: both
        # boundaries slope in the same direction while converging.
        triangle = hs < -0.02 and ls > 0.02
        if close > upper and previous <= upper and triangle:
            return [{"pattern": "TRIANGLE BREAKOUT", "signal": "BUY", "trigger_level": upper, "quality": 82.0, "detail": "Converging pivot highs/lows resolved upward on a 5-minute close."}]
        if close < lower and previous >= lower and triangle:
            return [{"pattern": "TRIANGLE BREAKDOWN", "signal": "SELL", "trigger_level": lower, "quality": 82.0, "detail": "Converging pivot highs/lows resolved downward on a 5-minute close."}]
        return []

    @classmethod
    def _flag(cls, rows: list[dict[str, Any]], i: int) -> list[dict[str, Any]]:
        if i < 28:
            return []
        impulse_start, pole_end = i - 22, i - 10
        pole_start_close, pole_end_close = cls._close(rows, impulse_start), cls._close(rows, pole_end)
        pole_move = (pole_end_close - pole_start_close) / max(abs(pole_start_close), 1e-9)
        if abs(pole_move) < 0.025:
            return []
        support, resistance = cls._range(rows, pole_end, i)
        cons_move = (cls._close(rows, i - 1) - pole_end_close) / max(abs(pole_end_close), 1e-9)
        if abs(cons_move) > abs(pole_move) * 0.6 + 0.005:
            return []
        close, previous = cls._close(rows, i), cls._close(rows, i - 1)
        if pole_move > 0 and close > resistance and previous <= resistance:
            return [{"pattern": "BULL FLAG BREAKOUT", "signal": "BUY", "trigger_level": resistance, "quality": 84.0, "detail": "Strong upward pole followed by compact consolidation and upside close."}]
        if pole_move < 0 and close < support and previous >= support:
            return [{"pattern": "BEAR FLAG BREAKDOWN", "signal": "SELL", "trigger_level": support, "quality": 84.0, "detail": "Strong downward pole followed by compact consolidation and downside close."}]
        return []

    @classmethod
    def _rounding(cls, rows: list[dict[str, Any]], i: int, inverse: bool = False) -> list[dict[str, Any]]:
        if i < 40:
            return []
        start, mid = i - 36, i - 18
        left, center, right_prev, close = (cls._close(rows, j) for j in (start, mid, i - 1, i))
        if not cls._near(left, right_prev, 0.035):
            return []
        amplitude = max(abs(left - center), abs(right_prev - center)) / max(abs(left), 1e-9)
        if amplitude < 0.02:
            return []
        left_mid, mid_right = cls._close(rows, mid - 8), cls._close(rows, mid + 8)
        if inverse:
            shape = center < left_mid and center < mid_right
            trigger = max(cls._high(rows, j) for j in range(start, mid))
            crossed = close > trigger and right_prev <= trigger
            if shape and crossed:
                return [{"pattern": "ROUNDING BOTTOM BREAKOUT", "signal": "BUY", "trigger_level": trigger, "quality": 78.0, "detail": "U-shaped 5-minute structure completed with resistance breakout."}]
        else:
            shape = center > left_mid and center > mid_right
            trigger = min(cls._low(rows, j) for j in range(start, mid))
            crossed = close < trigger and right_prev >= trigger
            if shape and crossed:
                return [{"pattern": "ROUNDING TOP BREAKDOWN", "signal": "SELL", "trigger_level": trigger, "quality": 78.0, "detail": "Inverted U-shaped 5-minute structure completed with support breakdown."}]
        return []

    @classmethod
    def detect(cls, rows: list[dict[str, Any]], i: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        candidates.extend(cls._head_shoulders(rows, i, inverse=False))
        candidates.extend(cls._head_shoulders(rows, i, inverse=True))
        candidates.extend(cls._converging_pattern(rows, i))
        candidates.extend(cls._flag(rows, i))
        candidates.extend(cls._rounding(rows, i, inverse=False))
        candidates.extend(cls._rounding(rows, i, inverse=True))
        candidates.sort(key=lambda x: float(x.get("quality") or 0), reverse=True)
        return candidates

    @classmethod
    def setup_candidates(cls, rows: list[dict[str, Any]], i: int) -> list[dict[str, Any]]:
        if i < 40:
            return []
        candidates: list[dict[str, Any]] = []
        return candidates
