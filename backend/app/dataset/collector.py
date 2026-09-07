from __future__ import annotations

import logging
import threading
from typing import Any

from app.core.event_bus import research_event_bus
from app.research.store import research_store

logger = logging.getLogger(__name__)


class HistoricalDatasetCollector:
    """Persist model-ready observations in PostgreSQL and publish them onward."""
    INPUT_TOPICS = ("fno.rankings", "intelligence.signals")
    OUTPUT_TOPIC = "dataset.observations"

    def __init__(self) -> None:
        self._running=False; self._observations=0; self._errors=0; self._subscriptions=[]; self._lock=threading.Lock()

    @property
    def running(self)->bool:return self._running

    @property
    def stats(self)->dict[str,Any]:
        try: counts=research_store.counts(); db=True
        except Exception: counts={}; db=False
        with self._lock:return {"running":self.running,"observations":self._observations,"errors":self._errors,"database_available":db,"persisted":counts}

    def _save(self,row:dict[str,Any])->None:
        try:
            if research_store.insert_observation(row):
                with self._lock:self._observations+=1
            research_event_bus.publish(self.OUTPUT_TOPIC,row)
        except Exception:
            with self._lock:self._errors+=1
            logger.exception("Dataset persistence failed")

    def _on_rankings(self,payload:dict[str,Any])->None:
        for row in payload.get("rankings",[]):
            if not isinstance(row,dict) or not row.get("symbol"):continue
            self._save({"timestamp_ms":int(__import__('datetime').datetime.fromisoformat(str(payload.get('timestamp')).replace('Z','+00:00')).timestamp()*1000),"source":"fno_scanner",
                "symbol":row.get("symbol"),"underlying":row.get("underlying"),"instrument_type":row.get("instrument_type"),"expiry_date":row.get("expiry_date"),
                "strike_price":row.get("strike_price"),"ltp":row.get("ltp"),"change_pct_1m":row.get("change_pct_since_last_scan"),"activity_score":row.get("activity_score"),"direction":row.get("direction")})

    def _on_intelligence(self,signal:dict[str,Any])->None:
        if not signal.get("token"):return
        self._save({"timestamp_ms":signal.get("timestamp_ms"),"source":"intelligence_score","symbol":signal.get("token"),"underlying":signal.get("underlying"),
            "instrument_type":signal.get("instrument_type"),"expiry_date":signal.get("expiry_date"),"strike_price":signal.get("strike_price"),"ltp":signal.get("ltp"),
            "flow_event":signal.get("event"),"flow_score":signal.get("score"),"intelligence_score":signal.get("intelligence_score"),"confidence":signal.get("confidence"),
            "bias":signal.get("bias"),"price_change_pct":signal.get("price_change_pct"),"depth_imbalance":signal.get("depth_imbalance"),"oi_change_pct":signal.get("oi_change_pct"),
            "volume_change_pct":signal.get("volume_change_pct"),"evidence":signal.get("evidence",[])})

    def start(self)->None:
        if self.running:raise RuntimeError("Historical dataset collector is already running")
        research_store.init(); self._running=True
        self._subscriptions=[research_event_bus.subscribe("fno.rankings",self._on_rankings),research_event_bus.subscribe("intelligence.signals",self._on_intelligence)]

    def stop(self)->None:
        self._running=False
        for token in self._subscriptions:research_event_bus.unsubscribe(token)
        self._subscriptions=[]

    def recent(self,limit:int=100)->list[dict[str,Any]]:return research_store.recent_observations(limit)


historical_dataset_collector=HistoricalDatasetCollector()
