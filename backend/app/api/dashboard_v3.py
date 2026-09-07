from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.intelligence.fno_scanner import fno_scanner
from app.intelligence.historical_session import historical_session_analyzer
from app.research.store import research_store
from app.signals.store import signal_store
from app.strategy.slo_engine import build_results

router = APIRouter(tags=["dashboard"])
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 40)


def _market() -> dict[str, Any]:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        state = "WEEKEND"
    elif MARKET_OPEN <= now.time() <= MARKET_CLOSE:
        state = "LIVE"
    elif now.time() < MARKET_OPEN:
        state = "PRE_OPEN"
    else:
        state = "CLOSED"
    return {
        "state": state,
        "label": {"LIVE": "LIVE", "PRE_OPEN": "PRE-OPEN", "CLOSED": "POST-MARKET", "WEEKEND": "POST-MARKET"}[state],
        "now_ist": now.isoformat(),
        "regular_open": "09:15",
        "regular_close": "15:40",
    }


def _regime(underlyings: list[dict[str, Any]]) -> dict[str, Any]:
    signed = []
    for row in underlyings[:10]:
        score = float(row.get("activity_score") or 0)
        direction = str(row.get("direction") or "FLAT").upper()
        signed.append(score if direction == "UP" else -score if direction == "DOWN" else 0.0)
    if not signed:
        return {"state": "WAITING", "confidence": 0.0, "signed_score": 0.0, "bullish": 0, "bearish": 0, "neutral": 0}
    avg = sum(signed) / len(signed)
    bullish = sum(1 for x in signed if x > 0)
    bearish = sum(1 for x in signed if x < 0)
    neutral = len(signed) - bullish - bearish
    return {
        "state": "BULLISH" if avg >= 15 else "BEARISH" if avg <= -15 else "MIXED / NEUTRAL",
        "confidence": round(sum(abs(x) for x in signed) / len(signed), 1),
        "signed_score": round(avg, 2),
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
    }


def _flow(rows: list[dict[str, Any]], historical: bool) -> dict[str, Any]:
    calls = [r for r in rows if str(r.get("instrument_type") or "").upper() == "CE"]
    puts = [r for r in rows if str(r.get("instrument_type") or "").upper() == "PE"]
    total = lambda xs, key: sum(float(r.get(key) or 0) for r in xs)
    ca, pa = total(calls, "activity_score"), total(puts, "activity_score")
    coi, poi = total(calls, "open_interest"), total(puts, "open_interest")
    cv, pv = total(calls, "volume"), total(puts, "volume")
    ta = ca + pa
    return {
        "call_activity_pct": round(ca / ta * 100, 1) if ta else None,
        "put_activity_pct": round(pa / ta * 100, 1) if ta else None,
        "call_oi": round(coi), "put_oi": round(poi),
        "call_volume": round(cv), "put_volume": round(pv),
        "pcr_oi": round(poi / coi, 3) if coi else None,
        "pcr_volume": round(pv / cv, 3) if cv else None,
        "call_contracts": len(calls), "put_contracts": len(puts),
        "source": "HISTORICAL SESSION" if historical else "LIVE FEED",
    }


def _performance() -> dict[str, Any]:
    try:
        return research_store.label_summary(horizon=5)
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def dashboard_data() -> dict[str, Any]:
    market = _market()
    historical = market["state"] not in {"LIVE"}
    hist = historical_session_analyzer.ensure_for_session() if historical else historical_session_analyzer.stats
    scan = fno_scanner.stats

    if historical and hist.get("completed_at"):
        rows = hist.get("latest_rows", [])
        underlyings = hist.get("underlying_rankings", [])
        opportunities = hist.get("results", [])
        source_mode = "POST_MARKET_HISTORICAL" if market["state"] == "CLOSED" else "PREVIOUS_SESSION_HISTORICAL"
    else:
        rows = scan.get("latest_rankings", [])
        underlyings = scan.get("top_underlyings", [])
        opportunities = build_results(rows, underlyings, min_score=65.0).get("results", [])
        source_mode = "LIVE_FEED" if market["state"] == "LIVE" else "HISTORICAL_LOADING"

    try:
        signals = signal_store.recent(50)
        signal_count = signal_store.count()
    except Exception:
        signals, signal_count = [], None

    return {
        "market": market,
        "source_mode": source_mode,
        "session": hist,
        "regime": _regime(underlyings),
        "flow": _flow(rows, historical),
        "underlyings": underlyings[:25],
        "opportunities": opportunities[:20],
        "strategy": {
            "count": len(opportunities),
            "method": "SLO_OPTIONS_V1_POST_MARKET_HISTORICAL" if historical else "SLO_OPTIONS_V1_LIVE_ADAPTER",
            "research_only": True,
            "trading": "DISABLED",
        },
        "signals": signals,
        "signal_count": signal_count,
        "performance": _performance(),
        "system": {
            "scanner_running": scan.get("running", False),
            "feed_contracts": scan.get("symbols_available_last_check", 0),
            "feed_events": scan.get("feed_events", 0),
            "ranking_ready": scan.get("ranking_ready", False),
            "historical_running": hist.get("running", False),
            "historical_contracts": hist.get("contracts", 0),
            "historical_requests": hist.get("requests", 0),
            "historical_errors": hist.get("errors", 0),
        },
    }


