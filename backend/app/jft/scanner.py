from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.services.groww_client import groww_client
from app.signals.store import signal_store

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class JFTScanner:
    """Historical 5-minute JFT level-cross scanner.

    Rule:
      - Previous-session classic pivot R3 is the bullish trigger.
      - A completed 5-minute close crossing from <= R3 to > R3 emits BUY CALL.
      - Stop loss is previous-session R2.
      - Previous-session classic pivot S3 is the bearish trigger.
      - A completed 5-minute close crossing from >= S3 to < S3 emits BUY PUT.
      - Stop loss is previous-session S2.

    The scanner is research-only and never places broker orders.
    """

    HISTORY_DAYS = 30
    MAX_UNDERLYINGS = 50
    MIN_BARS = 80
    CYCLE_SECONDS = 300
    MARKET_OPEN = (9, 15)
    MARKET_CLOSE = (15, 40)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._signals: list[dict[str, Any]] = []
        self._alerted: set[tuple[str, str, str, str]] = set()
        self._levels: dict[str, dict[str, Any]] = {}
        self._checks = 0
        self._requests = 0
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
                "mode": "HISTORICAL_5MIN_JFT",
                "rule": "CLOSE CROSS R3 => BUY CALL / SL R2; CLOSE CROSS S3 => BUY PUT / SL S2",
                "cached_underlyings": len(self._levels),
                "checks": self._checks,
                "requests": self._requests,
                "errors": self._errors,
                "last_error": self._last_error,
                "last_scan": self._last_scan,
                "last_signal": self._last_signal,
                "signals": list(self._signals[:100]),
            }

    @staticmethod
    def _parse(payload: Any) -> list[dict[str, Any]]:
        candles = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(candles, list):
            return []
        rows: list[dict[str, Any]] = []
        for c in candles:
            if not isinstance(c, (list, tuple)) or len(c) < 6:
                continue
            try:
                rows.append({"ts": str(c[0]), "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": float(c[5])})
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda x: str(x["ts"]))
        return rows

    @staticmethod
    def _dt(ts: str) -> datetime | None:
        try:
            value = str(ts).strip()
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
    def _day(cls, ts: str) -> str | None:
        dt = cls._dt(ts)
        return dt.date().isoformat() if dt else None

    @classmethod
    def pivot_levels(cls, high: float, low: float, close: float) -> dict[str, float]:
        p = (high + low + close) / 3.0
        return {
            "pivot": p,
            "r1": 2 * p - low,
            "r2": p + high - low,
            "r3": high + 2 * (p - low),
            "s1": 2 * p - high,
            "s2": p - high + low,
            "s3": low - 2 * (high - p),
        }

    @classmethod
    def _sessions(cls, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            day = cls._day(str(row["ts"]))
            if day:
                out[day].append(row)
        return dict(sorted(out.items()))

    @classmethod
    def _signals_for(cls, underlying: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sessions = cls._sessions(rows)
        days = list(sessions)
        results: list[dict[str, Any]] = []
        for pos in range(1, len(days)):
            prev_day, current_day = days[pos - 1], days[pos]
            previous = sessions[prev_day]
            current = sessions[current_day]
            if not previous or len(current) < 2:
                continue
            levels = cls.pivot_levels(previous[0]["high"] if False else max(float(x["high"]) for x in previous), min(float(x["low"]) for x in previous), float(previous[-1]["close"]))
            r3, r2, s3, s2 = levels["r3"], levels["r2"], levels["s3"], levels["s2"]
            previous_close = float(current[0]["open"])
            for row in current:
                close = float(row["close"])
                dt = cls._dt(str(row["ts"]))
                if not dt:
                    previous_close = close
                    continue
                if previous_close <= r3 < close:
                    results.append({
                        "underlying": underlying, "symbol": underlying, "signal": "BUY CALL", "option_action": "BUY CE",
                        "direction": "BULLISH", "pattern": "JFT R3 CROSS", "trigger": "R3", "trigger_level": round(r3, 4),
                        "stop_level": round(r2, 4), "stop_reference": "R2", "pivot": round(levels["pivot"], 4),
                        "r1": round(levels["r1"], 4), "r2": round(r2, 4), "r3": round(r3, 4),
                        "s1": round(levels["s1"], 4), "s2": round(s2, 4), "s3": round(s3, 4),
                        "price": close, "close_5min": close, "time": str(row["ts"]), "created_at": dt.isoformat(),
                        "reason": f"5M close crossed above previous-session R3 ({r3:.2f}); JFT BUY CALL. Stop loss = R2 ({r2:.2f}).",
                        "data_sources": ["GROWW_HISTORICAL_5MIN"], "research_only": True, "trading": "DISABLED",
                    })
                    previous_close = close
                    continue
                if previous_close >= s3 > close:
                    results.append({
                        "underlying": underlying, "symbol": underlying, "signal": "BUY PUT", "option_action": "BUY PE",
                        "direction": "BEARISH", "pattern": "JFT S3 CROSS", "trigger": "S3", "trigger_level": round(s3, 4),
                        "stop_level": round(s2, 4), "stop_reference": "S2", "pivot": round(levels["pivot"], 4),
                        "r1": round(levels["r1"], 4), "r2": round(r2, 4), "r3": round(r3, 4),
                        "s1": round(levels["s1"], 4), "s2": round(s2, 4), "s3": round(s3, 4),
                        "price": close, "close_5min": close, "time": str(row["ts"]), "created_at": dt.isoformat(),
                        "reason": f"5M close crossed below previous-session S3 ({s3:.2f}); JFT BUY PUT. Stop loss = S2 ({s2:.2f}).",
                        "data_sources": ["GROWW_HISTORICAL_5MIN"], "research_only": True, "trading": "DISABLED",
                    })
                previous_close = close
        return results

    def _universe(self) -> list[str]:
        rows = groww_client.fno_instruments(active_only=True)
        names = sorted({str(r.get("underlying_symbol") or "").strip().upper() for r in rows if r.get("underlying_symbol")})
        return names[: self.MAX_UNDERLYINGS]

    def _build(self) -> None:
        today = datetime.now(IST).date()
        start = today - timedelta(days=self.HISTORY_DAYS - 1)
        levels: dict[str, dict[str, Any]] = {}
        for underlying in self._universe():
            try:
                payload = groww_client.historical_candles(f"NSE-{underlying}", f"{start} 09:15:00", f"{today} 15:40:00", "CASH", "5minute")
                rows = self._parse(payload)
                with self._lock:
                    self._requests += 1
                signals = self._signals_for(underlying, rows) if len(rows) >= self.MIN_BARS else []
                if signals:
                    latest = signals[-1]
                    levels[underlying] = {"latest": latest, "count": len(signals), "signals": signals[-20:]}
                    for signal in signals:
                        self._emit(signal)
            except Exception as exc:
                with self._lock:
                    self._requests += 1
                    self._errors += 1
                    self._last_error = f"{underlying}: {exc}"
                logger.exception("JFT historical analysis failed for %s", underlying)
        with self._lock:
            self._levels = levels

    def _emit(self, signal: dict[str, Any]) -> bool:
        key = (str(signal["underlying"]), str(signal["signal"]), str(signal["time"]), str(signal["trigger"]))
        with self._lock:
            if key in self._alerted:
                return False
            self._alerted.add(key)
        now = datetime.now(IST)
        try:
            signal_store.insert_many([{
                "signal_key": f"JFT:{key[0]}:{key[1]}:{key[2]}:{key[3]}", "created_at": now,
                "symbol": key[0], "underlying": key[0], "instrument_type": "JFT",
                "ltp": signal["price"], "direction": signal["signal"], "bias": signal["direction"],
                "score": 100.0, "confidence": "RULE", "event": "JFT_LEVEL_CROSS",
                "evidence": [signal["reason"]], "payload": signal,
            }])
        except Exception as exc:
            logger.warning("JFT signal persistence failed: %s", exc)
        with self._lock:
            self._signals.insert(0, signal)
            self._signals = self._signals[:100]
            self._last_signal = now.isoformat()
        return True

    def _scan_once(self) -> None:
        self._build()
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
                logger.exception("JFT scanner cycle failed")
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
            self._thread = threading.Thread(target=self._run, name="jft-scanner", daemon=True)
            self._thread.start()
        return self.stats

    def stop(self) -> dict[str, Any]:
        self._running = False
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        return self.stats


jft_scanner = JFTScanner()
