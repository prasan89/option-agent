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
    return {"state": state, "label": {"LIVE": "LIVE", "PRE_OPEN": "PRE-OPEN", "CLOSED": "POST-MARKET", "WEEKEND": "POST-MARKET"}[state], "now_ist": now.isoformat(), "regular_open": "09:15", "regular_close": "15:40"}


def _flow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [r for r in rows if str(r.get("instrument_type") or "").upper() == "CE"]
    puts = [r for r in rows if str(r.get("instrument_type") or "").upper() == "PE"]
    total = lambda xs, key: sum(float(r.get(key) or 0) for r in xs)
    ca, pa = total(calls, "activity_score"), total(puts, "activity_score")
    coi, poi = total(calls, "open_interest"), total(puts, "open_interest")
    cv, pv = total(calls, "volume"), total(puts, "volume")
    return {"call_activity_pct": round(ca/(ca+pa)*100, 1) if ca+pa else None, "put_activity_pct": round(pa/(ca+pa)*100, 1) if ca+pa else None, "call_oi": round(coi), "put_oi": round(poi), "call_volume": round(cv), "put_volume": round(pv), "pcr_oi": round(poi/coi, 3) if coi else None, "pcr_volume": round(pv/cv, 3) if cv else None, "call_contracts": len(calls), "put_contracts": len(puts)}


def _regime(underlyings: list[dict[str, Any]]) -> dict[str, Any]:
    signed = []
    for row in underlyings[:10]:
        score = float(row.get("activity_score") or 0)
        direction = str(row.get("direction") or "FLAT").upper()
        signed.append(score if direction == "UP" else -score if direction == "DOWN" else 0)
    if not signed:
        return {"state": "WAITING", "confidence": 0, "signed_score": 0, "bullish": 0, "bearish": 0, "neutral": 0}
    avg = sum(signed) / len(signed)
    return {"state": "BULLISH" if avg >= 15 else "BEARISH" if avg <= -15 else "MIXED / NEUTRAL", "confidence": round(sum(abs(x) for x in signed)/len(signed), 1), "signed_score": round(avg, 2), "bullish": sum(x > 0 for x in signed), "bearish": sum(x < 0 for x in signed), "neutral": sum(x == 0 for x in signed)}


def dashboard_data() -> dict[str, Any]:
    market = _market()
    historical = market["state"] != "LIVE"
    hist = historical_session_analyzer.ensure_for_session() if historical else historical_session_analyzer.stats
    scan = fno_scanner.stats
    if historical and hist.get("completed_at"):
        rows = hist.get("latest_rows", [])
        underlyings = hist.get("underlying_rankings", [])
        engine = {"results": hist.get("results", []), "count": len(hist.get("results", [])), "method": "SLO_OPTIONS_V2_POST_MARKET_HISTORICAL", "diagnostics": {}}
        source_mode = "POST_MARKET_HISTORICAL" if market["state"] == "CLOSED" else "PREVIOUS_SESSION_HISTORICAL"
    else:
        rows = scan.get("latest_rankings", [])
        underlyings = scan.get("top_underlyings", [])
        engine = build_results(rows, underlyings, min_score=65.0)
        source_mode = "LIVE_FEED" if market["state"] == "LIVE" else "HISTORICAL_LOADING"
    try:
        signals = signal_store.recent(50)
        signal_count = signal_store.count()
    except Exception as exc:
        signals, signal_count = [], None
        engine.setdefault("diagnostics", {})["signal_store_error"] = str(exc)
    try:
        performance = research_store.label_summary(horizon=5)
    except Exception as exc:
        performance = {"available": False, "error": str(exc)}
    return {"market": market, "source_mode": source_mode, "session": hist, "regime": _regime(underlyings), "flow": _flow(rows), "underlyings": underlyings[:25], "opportunities": engine.get("results", [])[:50], "strategy": {"count": engine.get("count", 0), "watch_count": engine.get("watch_count", 0), "history_count": engine.get("history_count", 0), "method": engine.get("method", "SLO_OPTIONS_V2_LIVE_ADAPTER"), "diagnostics": engine.get("diagnostics", {}), "research_only": True, "trading": "DISABLED"}, "signals": signals, "signal_count": signal_count, "performance": performance, "system": {
            "scanner_running": scan.get("running", False),
            "feed_contracts": scan.get("symbols_available_last_check", 0),
            "feed_events": scan.get("feed_events", 0),
            "feed_state_symbols": scan.get("state_symbols", 0),
            "feed_state_with_ltp": scan.get("state_with_ltp", 0),
            "minute_history_ready": scan.get("minute_history_ready", 0),
            "feed_age_seconds": scan.get("last_feed_event_age_seconds"),
            "feed_ready": scan.get("feed_ready", False),
            "feed_starting": scan.get("feed_starting", False),
            "feed_startup_stage": scan.get("feed_startup_stage"),
            "feed_startup_error": scan.get("feed_startup_error"),
            "ranking_ready": scan.get("ranking_ready", False),
            "scanner_error": scan.get("last_error") or scan.get("error"),
            "historical_running": hist.get("running", False),
            "historical_contracts": hist.get("contracts", 0),
            "historical_requests": hist.get("requests", 0),
            "historical_errors": hist.get("errors", 0)
        }}


