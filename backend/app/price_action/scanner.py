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
    """Research-only price-action scanner using Groww daily and 5-minute candles."""

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
        self._setups = 0
        self._triggered = 0

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
                "setups": self._setups,
                "triggered": self._triggered,
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
                out.append({
                    "ts": str(c[0]), "open": float(c[1]), "high": float(c[2]),
                    "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
                })
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _ema(values: list[float], period: int) -> float:
        if not values:
            return 0.0
        alpha = 2.0 / (period + 1.0)
        value = values[0]
        for item in values[1:]:
            value = item * alpha + value * (1.0 - alpha)
        return value

    @classmethod
    def _daily_setup(cls, underlying: str, rows: list[dict[str, float | str]]) -> dict[str, Any] | None:
        if len(rows) < cls.MIN_DAILY_BARS:
            return None
        closes = [float(x["close"]) for x in rows]
        highs = [float(x["high"]) for x in rows]
        lows = [float(x["low"]) for x in rows]
        volumes = [float(x["volume"]) for x in rows]
        close = closes[-1]
        ema20 = cls._ema(closes, 20)
        ema50 = cls._ema(closes, 50)
        avg20 = sum(volumes[-20:]) / 20.0
        vol_ratio = volumes[-1] / avg20 if avg20 else 0.0
        resistance = max(highs[-21:-1])
        support = min(lows[-21:-1])

        # Prefer a genuine daily breakout/breakdown. Otherwise retain a
        # directional momentum setup so the scanner never silently becomes
        # empty merely because a textbook chart pattern is absent.
        if close > resistance:
            direction = "BUY"
            pattern = "DAILY BREAKOUT"
            level = resistance
            pattern_score = 78.0
        elif close < support:
            direction = "SELL"
            pattern = "DAILY BREAKDOWN"
            level = support
            pattern_score = 78.0
        elif close > ema20 > ema50:
            direction = "BUY"
            pattern = "BULLISH MOMENTUM SETUP"
            level = resistance
            pattern_score = 68.0
        elif close < ema20 < ema50:
            direction = "SELL"
            pattern = "BEARISH MOMENTUM SETUP"
            level = support
            pattern_score = 68.0
        else:
            # Neutral market: use the stronger side of the recent range as a
            # watch setup, but do not pretend that it is already triggered.
            up_move = (close - closes[-10]) / max(abs(closes[-10]), 1e-9)
            down_move = (closes[-10] - close) / max(abs(closes[-10]), 1e-9)
            if up_move >= down_move:
                direction, pattern, level = "BUY", "RANGE BREAKOUT WATCH", resistance
            else:
                direction, pattern, level = "SELL", "RANGE BREAKDOWN WATCH", support
            pattern_score = 65.0

        trend_score = 20.0 if ((close > ema20 > ema50) or (close < ema20 < ema50)) else 8.0
        volume_score = min(15.0, max(0.0, vol_ratio * 10.0))
        score = min(100.0, pattern_score * 0.55 + trend_score + volume_score + 6.0)
        return {
            "underlying": underlying,
            "signal": direction,
            "pattern": pattern,
            "score": round(score, 2),
            "status": "SETUP",
            "trigger_level": round(float(level), 4),
            "buy_above": round(float(level), 4) if direction == "BUY" else None,
            "sell_below": round(float(level), 4) if direction == "SELL" else None,
            "trigger_state": "WAITING_5M_CONFIRMATION",
            "daily_close": close,
            "ema20": round(ema20, 2),
            "ema50": round(ema50, 2),
            "volume_ratio": round(vol_ratio, 2),
            "fib_level": None,
            "reason": f"{pattern}; waiting for 5-minute trigger and volume confirmation.",
        }

    def _universe(self) -> list[str]:
        rows = groww_client.fno_instruments(active_only=True)
        names = sorted({str(r.get("underlying_symbol") or "").strip().upper() for r in rows if r.get("underlying_symbol")})
        try:
            from app.intelligence.fno_scanner import fno_scanner
            preferred = [str(x.get("underlying") or "").upper() for x in fno_scanner.stats.get("top_underlyings", [])]
        except Exception:
            preferred = []
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
                candidate = self._daily_setup(underlying, rows)
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
            self._setups = len(cache)
        logger.info("Price-action daily cache built: underlyings=%s requests=%s errors=%s", len(cache), self._daily_requests, self._errors)

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
                "symbol": item["symbol"],
                "underlying": item["underlying"],
                "instrument_type": "PRICE_ACTION",
                "ltp": item["price"],
                "direction": item["signal"],
                "bias": "BULLISH" if item["signal"] == "BUY" else "BEARISH",
                "score": item["score"],
                "confidence": "HIGH" if item["score"] >= 80 else "MEDIUM",
                "event": "PRICE_ACTION",
                "evidence": [item["pattern"], item["reason"]],
                "payload": item,
            }])
        except Exception as exc:
            logger.warning("Price-action signal persistence failed: %s", exc)
        with self._lock:
            self._signals.insert(0, item)
            self._signals = self._signals[:50]
            self._last_signal = now.isoformat()

    def _scan_once(self) -> None:
        today = datetime.now(IST).date().isoformat()
        if self._cache_date != today:
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
                if len(rows5) < 2:
                    continue
                close = float(rows5[-1]["close"])
                volumes = [float(x["volume"]) for x in rows5[-21:-1]] or [float(rows5[-1]["volume"])]
                avg = sum(volumes) / len(volumes)
                vol_ratio = float(rows5[-1]["volume"]) / avg if avg else 0.0
                level = float(candidate["trigger_level"])
                crossed = (candidate["signal"] == "BUY" and close >= level) or (candidate["signal"] == "SELL" and close <= level)

                signal = {
                    **candidate,
                    "price": close,
                    "close_5min": close,
                    "vol_ratio_5min": round(vol_ratio, 2),
                    "time": str(rows5[-1]["ts"]),
                }
                if crossed and vol_ratio >= 1.0:
                    signal["status"] = "CONFIRMED"
                    signal["trigger_state"] = "TRIGGERED TODAY"
                    signal["reason"] = f"{candidate['pattern']}; 5-minute close crossed trigger with volume ratio {vol_ratio:.2f}x."
                    with self._lock:
                        self._triggered += 1
                    signals.append(signal)
                elif candidate["pattern"] in {"DAILY BREAKOUT", "DAILY BREAKDOWN"}:
                    # Preserve valid daily setups in the API while waiting for
                    # the intraday confirmation instead of returning nothing.
                    signal["status"] = "SETUP"
                    signal["trigger_state"] = "WAITING_5M_CONFIRMATION"
                    signal["reason"] = f"{candidate['pattern']}; current 5-minute price has not confirmed the trigger yet."
                    signals.append(signal)
            except Exception as exc:
                with self._lock:
                    self._intraday_requests += 1
                    self._errors += 1
                    self._last_error = f"{underlying}: {exc}"
                logger.exception("Price-action 5-minute analysis failed for %s", underlying)
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
