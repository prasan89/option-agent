from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any

from app.core.event_bus import research_event_bus
from app.ml.labeler import OutcomeLabeler
from app.ml.model import baseline_predictor
from app.research.store import research_store

logger=logging.getLogger(__name__)


class MLEngine:
    """Create forward labels from live observations and train a research baseline."""
    INPUT_TOPIC="dataset.observations"; LABEL_TOPIC="dataset.labels"; LABEL_HORIZON_MINUTES=5; MAX_HISTORY=50_000

    def __init__(self)->None:
        self._running=False; self._latest={}; self._seen=set(); self._history=deque(maxlen=self.MAX_HISTORY); self._labeler=OutcomeLabeler()
        self._observations=0; self._labels=0; self._errors=0; self._lock=threading.Lock(); self._subscription=None

    @property
    def running(self)->bool:return self._running

    @property
    def stats(self)->dict[str,Any]:
        try:counts=research_store.counts();db=True
        except Exception:counts={};db=False
        with self._lock:return {"running":self.running,"observations":self._observations,"labels":self._labels,"errors":self._errors,
            "label_horizon_minutes":self.LABEL_HORIZON_MINUTES,"database_available":db,"model_ready":baseline_predictor.ready,"training":baseline_predictor.training_stats,"persisted":counts}

    @staticmethod
    def _timestamp(row):
        if row.get("timestamp_ms"):return int(row["timestamp_ms"])
        value=str(row.get("timestamp") or "")
        return int(datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()*1000) if value else 0

    @staticmethod
    def _features(row):
        return {"score":row.get("flow_score",row.get("score",0.0)),"price_change_pct":row.get("price_change_pct",row.get("change_pct_1m",0.0)),
            "depth_imbalance":row.get("depth_imbalance",0.0),"oi_change_pct":row.get("oi_change_pct",0.0),"volume_change_pct":row.get("volume_change_pct",0.0),"intelligence_score":row.get("intelligence_score",0.0)}

    def _on_observation(self,row:dict[str,Any])->None:
        try:
            symbol=str(row.get("symbol") or ""); price=float(row.get("ltp") or 0); ts=self._timestamp(row)
            if not symbol or price<=0 or ts<=0:return
            key=(symbol,ts,row.get("source"))
            if key in self._seen:return
            self._seen.add(key);self._history.append(row);self._latest[symbol]=row
            labels=self._labeler.observe(symbol,ts,price,self._features(row))
            for label in labels:
                if label.get("horizon_minutes")!=self.LABEL_HORIZON_MINUTES:continue
                if research_store.insert_label(label):
                    self._labels+=1;research_event_bus.publish(self.LABEL_TOPIC,label)
            self._observations+=1
        except Exception:
            self._errors+=1;logger.exception("Failed to process ML observation")

    def start(self)->None:
        if self.running:raise RuntimeError("ML engine is already running")
        self._running=True;self._subscription=research_event_bus.subscribe(self.INPUT_TOPIC,self._on_observation)

    def stop(self)->None:
        self._running=False
        if self._subscription:research_event_bus.unsubscribe(self._subscription);self._subscription=None

    def latest(self,limit=25):return list(self._latest.values())[-limit:]

    def train(self)->dict[str,Any]:
        records=research_store.labels(self.LABEL_HORIZON_MINUTES);rows=[r["features"] for r in records];labels=[int(r["label"]) for r in records]
        result=baseline_predictor.fit(rows,labels);return {**result,"label_rows":len(rows),"horizon_minutes":self.LABEL_HORIZON_MINUTES}

    def predict(self,row):return baseline_predictor.predict(self._features(row)).__dict__


ml_engine=MLEngine()