@router.get("/dashboard/data")
def data() -> dict[str, Any]:
    return dashboard_data()


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    return HTML


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI F&O Intelligence</title>
<style>
:root{font-family:Inter,system-ui,sans-serif;background:#080d19;color:#e8edf7;--p:#121a2c;--b:#26334d;--m:#8e9ab0;--g:#42d392;--r:#ff6f7d;--a:#f4c95d;--x:#71a7ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d,#080d19 45%);min-height:100vh}.wrap{max-width:1500px;margin:auto;padding:25px}.top{display:flex;justify-content:space-between;gap:15px;margin-bottom:16px}.title{font-size:30px;font-weight:800}.sub,.small{color:var(--m);font-size:12px}.nav{display:flex;gap:8px;margin-top:12px}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--b);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.badge{border:1px solid var(--b);border-radius:999px;padding:9px 13px;background:#10182a;height:max-content}.notice{padding:13px 15px;border:1px solid #3a3040;border-radius:10px;background:#171c2d;margin-bottom:14px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:14px}.card,.panel{background:rgba(18,26,44,.96);border:1px solid var(--b);border-radius:14px}.card{padding:15px}.label{color:var(--m);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:24px;font-weight:800;margin-top:7px}.panel{overflow:hidden;margin-bottom:14px}.head{padding:15px 18px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}.head b{font-size:15px}.body{padding:17px}.layout{display:grid;grid-template-columns:1fr 1fr;gap:14px}.flow{display:grid;grid-template-columns:1fr 1fr;gap:10px}.box{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:13px}.bar{height:9px;background:#202b40;border-radius:99px;overflow:hidden;margin-top:9px}.bar i{display:block;height:100%;background:var(--x)}table{width:100%;border-collapse:collapse;min-width:950px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--m);background:#0f1728}.table{overflow:auto}.bull{color:var(--g)}.bear{color:var(--r)}.amber{color:var(--a)}.blue{color:var(--x)}.empty{padding:32px;text-align:center;color:var(--m)}.health{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;padding:14px}.h{padding:11px;text-align:center;border:1px solid #22304a;border-radius:9px;background:#0e1627}.ok{color:var(--g)}.off{color:var(--r)}@media(max-width:1100px){.grid{grid-template-columns:repeat(3,1fr)}.layout{grid-template-columns:1fr}}@media(max-width:700px){.wrap{padding:13px}.grid{grid-template-columns:repeat(2,1fr)}.health{grid-template-columns:repeat(2,1fr)}.top{display:block}.badge{margin-top:12px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">AI F&O Intelligence</div><div class="sub">Live flow during market hours • historical session replay after close • research only</div><div class="nav"><a href="/dashboard">Dashboard</a><a href="/strategy">SLO Strategy</a></div></div><div id="badge" class="badge">Loading…</div></div>
<div id="notice" class="notice">Loading today's session…</div>
<div class="grid"><div class="card"><div class="label">Data Mode</div><div id="mode" class="value">—</div><div id="modeSub" class="small">—</div></div><div class="card"><div class="label">Session</div><div id="session" class="value">—</div><div id="sessionSub" class="small">—</div></div><div class="card"><div class="label">Top SLO Score</div><div id="score" class="value">—</div><div id="scoreSub" class="small">—</div></div><div class="card"><div class="label">Opportunities</div><div id="count" class="value">—</div><div class="small">research candidates</div></div><div class="card"><div class="label">Historical Contracts</div><div id="contracts" class="value">—</div><div id="contractsSub" class="small">—</div></div></div>
<div class="layout"><div class="panel"><div class="head"><b>Session Regime</b><span id="regimeMeta" class="small"></span></div><div class="body"><div id="regime" class="value">WAITING</div><div id="regimeScore" class="small">—</div><div class="bar"><i id="regimeBar" style="width:0"></i></div></div></div><div class="panel"><div class="head"><b>CE vs PE Session Activity</b><span id="flowSource" class="small"></span></div><div class="body flow"><div class="box"><div class="label">Calls</div><div id="call" class="value bull">—</div><div id="callMeta" class="small"></div><div class="bar"><i id="callBar"></i></div></div><div class="box"><div class="label">Puts</div><div id="put" class="value bear">—</div><div id="putMeta" class="small"></div><div class="bar"><i id="putBar" style="background:var(--r)"></i></div></div><div class="box"><div class="label">PCR OI</div><div id="pcrOi" class="value">—</div></div><div class="box"><div class="label">PCR Volume</div><div id="pcrVol" class="value">—</div></div></div></div></div>
<div class="panel"><div class="head"><b>🔥 Today's Research Opportunities</b><span id="method" class="small"></span></div><div class="table"><table><thead><tr><th>#</th><th>Underlying</th><th>Option</th><th>Direction</th><th>SLO</th><th>Premium</th><th>Stop</th><th>Target</th><th>DTE</th><th>Delta</th><th>Theta</th><th>IV</th><th>Volume</th><th>OI</th></tr></thead><tbody id="opp"></tbody></table></div><div class="small" style="padding:13px 18px">Historical mode uses Groww's completed-session candles. It does not recreate historical order-book depth or identify market participants.</div></div>
<div class="panel"><div class="head"><b>Today's Active Underlyings</b><span class="small">Underlying session movement</span></div><div class="table"><table><thead><tr><th>#</th><th>Underlying</th><th>Direction</th><th>Score</th><th>Session %</th><th>Spot</th><th>High</th><th>Low</th></tr></thead><tbody id="under"></tbody></table></div></div>
<div class="panel"><div class="head"><b>Research System Health</b><span id="healthMeta" class="small"></span></div><div id="health" class="health"></div></div>
</div><script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const fmt=(v,d=2)=>v==null||v===''?'—':Number.isFinite(Number(v))?Number(v).toFixed(d):esc(v);
const pct=v=>v==null?'—':fmt(v,2)+'%';
const dirCls=v=>['BULLISH','UP','BUY_CALL'].includes(v)?'bull':['BEARISH','DOWN','BUY_PUT'].includes(v)?'bear':'';
async function refresh(){try{const r=await fetch('/dashboard/data');if(!r.ok)throw new Error(await r.text());const d=await r.json();const m=d.market||{},s=d.session||{},f=d.flow||{},reg=d.regime||{},sys=d.system||{},opp=d.opportunities||[],u=d.underlyings||[];document.getElementById('badge').innerHTML=`<span class="${m.state==='LIVE'?'ok':'amber'}">●</span> ${esc(m.label)} • ${new Date(m.now_ist).toLocaleTimeString('en-IN')}`;document.getElementById('notice').textContent=m.state==='LIVE'?'LIVE MARKET: Groww feed is the primary source. Historical analysis remains available for completed sessions.':s.running?`POST-MARKET: loading ${s.target_date||'session'} from Groww historical candles… ${s.contracts||0} contracts completed.`:`POST-MARKET: showing completed session ${s.target_date||'latest available'} from historical market data.`;document.getElementById('mode').textContent=d.source_mode==='LIVE_FEED'?'LIVE':d.source_mode==='HISTORICAL_LOADING'?'LOADING':'HISTORICAL';document.getElementById('modeSub').textContent=d.source_mode.replaceAll('_',' ');document.getElementById('session').textContent=s.target_date||'—';document.getElementById('sessionSub').textContent=`${m.regular_open}–${m.regular_close} IST`;document.getElementById('score').textContent=opp.length?fmt(opp[0].total_score):'—';document.getElementById('scoreSub').textContent=opp.length?`${esc(opp[0].underlying)} ${esc(opp[0].symbol)}`:'No qualifying candidate yet';document.getElementById('count').textContent=opp.length;document.getElementById('contracts').textContent=s.contracts||0;document.getElementById('contractsSub').textContent=s.running?`Requests ${s.requests||0} • errors ${s.errors||0}`:`Completed ${s.completed_at?new Date(s.completed_at).toLocaleTimeString('en-IN'):'—'}`;document.getElementById('regime').textContent=reg.state||'WAITING';document.getElementById('regime').className='value '+(reg.state==='BULLISH'?'bull':reg.state==='BEARISH'?'bear':'');document.getElementById('regimeScore').textContent=`Signed score ${fmt(reg.signed_score)} • confidence ${fmt(reg.confidence,1)} • ${reg.bullish||0} bullish / ${reg.bearish||0} bearish`;document.getElementById('regimeBar').style.width=Math.min(100,Math.abs(Number(reg.signed_score)||0))+'%';document.getElementById('flowSource').textContent=f.source;document.getElementById('call').textContent=f.call_activity_pct==null?'—':fmt(f.call_activity_pct,1)+'%';document.getElementById('put').textContent=f.put_activity_pct==null?'—':fmt(f.put_activity_pct,1)+'%';document.getElementById('callBar').style.width=(f.call_activity_pct||0)+'%';document.getElementById('putBar').style.width=(f.put_activity_pct||0)+'%';document.getElementById('callMeta').textContent=`${f.call_contracts||0} contracts • OI ${fmt(f.call_oi,0)} • Vol ${fmt(f.call_volume,0)}`;document.getElementById('putMeta').textContent=`${f.put_contracts||0} contracts • OI ${fmt(f.put_oi,0)} • Vol ${fmt(f.put_volume,0)}`;document.getElementById('pcrOi').textContent=fmt(f.pcr_oi,3);document.getElementById('pcrVol').textContent=fmt(f.pcr_volume,3);document.getElementById('method').textContent=d.strategy?.method||'';document.getElementById('opp').innerHTML=opp.length?opp.map((x,i)=>`<tr><td>${i+1}</td><td><b>${esc(x.underlying)}</b></td><td>${esc(x.symbol)}</td><td class="${dirCls(x.direction)}">${esc(x.direction)}</td><td><b>${fmt(x.total_score)}</b></td><td>${fmt(x.premium)}</td><td>${fmt(x.stop_premium)}</td><td>${fmt(x.target_premium)}</td><td>${esc(x.dte)}</td><td>${fmt(x.delta,3)}</td><td>${fmt(x.theta,3)}</td><td>${x.iv==null?'—':fmt(x.iv*100,2)+'%'}</td><td>${fmt(x.volume,0)}</td><td>${fmt(x.open_interest,0)}</td></tr>`).join(''):'<tr><td colspan="14" class="empty">No qualifying research candidate yet. Historical analysis may still be loading, or today's data did not meet the SLO threshold.</td></tr>';document.getElementById('under').innerHTML=u.length?u.map((x,i)=>`<tr><td>${i+1}</td><td><b>${esc(x.underlying)}</b></td><td class="${dirCls(x.direction)}">${esc(x.direction)}</td><td>${fmt(x.activity_score)}</td><td>${pct(x.session_change_pct)}</td><td>${fmt(x.spot)}</td><td>${fmt(x.session_high)}</td><td>${fmt(x.session_low)}</td></tr>`).join(''):'<tr><td colspan="8" class="empty">Waiting for historical session analysis…</td></tr>';document.getElementById('health').innerHTML=[['Live scanner',sys.scanner_running],['Live ranking',sys.ranking_ready],['Historical analyzer',sys.historical_running||!!s.completed_at],['Historical data',sys.historical_contracts>0],['Research',true],['Trading',false]].map(x=>`<div class="h"><div class="${x[1]?'ok':'off'}" style="font-size:17px">${x[1]?'●':'○'}</div>${esc(x[0])}</div>`).join('');document.getElementById('healthMeta').textContent=`Historical requests ${sys.historical_requests||0} • errors ${sys.historical_errors||0} • signals ${d.signal_count??'—'}`;}catch(e){document.getElementById('notice').textContent='Dashboard data error: '+e.message}}
refresh();setInterval(refresh,10000);
</script></body></html>'''
