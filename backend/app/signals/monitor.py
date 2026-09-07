from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.core.event_bus import research_event_bus
from app.ml.engine import ml_engine
from app.signals.store import signal_store

logger=logging.getLogger(__name__)


class SignalMonitor:
    """Buffer qualified intelligence events and persist them every five minutes."""
    INTERVAL_SECONDS=300; MIN_SCORE=60.0; INPUT_TOPIC="intelligence.signals"

    def __init__(self)->None:
        self._running=False;self._thread=None;self._checks=0;self._signals_generated=0;self._errors=0;self._last_check=None
        self._pending=[];self._subscription=None;self._lock=threading.Lock();self._db_available=False

    @property
    def running(self):return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self):
        with self._lock:
            return {"running":self.running,"interval_seconds":self.INTERVAL_SECONDS,"minimum_score":self.MIN_SCORE,"checks":self._checks,
                "signals_generated":self._signals_generated,"errors":self._errors,"last_check":self._last_check,"database_available":self._db_available,"persisted_signals":self._safe_count()}

    @staticmethod
    def _candidate(raw):
        score=float(raw.get("intelligence_score") or 0);confidence=str(raw.get("confidence") or "LOW").upper()
        if abs(score)<SignalMonitor.MIN_SCORE or confidence=="LOW":return None
        symbol=str(raw.get("symbol") or raw.get("trading_symbol") or raw.get("token") or "");
        if not symbol:return None
        ts=str(raw.get("timestamp_ms") or raw.get("timestamp") or "");direction="UP" if score>0 else "DOWN"
        return {"signal_key":f"{symbol}:{ts}:{direction}","created_at":datetime.now(timezone.utc),"symbol":symbol,"underlying":raw.get("underlying") or raw.get("underlying_symbol"),
            "instrument_type":raw.get("instrument_type"),"expiry_date":raw.get("expiry_date"),"strike_price":raw.get("strike_price"),"ltp":raw.get("ltp"),"direction":direction,
            "bias":str(raw.get("bias") or ("BULLISH" if score>0 else "BEARISH")),"score":round(score,2),"confidence":confidence,"ml_probability_up":None,"ml_probability_down":None,
            "event":raw.get("event"),"evidence":raw.get("evidence") or [],"payload":raw}

    def _safe_count(self):
        try:self._db_available=True;return signal_store.count()
        except Exception:self._db_available=False;return None

    def _on_signal(self,raw):
        try:
            candidate=self._candidate(raw)
            if candidate:
                if ml_engine.stats.get("model_ready"):
                    prediction=ml_engine.predict(raw);candidate["ml_probability_up"]=prediction.get("probability_up");candidate["ml_probability_down"]=prediction.get("probability_down")
                with self._lock:self._pending.append(candidate)
        except Exception:self._errors+=1;logger.exception("Failed to buffer dashboard signal")

    def run_once(self):
        with self._lock: candidates=self._pending;self._pending=[]
        inserted=signal_store.insert_many(candidates);self._db_available=True
        with self._lock:self._checks+=1;self._signals_generated+=inserted;self._last_check=datetime.now(timezone.utc).isoformat()
        return {"checked":True,"candidates":len(candidates),"inserted":inserted}

    def _run(self):
        while self._running:
            started=time.monotonic()
            try:self.run_once()
            except Exception:
                with self._lock:self._errors+=1;self._db_available=False
                logger.exception("Signal monitor iteration failed")
            wait=max(0,self.INTERVAL_SECONDS-(time.monotonic()-started));end=time.monotonic()+wait
            while self._running and time.monotonic()<end:time.sleep(min(.5,end-time.monotonic()))
        self._running=False

    def start(self):
        if self.running:raise RuntimeError("Signal monitor is already running")
        signal_store.init();self._db_available=True;self._running=True
        self._subscription=research_event_bus.subscribe(self.INPUT_TOPIC,self._on_signal)
        self._thread=threading.Thread(target=self._run,name="signal-monitor",daemon=True);self._thread.start()

    def stop(self):
        self._running=False
        if self._subscription:research_event_bus.unsubscribe(self._subscription);self._subscription=None
        if self._pending:
            try:self.run_once()
            except Exception:logger.exception("Final signal flush failed")


signal_monitor=SignalMonitor()
