from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.groww_client import groww_client
from app.signals.store import signal_store

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class PriceActionScanner:
    """Live SLO Price Action scanner using Groww historical candles.

    The implementation follows the reference repository's architecture:
    daily candles build a cached pattern setup, then 5-minute candles confirm
    a trigger with volume. Only confirmed BUY/SELL signals are emitted.
    """

    MIN_SCORE = 65.0
    HISTORY_DAYS = 365
    MAX_UNDERLYINGS = 50
    MIN_DAILY_BARS = 80
    CYCLE_SECONDS = 300
    MARKET_OPEN = (9, 15)
    MARKET_CLOSE = (15, 40)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_date: str | None = None
        self._signals: list[dict[str, Any]] = []
        self._alerted: set[tuple[str, str, float, str]] = set()
        self._checks = 0
        self._daily_requests = 0
        self._intraday_requests = 0
        self._errors = 0
        self._last_error: str | None = None
        self._last_scan: str | None = None
        self._last_signal: str | None = None

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "mode": "LIVE_GROWW_PRICE_ACTION",
                "minimum_score": self.MIN_SCORE,
                "cached_underlyings": len(self._cache),
                "cache_date": self._cache_date,
                "checks": self._checks,
                "daily_requests": self._daily_requests,
                "intraday_requests": self._intraday_requests,
                "errors": self._errors,
                "last_error": self._last_error,
                "last_scan": self._last_scan,
                "last_signal": self._last_signal,
                "signals": list(self._signals[:50]),
            }

    @staticmethod
    def _market_open() -> bool:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        return PriceActionScanner.MARKET_OPEN <= (now.hour, now.minute) <= PriceActionScanner.MARKET_CLOSE

    @staticmethod
    def _parse_candles(payload: Any) -> list[dict[str, float | str]]:
        candles = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(candles, list):
            return []
        out: list[dict[str, float | str]] = []
        for c in candles:
            if not isinstance(c, (list, tuple)) or len(c) < 6:
                continue
            try:
                ts = c[0]
                row = {
                    "ts": str(ts),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                out.append(row)
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _pivots(rows: list[dict[str, float | str]], window: int = 3) -> list[tuple[int, float, str]]:
        pivots: list[tuple[int, float, str]] = []
        for i in range(window, len(rows) - window):
            high = float(rows[i]["high"])
            low = float(rows[i]["low"])
            highs = [float(x["high"]) for x in rows[i - window : i + window + 1]]
            lows = [float(x["low"]) for x in rows[i - window : i + window + 1]]
            if high >= max(highs):
                pivots.append((i, high, "H"))
            if low <= min(lows):
                pivots.append((i, low, "L"))
        pivots.sort(key=lambda x: x[0])
        clean: list[tuple[int, float, str]] = []
        for p in pivots:
            if clean and clean[-1][2] == p[2]:
                if (p[2] == "H" and p[1] > clean[-1][1]) or (p[2] == "L" and p[1] < clean[-1][1]):
                    clean[-1] = p
            else:
                clean.append(p)
        return clean

    @staticmethod
    def _pattern(name: str, direction: str, score: float, status: str, points: dict[str, Any]) -> dict[str, Any]:
        return {"name": name, "direction": direction, "score": round(score, 2), "status": status, "points": points}

    @classmethod
    def _detect_patterns(cls, rows: list[dict[str, float | str]]) -> list[dict[str, Any]]:
        p = cls._pivots(rows)
        if len(p) < 2:
            return []
        close = float(rows[-1]["close"])
        patterns: list[dict[str, Any]] = []
        a = p[-2]
        avg_vol = sum(float(x["volume"]) for x in rows[-20:]) / max(1, min(20, len(rows)))
        vol_ratio = float(rows[-1]["volume"]) / avg_vol if avg_vol else 0.0
        dist = abs(close - a[1]) / max(abs(a[1]), 1e-9)
        if a[2] == "H" and close > a[1]:
            patterns.append(cls._pattern("BREAKOUT", "BUY", min(90, 65 + (10 if vol_ratio >= 1.5 else 0) + (5 if dist <= .02 else 0)), "CONFIRMED", {"level": a[1]}))
        if a[2] == "L" and close < a[1]:
            patterns.append(cls._pattern("BREAKDOWN", "SELL", min(90, 65 + (10 if vol_ratio >= 1.5 else 0) + (5 if dist <= .02 else 0)), "CONFIRMED", {"level": a[1]}))

        if len(p) >= 5:
            a, b, c, d, e = p[-5:]
            kinds = [x[2] for x in (a, b, c, d, e)]
            if kinds == ["H", "L", "H", "L", "H"] and c[1] > a[1] and c[1] > e[1] and abs(a[1] - e[1]) / max(abs(c[1]), 1e-9) <= .06:
                neckline = (b[1] + d[1]) / 2
                score = max(0, 100 - abs(a[1] - e[1]) / max(abs(c[1]), 1e-9) * 800)
                status = "CONFIRMED" if close < neckline else "FORMING"
                patterns.append(cls._pattern("HEAD AND SHOULDERS", "SELL", score, status, {"NECKLINE": neckline}))
            if kinds == ["L", "H", "L", "H", "L"] and c[1] < a[1] and c[1] < e[1] and abs(a[1] - e[1]) / max(abs(c[1]), 1e-9) <= .06:
                neckline = (b[1] + d[1]) / 2
                score = max(0, 100 - abs(a[1] - e[1]) / max(abs(c[1]), 1e-9) * 800)
                status = "CONFIRMED" if close > neckline else "FORMING"
                patterns.append(cls._pattern("INVERSE HEAD AND SHOULDERS", "BUY", score, status, {"NECKLINE": neckline}))

        if len(p) >= 6:
            q = p[-6:]
            highs = [x[1] for x in q if x[2] == "H"]
            lows = [x[1] for x in q if x[2] == "L"]
            if len(highs) >= 3 and len(lows) >= 3:
                dh = highs[-1] - highs[0]
                dl = lows[-1] - lows[0]
                if dh < 0 and dl > 0:
                    patterns.append(cls._pattern("SYMMETRICAL TRIANGLE", "BUY", 71, "FORMING", {"highs": highs, "lows": lows}))
                elif dh < 0 and abs(dl) < abs(dh) * .35:
                    patterns.append(cls._pattern("DESCENDING TRIANGLE", "SELL", 71, "FORMING", {"highs": highs, "lows": lows}))
                elif dl > 0 and abs(dh) < abs(dl) * .35:
                    patterns.append(cls._pattern("ASCENDING TRIANGLE", "BUY", 71, "FORMING", {"highs": highs, "lows": lows}))
                elif dh < 0 and dl < 0:
                    patterns.append(cls._pattern("RISING WEDGE", "SELL", 73, "FORMING", {"highs": highs, "lows": lows}))
                elif dh > 0 and dl > 0:
                    patterns.append(cls._pattern("FALLING WEDGE", "BUY", 73, "FORMING", {"highs": highs, "lows": lows}))

        if len(rows) >= 30:
            impulse = rows[-30:-15]
            flag = rows[-15:]
            move = (float(impulse[-1]["close"]) - float(impulse[0]["close"])) / max(abs(float(impulse[0]["close"])), 1e-9)
            compression = (max(float(x["high"]) for x in flag) - min(float(x["low"]) for x in flag)) / max(sum(float(x["close"]) for x in flag) / len(flag), 1e-9)
            slope = float(flag[-1]["close"]) - float(flag[0]["close"])
            opposite = (move > 0 and slope < 0) or (move < 0 and slope > 0)
            if abs(move) >= .06 and compression < abs(move) * .5 and opposite:
                patterns.append(cls._pattern("FLAG/PENNANT", "BUY" if move > 0 else "SELL", 74, "FORMING", {"impulse_pct": round(move * 100, 2)}))

        if len(rows) >= 60:
            x = rows[-60:]
            first = float(x[0]["close"])
            middle = float(x[30]["close"])
            last = float(x[-1]["close"])
            if middle < first * .94 and last > middle * 1.04:
                patterns.append(cls._pattern("ROUNDING BOTTOM", "BUY", 70, "FORMING", {"left": first, "bottom": middle, "right": last}))
            if middle > first * 1.06 and last < middle * .96:
                patterns.append(cls._pattern("ROUNDING TOP", "SELL", 70, "FORMING", {"left": first, "top": middle, "right": last}))

        return sorted(patterns, key=lambda x: float(x["score"]), reverse=True)

    @staticmethod
    def _enrich(rows: list[dict[str, float | str]], pattern_score: float) -> dict[str, Any]:
        closes = [float(x["close"]) for x in rows]
        close = closes[-1]
        ema20 = closes[0]
        ema50 = closes[0]
        a20 = 2 / 21
        a50 = 2 / 51
        for value in closes:
            ema20 = value * a20 + ema20 * (1 - a20)
            ema50 = value * a50 + ema50 * (1 - a50)
        avg20 = sum(float(x["volume"]) for x in rows[-20:]) / max(1, min(20, len(rows)))
        volume_ratio = float(rows[-1]["volume"]) / avg20 if avg20 else 0.0
        trend = 20.0 if ((close > ema20 > ema50) or (close < ema20 < ema50)) else 8.0
        volume = min(15.0, max(0.0, volume_ratio * 10.0))
        recent = rows[-50:]
        high = max(float(x["high"]) for x in recent)
        low = min(float(x["low"]) for x in recent)
        span = high - low
        fib_level = None
        fib_score = 6.0
        if span > 0:
            ratio = (close - low) / span
            fib_level = min((.382, .5, .618, .786), key=lambda x: abs(x - ratio))
            distance = abs(ratio - fib_level)
            fib_score = 10.0 if distance <= .025 else 8.0 if distance <= .05 else 6.0
        total = min(100.0, pattern_score * .55 + trend + volume + fib_score)
        return {
            "score": round(total, 2), "close": close, "ema20": round(ema20, 2), "ema50": round(ema50, 2),
            "volume_ratio": round(volume_ratio, 2), "fib_level": fib_level,
        }

    @staticmethod
    def _trigger(pattern: dict[str, Any]) -> tuple[float | None, float | None]:
        points = pattern["points"]
        if pattern["name"] in {"BREAKOUT", "BREAKDOWN"}:
            level = float(points["level"])
        elif pattern["name"] in {"HEAD AND SHOULDERS", "INVERSE HEAD AND SHOULDERS"}:
            level = float(points["NECKLINE"])
        else:
            level = float(points.get("D") or points.get("level") or 0)
        if level <= 0:
            return None, None
        return (level, None) if pattern["direction"] == "BUY" else (None, level)

    @classmethod
    def _candidate(cls, underlying: str, rows: list[dict[str, float | str]]) -> dict[str, Any] | None:
        patterns = cls._detect_patterns(rows)
        enriched = []
        for pattern in patterns:
            metrics = cls._enrich(rows, float(pattern["score"]))
            item = {**pattern, **metrics}
            enriched.append(item)
        candidates = [x for x in enriched if float(x["score"]) >= cls.MIN_SCORE]
        if not candidates:
            return None
        buys = [x for x in candidates if x["direction"] == "BUY"]
        sells = [x for x in candidates if x["direction"] == "SELL"]
        best = max(candidates, key=lambda x: float(x["score"]))
        if buys and sells:
            return None
        if best["status"] != "CONFIRMED":
            return None
        buy_above, sell_below = cls._trigger(best)
        level = buy_above if best["direction"] == "BUY" else sell_below
        if level is None:
            return None
        return {
            "underlying": underlying, "signal": best["direction"], "pattern": best["name"],
            "score": best["score"], "status": best["status"], "buy_above": buy_above, "sell_below": sell_below,
            "trigger_level": level, "trigger_state": "WAITING_5M_CONFIRMATION", "daily_close": best["close"],
            "ema20": best["ema20"], "ema50": best["ema50"], "volume_ratio": best["volume_ratio"],
            "fib_level": best["fib_level"], "reason": f"Confirmed {best['name']}; waiting for 5-minute trigger with volume.",
        }

    def _universe(self) -> list[str]:
        rows = groww_client.fno_instruments(active_only=True)
        names = sorted({str(r.get("underlying_symbol") or "").strip().upper() for r in rows if r.get("underlying_symbol")})
        preferred = [str(x.get("underlying") or "").upper() for x in __import__("app.intelligence.fno_scanner", fromlist=["fno_scanner"]).fno_scanner.stats.get("top_underlyings", [])]
        ordered = [x for x in preferred if x in names]
        ordered += [x for x in names if x not in ordered]
        return ordered[: self.MAX_UNDERLYINGS]

    def _build_cache(self) -> None:
        today = datetime.now(IST).date()
        end = today + timedelta(days=1)
        start = today - timedelta(days=self.HISTORY_DAYS)
        cache: dict[str, dict[str, Any]] = {}
        for underlying in self._universe():
            try:
                payload = groww_client.historical_candles(
                    f"NSE-{underlying}", f"{start} 09:15:00", f"{end} 15:40:00", "CASH", "1day"
                )
                rows = self._parse_candles(payload)
                with self._lock:
                    self._daily_requests += 1
                if len(rows) < self.MIN_DAILY_BARS:
                    continue
                candidate = self._candidate(underlying, rows)
                if candidate:
                    cache[underlying] = {"daily_rows": rows, "candidate": candidate}
            except Exception as exc:
                with self._lock:
                    self._daily_requests += 1
                    self._errors += 1
                    self._last_error = f"{underlying}: {exc}"
                logger.exception("Price-action daily analysis failed for %s", underlying)
        with self._lock:
            self._cache = cache
            self._cache_date = today.isoformat()

    def _emit(self, signal: dict[str, Any]) -> None:
        now = datetime.now(IST)
        key = (signal["underlying"], signal["signal"], round(float(signal["trigger_level"]), 4), now.date().isoformat())
        with self._lock:
            if key in self._alerted:
                return
            self._alerted.add(key)
        item = {
            **signal,
            "symbol": signal["underlying"],
            "price": signal["price"],
            "created_at": now.isoformat(),
            "data_sources": ["GROWW_HISTORICAL_DAILY", "GROWW_HISTORICAL_5MIN"],
            "research_only": True,
            "trading": "DISABLED",
        }
        try:
            signal_store.insert_many([{
                "signal_key": f"PRICE_ACTION:{key[0]}:{key[1]}:{key[2]}:{key[3]}",
                "created_at": now,
                "symbol": item["symbol"], "underlying": item["underlying"], "instrument_type": "PRICE_ACTION",
                "ltp": item["price"], "direction": item["signal"], "bias": "BULLISH" if item["signal"] == "BUY" else "BEARISH",
                "score": item["score"], "confidence": "HIGH" if item["score"] >= 80 else "MEDIUM",
                "event": "PRICE_ACTION", "evidence": [item["pattern"], item["reason"]], "payload": item,
            }])
        except Exception as exc:
            logger.warning("Price-action signal persistence failed: %s", exc)
        with self._lock:
            self._signals.insert(0, item)
            self._signals = self._signals[:50]
            self._last_signal = now.isoformat()

    def _scan_once(self) -> None:
        if not self._cache_date or self._cache_date != datetime.now(IST).date().isoformat():
            self._build_cache()
        signals: list[dict[str, Any]] = []
        for underlying, cached in list(self._cache.items()):
            candidate = cached["candidate"]
            try:
                end = datetime.now(IST)
                start = end - timedelta(days=2)
                payload = groww_client.historical_candles(
                    f"NSE-{underlying}", start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"), "CASH", "5minute"
                )
                rows5 = self._parse_candles(payload)
                with self._lock:
                    self._intraday_requests += 1
                if len(rows5) < 10:
                    continue
                close = float(rows5[-1]["close"])
                avg = sum(float(x["volume"]) for x in rows5[-20:]) / min(20, len(rows5))
                vol_ratio = float(rows5[-1]["volume"]) / avg if avg else 0.0
                level = float(candidate["trigger_level"])
                crossed = (candidate["signal"] == "BUY" and close > level) or (candidate["signal"] == "SELL" and close < level)
                if not crossed or vol_ratio < 1.0:
                    continue
                signal = {
                    **candidate,
                    "price": close,
                    "close_5min": close,
                    "vol_ratio_5min": round(vol_ratio, 2),
                    "trigger_state": "TRIGGERED TODAY",
                    "reason": f"{candidate['reason']} 5-minute close crossed trigger with volume ratio {vol_ratio:.2f}x.",
                    "time": str(rows5[-1]["ts"]),
                }
                signals.append(signal)
            except Exception as exc:
                with self._lock:
                    self._intraday_requests += 1
                    self._errors += 1
                    self._last_error = f"{underlying}: {exc}"
        for signal in signals:
            self._emit(signal)
        with self._lock:
            self._checks += 1
            self._last_scan = datetime.now(IST).isoformat()

    def _run(self) -> None:
        while self._running:
            try:
                if self._market_open():
                    self._scan_once()
                    deadline = time.monotonic() + self.CYCLE_SECONDS
                else:
                    deadline = time.monotonic() + 30
            except Exception as exc:
                with self._lock:
                    self._errors += 1
                    self._last_error = str(exc)
                logger.exception("Price-action scanner cycle failed")
                deadline = time.monotonic() + 30
            while self._running and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.1, deadline - time.monotonic())))
        self._running = False

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.running:
                return self.stats
            if not groww_client.configured:
                raise RuntimeError("Groww credentials are not configured")
            self._running = True
            self._thread = threading.Thread(target=self._run, name="price-action-scanner", daemon=True)
            self._thread.start()
        return self.stats

    def stop(self) -> dict[str, Any]:
        self._running = False
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        return self.stats


price_action_scanner = PriceActionScanner()
