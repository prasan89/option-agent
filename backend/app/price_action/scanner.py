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
    """Historical-only 5-minute price-action scanner.

    No websocket/live-price dependency. Signals are reconstructed from Groww
    historical 5-minute candles, so the same logic works during and after
    market hours and can be reviewed consistently.
    """

    MIN_SCORE = 65.0
    HISTORY_DAYS = 30
    MAX_UNDERLYINGS = 50
    LOOKBACK_BARS = 20
    MIN_BARS = 80
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
        self._alerted: set[tuple[str, str, str, str]] = set()
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
                "mode": "HISTORICAL_5MIN_PRICE_ACTION",
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
        return now.weekday() < 5 and PriceActionScanner.MARKET_OPEN <= (now.hour, now.minute) <= PriceActionScanner.MARKET_CLOSE

    @staticmethod
    def _parse_candles(payload: Any) -> list[dict[str, float | str]]:
        candles = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(candles, list):
            return []
        out: list[dict[str, float | str]] = []
        for candle in candles:
            if not isinstance(candle, (list, tuple)) or len(candle) < 6:
                continue
            try:
                out.append({
                    "ts": str(candle[0]), "open": float(candle[1]), "high": float(candle[2]),
                    "low": float(candle[3]), "close": float(candle[4]), "volume": float(candle[5]),
                })
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda row: str(row["ts"]))
        return out

    @staticmethod
    def _candle_datetime(ts: str) -> datetime | None:
        value = str(ts or "").strip()
        try:
            if value.isdigit():
                epoch = float(value)
                if epoch > 10_000_000_000:
                    epoch /= 1000.0
                return datetime.fromtimestamp(epoch, tz=IST)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=IST)
            return parsed.astimezone(IST)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    @classmethod
    def _candle_date(cls, ts: str) -> str | None:
        parsed = cls._candle_datetime(ts)
        return parsed.date().isoformat() if parsed else None

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
    def _session_vwap(cls, rows: list[dict[str, float | str]], index: int) -> float:
        current_date = cls._candle_date(str(rows[index]["ts"]))
        if not current_date:
            return float(rows[index]["close"])
        start = index
        while start > 0 and cls._candle_date(str(rows[start - 1]["ts"])) == current_date:
            start -= 1
        pv = 0.0
        volume = 0.0
        for row in rows[start:index + 1]:
            typical = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3.0
            vol = max(0.0, float(row["volume"]))
            pv += typical * vol
            volume += vol
        return pv / volume if volume else float(rows[index]["close"])

    @classmethod
    def _score_bar(cls, rows: list[dict[str, float | str]], index: int, direction: str, trigger_level: float) -> dict[str, Any]:
        closes = [float(row["close"]) for row in rows[:index + 1]]
        volumes = [float(row["volume"]) for row in rows]
        close = closes[-1]
        ema9 = cls._ema(closes[-30:], 9)
        ema20 = cls._ema(closes[-50:], 20)
        ema50 = cls._ema(closes[-80:], 50)
        vwap = cls._session_vwap(rows, index)
        previous_volumes = volumes[max(0, index - cls.LOOKBACK_BARS):index]
        avg_volume = sum(previous_volumes) / len(previous_volumes) if previous_volumes else 0.0
        volume_ratio = volumes[index] / avg_volume if avg_volume else 0.0
        trend_ok = ((direction == "BUY" and close > vwap and ema9 > ema20) or (direction == "SELL" and close < vwap and ema9 < ema20))
        ema50_ok = close > ema50 if direction == "BUY" else close < ema50
        volume_ok = volume_ratio >= 1.0
        score = 55.0
        if trend_ok:
            score += 15.0
        if ema50_ok:
            score += 10.0
        if volume_ok:
            score += min(15.0, volume_ratio * 10.0)
        if abs(close - trigger_level) / max(abs(trigger_level), 1e-9) <= 0.0025:
            score += 5.0
        return {
            "score": round(min(100.0, score), 2), "ema9": round(ema9, 2), "ema20": round(ema20, 2),
            "ema50": round(ema50, 2), "vwap": round(vwap, 2), "volume_ratio": round(volume_ratio, 2),
        }

    @classmethod
    def _historical_signals(cls, underlying: str, rows: list[dict[str, float | str]]) -> list[dict[str, Any]]:
        if len(rows) < cls.MIN_BARS:
            return []
        results: list[dict[str, Any]] = []
        for index in range(cls.LOOKBACK_BARS, len(rows)):
            current = rows[index]
            previous = rows[index - cls.LOOKBACK_BARS:index]
            close = float(current["close"])
            previous_close = float(rows[index - 1]["close"])
            resistance = max(float(row["high"]) for row in previous)
            support = min(float(row["low"]) for row in previous)
            breakout = close > resistance and previous_close <= resistance
            breakdown = close < support and previous_close >= support
            if not breakout and not breakdown:
                continue
            direction = "BUY" if breakout else "SELL"
            level = resistance if breakout else support
            metrics = cls._score_bar(rows, index, direction, level)
            if metrics["score"] < cls.MIN_SCORE:
                continue
            dt = cls._candle_datetime(str(current["ts"]))
            if not dt:
                continue
            stop = (min(float(row["low"]) for row in rows[max(0, index - 3):index + 1]) if direction == "BUY" else max(float(row["high"]) for row in rows[max(0, index - 3):index + 1]))
            risk = abs(close - stop)
            target = close + 2.0 * risk if direction == "BUY" else close - 2.0 * risk
            pattern = "5M RANGE BREAKOUT" if direction == "BUY" else "5M RANGE BREAKDOWN"
            results.append({
                "underlying": underlying, "symbol": underlying, "signal": direction, "pattern": pattern,
                "score": metrics["score"], "status": "CONFIRMED", "trigger_level": round(level, 4),
                "buy_above": round(level, 4) if direction == "BUY" else None,
                "sell_below": round(level, 4) if direction == "SELL" else None,
                "trigger_state": "HISTORICAL_5M_CONFIRMED", "price": close, "close_5min": close,
                "vol_ratio_5min": metrics["volume_ratio"], "ema9": metrics["ema9"], "ema20": metrics["ema20"},
                "ema50": metrics["ema50"], "vwap": metrics["vwap"], "fib_level": None,
                "stop_loss": round(stop, 4), "target": round(target, 4), "rr": 2.0,
                "time": str(current["ts"]), "created_at": dt.isoformat(),
                "reason": f"{pattern}; prior {cls.LOOKBACK_BARS}-bar range crossed on a 5-minute close; VWAP/trend/volume confirmation score={metrics['score']:.2f}.",
                "data_sources": ["GROWW_HISTORICAL_5MIN"], "research_only": True, "trading": "DISABLED",
            })
        return results

    @classmethod
    def _latest_setup(cls, underlying: str, rows: list[dict[str, float | str]]) -> dict[str, Any] | None:
        if len(rows) < cls.MIN_BARS:
            return None
        index = len(rows) - 1
        current = rows[index]
        previous = rows[index - cls.LOOKBACK_BARS:index]
        close = float(current["close"])
        resistance = max(float(row["high"]) for row in previous)
        support = min(float(row["low"]) for row in previous)
        if close >= resistance * 0.997:
            direction, level = "BUY", resistance
        elif close <= support * 1.003:
            direction, level = "SELL", support
        else:
            return None
        metrics = cls._score_bar(rows, index, direction, level)
        if metrics["score"] < cls.MIN_SCORE:
            return None
        dt = cls._candle_datetime(str(current["ts"]))
        if not dt:
            return None
        pattern = "5M BREAKOUT SETUP" if direction == "BUY" else "5M BREAKDOWN SETUP"
        return {
            "underlying": underlying, "symbol": underlying, "signal": direction, "pattern": pattern,
            "score": metrics["score"], "status": "SETUP", "trigger_level": round(level, 4),
            "trigger_state": "WAITING_5M_BREAK_OR_NEXT_CLOSE", "price": close, "close_5min": close,
            "vol_ratio_5min": metrics["volume_ratio"], "ema9": metrics["ema9"], "ema20": metrics["ema20"],
            "ema50": metrics["ema50"], "vwap": metrics["vwap"], "fib_level": None,
            "time": str(current["ts"]), "created_at": dt.isoformat(),
            "reason": f"{pattern}; historical 5-minute candle is within 0.3% of the prior {cls.LOOKBACK_BARS}-bar trigger.",
            "data_sources": ["GROWW_HISTORICAL_5MIN"], "research_only": True, "trading": "DISABLED",
        }

    def _universe(self) -> list[str]:
        rows = groww_client.fno_instruments(active_only=True)
        names = sorted({str(row.get("underlying_symbol") or "").strip().upper() for row in rows if row.get("underlying_symbol")})
        try:
            from app.intelligence.fno_scanner import fno_scanner
            preferred = [str(item.get("underlying") or "").upper() for item in fno_scanner.stats.get("top_underlyings", [])]
        except Exception:
            preferred = []
        ordered = [name for name in preferred if name in names]
        ordered += [name for name in names if name not in ordered]
        return ordered[:self.MAX_UNDERLYINGS]

    def _build_cache(self) -> None:
        today = datetime.now(IST).date()
        start = today - timedelta(days=self.HISTORY_DAYS - 1)
        end = today
        cache: dict[str, dict[str, Any]] = {}
        request_errors = 0
        for underlying in self._universe():
            try:
                payload = groww_client.historical_candles(f"NSE-{underlying}", f"{start} 09:15:00", f"{end} 15:40:00", "CASH", "5minute")
                rows = self._parse_candles(payload)
                with self._lock:
                    self._intraday_requests += 1
                confirmed = self._historical_signals(underlying, rows)
                setup = self._latest_setup(underlying, rows)
                latest_confirmed = confirmed[-1] if confirmed else None
                if latest_confirmed or setup:
                    cache[underlying] = {"rows": rows, "confirmed": latest_confirmed, "setup": setup}
            except Exception as exc:
                request_errors += 1
                with self._lock:
                    self._intraday_requests += 1
                    self._errors += 1
                    self._last_error = f"{underlying}: {exc}"
                logger.exception("Historical 5-minute price-action analysis failed for %s", underlying)
        with self._lock:
            self._cache = cache
            self._cache_date = today.isoformat() if request_errors == 0 else None
            self._setups = sum(1 for item in cache.values() if item.get("setup"))
            self._triggered = sum(1 for item in cache.values() if item.get("confirmed"))
        logger.info("Historical 5-minute price-action cache built: underlyings=%s requests=%s errors=%s", len(cache), self._intraday_requests, request_errors)

    def _emit(self, signal: dict[str, Any]) -> bool:
        now = datetime.now(IST)
        key = (str(signal["underlying"]), str(signal["signal"]), str(signal.get("time") or signal.get("created_at") or ""), str(signal.get("status") or "SETUP"))
        with self._lock:
            if key in self._alerted:
                return False
            self._alerted.add(key)
        item = {**signal, "symbol": signal["underlying"], "price": signal["price"], "created_at": signal.get("created_at") or now.isoformat(), "research_only": True, "trading": "DISABLED"}
        try:
            signal_store.insert_many([{
                "signal_key": f"PRICE_ACTION:{key[0]}:{key[1]}:{key[2]}:{key[3]}", "created_at": now,
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
        return True

    def _scan_once(self) -> None:
        today = datetime.now(IST).date().isoformat()
        if self._cache_date != today or self._market_open():
            self._build_cache()
        signals = []
        for cached in list(self._cache.values()):
            signal = cached.get("confirmed") or cached.get("setup")
            if signal:
                signals.append(signal)
        signals.sort(key=lambda item: (float(item.get("score") or 0), str(item.get("time") or "")), reverse=True)
        for signal in signals:
            self._emit(signal)
        with self._lock:
            self._checks += 1
            self._last_scan = datetime.now(IST).isoformat()

    def _run(self) -> None:
        while self._running:
            try:
                self._scan_once()
                deadline = time.monotonic() + self.CYCLE_SECONDS
            except Exception as exc:
                with self._lock:
                    self._errors += 1
                    self._last_error = str(exc)
                logger.exception("Historical price-action scanner cycle failed")
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
