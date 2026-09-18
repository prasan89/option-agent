from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.price_action.patterns import PriceActionPatternDetector
from app.services.groww_client import groww_client
from app.signals.store import signal_store

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class PriceActionScanner:
    """Daily-chart structural setup + 15-minute breakout confirmation scanner."""

    MIN_SCORE = 65.0
    DAILY_HISTORY_DAYS = 220
    INTRADAY_HISTORY_DAYS = 30
    MAX_UNDERLYINGS = 50
    MIN_DAILY_BARS = 60
    MIN_15M_BARS = 10
    CYCLE_SECONDS = 300
    MARKET_OPEN = (9, 15)
    MARKET_CLOSE = (15, 40)
    PATTERN_NAMES = (
        "HEAD & SHOULDERS", "INVERSE HEAD & SHOULDERS",
        "TRIANGLE BREAKOUT", "TRIANGLE BREAKDOWN",
        "BULL FLAG BREAKOUT", "BEAR FLAG BREAKDOWN",
        "ROUNDING BOTTOM BREAKOUT", "ROUNDING TOP BREAKDOWN",
        "DOUBLE BOTTOM", "DOUBLE TOP", "TRIPLE BOTTOM", "TRIPLE TOP",
        "CUP & HANDLE", "INVERSE CUP & HANDLE",
    )

    def __init__(self) -> None:
        self._lock=threading.Lock(); self._running=False; self._thread=None
        self._cache={}; self._cache_date=None; self._signals=[]
        self._alerted=set(); self._checks=0; self._daily_requests=0
        self._intraday_requests=0; self._errors=0; self._last_error=None
        self._last_scan=None; self._last_signal=None; self._setups=0
        self._triggered=0; self._pattern_counts=Counter()

    @property
    def running(self): return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self):
        with self._lock:
            return {"running":self.running,"mode":"DAILY_PATTERN_15MIN_BREAKOUT","minimum_score":self.MIN_SCORE,
                    "cached_underlyings":len(self._cache),"cache_date":self._cache_date,"checks":self._checks,
                    "daily_requests":self._daily_requests,"intraday_requests":self._intraday_requests,
                    "setups":self._setups,"triggered":self._triggered,"errors":self._errors,
                    "last_error":self._last_error,"last_scan":self._last_scan,"last_signal":self._last_signal,
                    "pattern_counts":dict(self._pattern_counts),"supported_patterns":list(self.PATTERN_NAMES),
                    "signals":list(self._signals[:50])}

    @staticmethod
    def _market_open():
        now=datetime.now(IST)
        return now.weekday()<5 and PriceActionScanner.MARKET_OPEN <= (now.hour,now.minute) <= PriceActionScanner.MARKET_CLOSE

    @staticmethod
    def _parse(payload):
        candles=payload.get("candles") if isinstance(payload,dict) else None
        if not isinstance(candles,list): return []
        out=[]
        for c in candles:
            if not isinstance(c,(list,tuple)) or len(c)<6: continue
            try: out.append({"ts":str(c[0]),"open":float(c[1]),"high":float(c[2]),"low":float(c[3]),"close":float(c[4]),"volume":float(c[5])})
            except (TypeError,ValueError): pass
        out.sort(key=lambda x:str(x["ts"])); return out

    @staticmethod
    def _dt(ts):
        try:
            v=str(ts).strip()
            if v.isdigit():
                e=float(v); e=e/1000 if e>10_000_000_000 else e
                return datetime.fromtimestamp(e,tz=IST)
            d=datetime.fromisoformat(v.replace("Z","+00:00"))
            return (d.replace(tzinfo=IST) if d.tzinfo is None else d).astimezone(IST)
        except (TypeError,ValueError,OverflowError,OSError): return None

    @staticmethod
    def _ema(values,period):
        if not values:return 0.0
        a=2/(period+1); x=values[0]
        for v in values[1:]: x=v*a+x*(1-a)
        return x

    @classmethod
    def _score_15m(cls, rows, i, direction, trigger, quality):
        closes=[float(x["close"]) for x in rows[:i+1]]
        vols=[float(x["volume"]) for x in rows]
        close=closes[-1]; ema9=cls._ema(closes[-30:],9); ema20=cls._ema(closes[-50:],20)
        prev=vols[max(0,i-12):i]; avg=sum(prev)/len(prev) if prev else 0
        vr=vols[i]/avg if avg else 0
        trend=(close>ema9>ema20) if direction=="BUY" else (close<ema9<ema20)
        score=60+min(10,quality*.10)+(15 if trend else 0)+(min(15,vr*10) if vr else 0)
        return {"score":round(min(100,score),2),"ema9_15m":round(ema9,2),"ema20_15m":round(ema20,2),"volume_ratio_15m":round(vr,2)}

    @classmethod
    def _signal(cls, underlying, daily, intraday, i, candidate):
        dt=cls._dt(str(intraday[i]["ts"]))
        if not dt:return None
        direction=candidate["signal"]; trigger=float(candidate["trigger_level"])
        m=cls._score_15m(intraday,i,direction,trigger,float(candidate.get("quality") or 70))
        if m["score"]<cls.MIN_SCORE:return None
        price=float(intraday[i]["close"])
        window=intraday[max(0,i-3):i+1]
        stop=min(float(x["low"]) for x in window) if direction=="BUY" else max(float(x["high"]) for x in window)
        risk=max(abs(price-stop),price*.002)
        stop=price-risk if direction=="BUY" else price+risk
        target=price+2*risk if direction=="BUY" else price-2*risk
        daily_date=cls._dt(str(daily[-1]["ts"])).date().isoformat() if daily else None
        return {"underlying":underlying,"symbol":underlying,"signal":direction,"pattern":candidate["pattern"],
                "score":m["score"],"status":"CONFIRMED","pattern_quality":float(candidate.get("quality") or 0),
                "daily_trigger_level":round(trigger,4),"trigger_level":round(trigger,4),
                "buy_above":round(trigger,4) if direction=="BUY" else None,
                "sell_below":round(trigger,4) if direction=="SELL" else None,
                "trigger_state":"DAILY_PATTERN_15MIN_CONFIRMED","price":price,
                "close_15min":price,"vol_ratio_15m":m["volume_ratio_15m"],"ema9_15m":m["ema9_15m"],
                "ema20_15m":m["ema20_15m"],"daily_close":float(daily[-1]["close"]),"daily_setup_date":daily_date,
                "stop_loss":round(stop,4),"target":round(target,4),"rr":2.0,"time":str(intraday[i]["ts"]),
                "created_at":dt.isoformat(),"reason":f"{candidate['pattern']}; {candidate.get('detail','daily setup')}; 15-minute close crossed daily trigger; score={m['score']:.2f}.",
                "data_sources":["GROWW_HISTORICAL_DAILY","GROWW_HISTORICAL_15MIN"],"research_only":True,"trading":"DISABLED"}

    def _universe(self):
        rows=groww_client.fno_instruments(active_only=True)
        names=sorted({str(r.get("underlying_symbol") or "").strip().upper() for r in rows if r.get("underlying_symbol")})
        try:
            from app.intelligence.fno_scanner import fno_scanner
            preferred=[str(x.get("underlying") or "").upper() for x in fno_scanner.stats.get("top_underlyings",[])]
        except Exception: preferred=[]
        ordered=[x for x in preferred if x in names]; ordered += [x for x in names if x not in ordered]
        return ordered[:self.MAX_UNDERLYINGS]

    def _daily_setup(self, rows):
        completed=rows[:-1] if rows else rows
        if len(completed)<self.MIN_DAILY_BARS:return None
        candidates=PriceActionPatternDetector.daily_setup_candidates(completed,len(completed)-1)
        return candidates[0] if candidates else None

    def _scan_underlying(self, underlying, today):
        daily=groww_client.historical_candles(f"NSE-{underlying}",f"{today-timedelta(days=self.DAILY_HISTORY_DAYS)} 09:15:00",f"{today} 15:40:00","CASH","1day")
        self._daily_requests+=1
        drows=self._parse(daily)
        if len(drows)<self.MIN_DAILY_BARS:return None
        setup=self._daily_setup(drows)
        if not setup:return None
        start=today-timedelta(days=self.INTRADAY_HISTORY_DAYS)
        intra=groww_client.historical_candles(f"NSE-{underlying}",f"{start} 09:15:00",f"{today} 15:40:00","CASH","15minute")
        self._intraday_requests+=1
        irows=[x for x in self._parse(intra) if self._dt(str(x["ts"])) and self._dt(str(x["ts"])).date()==today]
        if len(irows)<self.MIN_15M_BARS:return {"setup":setup}
        triggered=None
        for i in range(1,len(irows)):
            prev=float(irows[i-1]["close"]); close=float(irows[i]["close"]); level=float(setup["trigger_level"])
            crossed=(prev<=level<close) if setup["signal"]=="BUY" else (prev>=level>close)
            if crossed:
                triggered=cls._signal(self,underlying,drows,irows,i,setup) if False else self._signal(underlying,drows,irows,i,setup)
                if triggered: break
        return {"setup":setup,"confirmed":triggered}

    def _build_cache(self):
        today=datetime.now(IST).date()
        cache={}; counts=Counter(); errors=0
        for underlying in self._universe():
            try:
                result=self._scan_underlying(underlying,today)
                if result: cache[underlying]=result; counts[str(result.get("setup",{}).get("pattern"))]+=1
            except Exception as exc:
                errors+=1; self._errors+=1; self._last_error=f"{underlying}: {exc}"; logger.exception("Daily/15m scan failed for %s",underlying)
        with self._lock:
            self._cache=cache; self._cache_date=today.isoformat() if errors==0 else None
            self._setups=sum(1 for x in cache.values() if x.get("setup"))
            self._triggered=sum(1 for x in cache.values() if x.get("confirmed"))
            self._pattern_counts=counts
        logger.info("Daily pattern + 15m breakout cache: underlyings=%s daily_requests=%s 15m_requests=%s errors=%s",len(cache),self._daily_requests,self._intraday_requests,errors)

    def _emit(self, signal):
        now=datetime.now(IST)
        key=(str(signal["underlying"]),str(signal["signal"]),str(signal.get("time") or ""),str(signal.get("pattern") or ""))
        with self._lock:
            if key in self._alerted:return False
            self._alerted.add(key)
        item={**signal,"symbol":signal["underlying"],"price":signal["price"],"research_only":True,"trading":"DISABLED"}
        try:
            signal_store.insert_many([{"signal_key":f"PRICE_ACTION:{key[0]}:{key[1]}:{key[2]}:{key[3]}","created_at":now,
                "symbol":item["symbol"],"underlying":item["underlying"],"instrument_type":"PRICE_ACTION","ltp":item["price"],
                "direction":item["signal"],"bias":"BULLISH" if item["signal"]=="BUY" else "BEARISH","score":item["score"],
                "confidence":"HIGH" if item["score"]>=80 else "MEDIUM","event":"PRICE_ACTION","evidence":[item["pattern"],item["reason"]],"payload":item}])
        except Exception as exc: logger.warning("Price-action persistence failed: %s",exc)
        with self._lock:
            self._signals.insert(0,item); self._signals=self._signals[:50]; self._last_signal=now.isoformat()
        return True

    def _scan_once(self):
        today=datetime.now(IST).date()
        if self._cache_date!=today.isoformat() or self._market_open(): self._build_cache()
        for cached in list(self._cache.values()):
            if cached.get("confirmed"): self._emit(cached["confirmed"])
        with self._lock:self._checks+=1;self._last_scan=datetime.now(IST).isoformat()

    def _run(self):
        while self._running:
            try:self._scan_once(); deadline=time.monotonic()+self.CYCLE_SECONDS
            except Exception as exc:
                self._errors+=1;self._last_error=str(exc);logger.exception("Price-action cycle failed");deadline=time.monotonic()+30
            while self._running and time.monotonic()<deadline:time.sleep(min(1,max(.1,deadline-time.monotonic())))
        self._running=False

    def start(self):
        with self._lock:
            if self.running:return self.stats
            if not groww_client.configured:raise RuntimeError("Groww credentials are not configured")
            self._running=True;self._thread=threading.Thread(target=self._run,name="price-action-scanner",daemon=True);self._thread.start()
        return self.stats

    def stop(self):
        self._running=False;thread=self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():thread.join(timeout=2)
        return self.stats


price_action_scanner=PriceActionScanner()