@router.get("/dashboard/data")
def data() -> dict[str, Any]:
    return dashboard_data()


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    return HTML


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AI F&O Intelligence</title>
<style>body{margin:0;background:#080d19;color:#e8edf7;font-family:Inter,system-ui,sans-serif}.wrap{max-width:1500px;margin:auto;padding:24px}.top{display:flex;justify-content:space-between;gap:16px}.title{font-size:29px;font-weight:800}.muted{color:#8e9ab0;font-size:12px}.nav{display:flex;gap:8px;margin:12px 0;flex-wrap:wrap}.nav a{color:#b9c7df;text-decoration:none;border:1px solid #26334d;padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.badge,.notice,.card,.panel{background:#121a2c;border:1px solid #26334d;border-radius:12px}.badge{padding:9px 13px;height:max-content}.notice{padding:13px;margin:15px 0}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.card{padding:14px}.label{color:#8e9ab0;font-size:11px;text-transform:uppercase}.value{font-size:23px;font-weight:800;margin-top:6px}.panel{margin-top:14px;overflow:hidden}.head{padding:14px 17px;border-bottom:1px solid #26334d;display:flex;justify-content:space-between;gap:10px;align-items:center}.body{padding:15px}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:900px}th,td{padding:9px 11px;border-bottom:1px solid #202a40;text-align:left;font-size:12px;white-space:nowrap}th{color:#8e9ab0;background:#0f1728;position:sticky;top:0}.sortable{cursor:pointer;user-select:none}.sortable:hover{color:#fff}.sortmark{font-size:10px;margin-left:4px;color:#71a7ff}.sortbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 17px;border-bottom:1px solid #26334d;background:#0e1627}.sortbar label{color:#8e9ab0;font-size:12px}.sortbar select,.sortbar button{background:#10182a;color:#b9c7df;border:1px solid #26334d;border-radius:7px;padding:7px 10px;cursor:pointer}.sortbar button.active{color:#fff;border-color:#52678f;background:#17233b}.diag{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}.diag div{background:#0e1627;border:1px solid #22304a;border-radius:9px;padding:10px}.good{color:#42d392}.bad{color:#ff6f7d}.warn{color:#f4c95d}.empty{text-align:center;padding:30px;color:#8e9ab0}@media(max-width:1000px){.grid{grid-template-columns:repeat(3,1fr)}.diag{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.grid{grid-template-columns:repeat(2,1fr)}.top{display:block}}</style></head>
<body><div class="wrap"><div class="top"><div><div class="title">AI F&O Intelligence</div><div class="muted">Live flow during market hours • research only • trading disabled</div><div class="nav"><a href="/dashboard">Dashboard</a><a href="/strategy">SLO Strategy</a><a href="/dashboard/history">SLO History</a><a href="/price-action">Price Action</a><a href="/price-action/history">Price Action History</a><a href="/jft" class="active">JFT Signals</a></div></div><div id="badge" class="badge">Loading…</div></div><div id="notice" class="notice">Loading…</div>
<div class="grid"><div class="card"><div class="label">Data Mode</div><div id="mode" class="value">—</div></div><div class="card"><div class="label">Session</div><div id="session" class="value">—</div></div><div class="card"><div class="label">Top SLO</div><div id="score" class="value">—</div></div><div class="card"><div class="label">Opportunities</div><div id="count" class="value">—</div></div><div class="card"><div class="label">WATCH</div><div id="watch" class="value">—</div></div></div>
<div class="panel"><div class="head"><b>Pipeline Diagnostics</b><span id="diagStatus" class="muted"></span></div><div class="body"><div id="diag" class="diag"></div><div id="error" class="muted" style="margin-top:10px"></div></div></div>
<div class="panel"><div class="head"><b>Session Regime & Flow</b><span id="regime" class="muted"></span></div><div class="body"><b id="regimeScore">—</b> • Calls <span id="calls">—</span> • Puts <span id="puts">—</span> • PCR OI <span id="pcr">—</span></div></div>
<div class="panel"><div class="head"><b>Today's Research Opportunities</b><span id="method" class="muted"></span></div><div class="sortbar"><label for="oppSort">Sort</label><select id="oppSort" onchange="setSort('opp',this.value)"><option value="total_score">SLO</option><option value="underlying">Underlying</option><option value="direction">Direction</option><option value="premium">Premium</option><option value="stop_premium">Stop</option><option value="target_premium">Target</option><option value="dte">DTE</option><option value="volume">Volume</option><option value="open_interest">OI</option></select><button id="oppAsc" onclick="setDir('opp','asc')">↑ ASC</button><button id="oppDesc" class="active" onclick="setDir('opp','desc')">↓ DESC</button><span id="oppSortInfo" class="muted">SLO ↓</span></div><div class="table"><table><thead><tr><th>Status</th><th class="sortable" onclick="sortBy('opp','underlying')">Underlying</th><th>Option</th><th class="sortable" onclick="sortBy('opp','direction')">Direction</th><th class="sortable" onclick="sortBy('opp','total_score')">SLO</th><th class="sortable" onclick="sortBy('opp','premium')">Premium</th><th class="sortable" onclick="sortBy('opp','stop_premium')">Stop</th><th class="sortable" onclick="sortBy('opp','target_premium')">Target</th><th class="sortable" onclick="sortBy('opp','dte')">DTE</th><th class="sortable" onclick="sortBy('opp','volume')">Volume</th><th class="sortable" onclick="sortBy('opp','open_interest')">OI</th><th>Reason</th></tr></thead><tbody id="opp"></tbody></table></div></div>
<div class="panel"><div class="head"><b>Active Underlyings</b><span class="muted">Top 25</span></div><div class="sortbar"><label for="underSort">Sort</label><select id="underSort" onchange="setSort('under',this.value)"><option value="activity_score">Score</option><option value="underlying">Underlying</option><option value="direction">Direction</option><option value="spot">Spot</option><option value="session_pct">Session %</option></select><button id="underAsc" onclick="setDir('under','asc')">↑ ASC</button><button id="underDesc" class="active" onclick="setDir('under','desc')">↓ DESC</button><span id="underSortInfo" class="muted">Score ↓</span></div><div class="table"><table><thead><tr><th>#</th><th class="sortable" onclick="sortBy('under','underlying')">Underlying</th><th class="sortable" onclick="sortBy('under','direction')">Direction</th><th class="sortable" onclick="sortBy('under','activity_score')">Score</th><th class="sortable" onclick="sortBy('under','spot')">Spot</th><th class="sortable" onclick="sortBy('under','session_pct')">Session %</th></tr></thead><tbody id="under"></tbody></table></div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const n=(v,d=2)=>v==null?'—':Number.isFinite(Number(v))?Number(v).toFixed(d):esc(v);let dataRows={opp:[],under:[]};let sorts={opp:{field:'total_score',dir:'desc'},under:{field:'activity_score',dir:'desc'}};const labels={opp:{total_score:'SLO',underlying:'Underlying',direction:'Direction',premium:'Premium',stop_premium:'Stop',target_premium:'Target',dte:'DTE',volume:'Volume',open_interest:'OI'},under:{activity_score:'Score',underlying:'Underlying',direction:'Direction',spot:'Spot',session_pct:'Session %'}};
function compare(a,b,field){const av=a?.[field],bv=b?.[field];if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;const an=Number(av),bn=Number(bv);if(Number.isFinite(an)&&Number.isFinite(bn))return an-bn;return String(av).localeCompare(String(bv),undefined,{numeric:true,sensitivity:'base'})}function setSort(k,v){sorts[k].field=v;renderTables()}function setDir(k,v){sorts[k].dir=v;document.getElementById(k+'Asc').classList.toggle('active',v==='asc');document.getElementById(k+'Desc').classList.toggle('active',v==='desc');renderTables()}function sortBy(k,v){if(sorts[k].field===v)sorts[k].dir=sorts[k].dir==='asc'?'desc':'asc';else{sorts[k].field=v;sorts[k].dir='desc'}document.getElementById(k+'Sort').value=v;document.getElementById(k+'Asc').classList.toggle('active',sorts[k].dir==='asc');document.getElementById(k+'Desc').classList.toggle('active',sorts[k].dir==='desc');renderTables()}
function renderTables(){for(const k of ['opp','under']){const s=sorts[k],rows=[...dataRows[k]].sort((a,b)=>{const c=compare(a,b,s.field);return s.dir==='asc'?c:-c});document.getElementById(k+'SortInfo').textContent=`${labels[k][s.field]} ${s.dir==='asc'?'↑':'↓'}`}const o=[...dataRows.opp].sort((a,b)=>{const c=compare(a,b,sorts.opp.field);return sorts.opp.dir==='asc'?c:-c});document.getElementById('opp').innerHTML=o.length?o.map(z=>`<tr><td class="${z.status==='WATCH'?'warn':z.status==='LIVE'?'good':''}">${esc(z.status)}</td><td><b>${esc(z.underlying)}</b></td><td>${esc(z.option_type)} ${n(z.strike,0)}</td><td>${esc(z.direction)}</td><td>${n(z.total_score)}</td><td>${n(z.premium,4)}</td><td>${n(z.stop_premium,4)}</td><td>${n(z.target_premium,4)}</td><td>${esc(z.dte)}</td><td>${esc(z.volume??'—')}</td><td>${esc(z.open_interest??'—')}</td><td>${esc(z.reason||'—')}</td></tr>`).join(''):'<tr><td colspan="12" class="empty">No current candidate. The diagnostics above show exactly which pipeline stage is empty.</td></tr>';const u=[...dataRows.under].sort((a,b)=>{const c=compare(a,b,sorts.under.field);return sorts.under.dir==='asc'?c:-c});document.getElementById('under').innerHTML=u.length?u.map((z,i)=>`<tr><td>${i+1}</td><td><b>${esc(z.underlying)}</b></td><td>${esc(z.direction||'FLAT')}</td><td>${n(z.activity_score)}</td><td>${n(z.spot,2)}</td><td>${n(z.session_pct,2)}%</td></tr>`).join(''):'<tr><td colspan="6" class="empty">No active underlyings from scanner.</td></tr>'}
async function refresh(){try{const r=await fetch('/dashboard/data');if(!r.ok)throw Error(await r.text());const d=await r.json(),m=d.market||{},s=d.strategy||{},sys=d.system||{},reg=d.regime||{},f=d.flow||{},o=d.opportunities||[],u=d.underlyings||[],x=s.diagnostics||{};dataRows.opp=o;dataRows.under=u;document.getElementById('badge').textContent=m.label+' • '+new Date(m.now_ist).toLocaleTimeString('en-IN');document.getElementById('notice').textContent=m.state==='LIVE'?'LIVE MARKET: reading Groww feed and research pipeline.':'Historical/session mode: '+m.label;document.getElementById('mode').textContent=d.source_mode||'—';document.getElementById('session').textContent=m.state;document.getElementById('score').textContent=o.length?n(Math.max(...o.map(z=>Number(z.total_score)||0))):'—';document.getElementById('count').textContent=s.count??0;document.getElementById('watch').textContent=s.watch_count??0;document.getElementById('regime').textContent=reg.state||'WAITING';document.getElementById('regimeScore').textContent='Confidence '+n(reg.confidence,1)+' / signed '+n(reg.signed_score,2);document.getElementById('calls').textContent=(f.call_contracts??0)+' contracts / '+(f.call_activity_pct??'—')+'%';document.getElementById('puts').textContent=(f.put_contracts??0)+' contracts / '+(f.put_activity_pct??'—')+'%';document.getElementById('pcr').textContent=n(f.pcr_oi,3);document.getElementById('method').textContent=s.method||'—';document.getElementById('diagStatus').textContent=(sys.ranking_ready?'RANKING READY':'RANKING NOT READY')+' • feed '+(sys.feed_startup_stage||((sys.feed_ready||sys.feed_starting)?'STARTING':'STOPPED'))+' • events '+(sys.feed_events??0);const diag=[['Feed Contracts',sys.feed_contracts??0],['Feed Events',sys.feed_events??0],['LTP State',sys.feed_state_with_ltp??0],['1M History',sys.minute_history_ready??0],['Strategy Rows',x.rows??0],['Options',x.option_rows??0],['Score Rows',x.score_rows??0]];document.getElementById('diag').innerHTML=diag.map(([label,value])=>`<div><span class="muted">${label}</span><br><b>${esc(value)}</b></div>`).join('');document.getElementById('error').textContent=(sys.feed_startup_error?'Feed: '+sys.feed_startup_error+' • ':'')+(sys.scanner_error?'Scanner: '+sys.scanner_error:'');renderTables()}catch(e){document.getElementById('notice').textContent='Dashboard API error: '+e.message}}refresh();setInterval(refresh,10000);</script></div></body></html>'''
