from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.price_action.scanner import price_action_scanner
from app.signals.store import signal_store

router = APIRouter(tags=["price-action"])
IST = ZoneInfo("Asia/Kolkata")
EXCLUDED_PATTERNS = {"5M RANGE BREAKOUT", "5M RANGE BREAKDOWN", "RISING BREAKOUT", "RISING BREAKDOWN"}


def _results() -> list[dict[str, Any]]:
    live = price_action_scanner.stats.get("signals", [])
    try:
        persisted = []
        for item in signal_store.recent(200):
            if item.get("instrument_type") == "PRICE_ACTION":
                payload = item.get("payload") or {}
                if isinstance(payload, dict):
                    persisted.append(payload)
        combined = live + persisted
        seen: set[str] = set()
        output: list[dict[str, Any]] = []
        for row in combined:
            if str(row.get("pattern") or "") in EXCLUDED_PATTERNS:
                continue
            key = f"{row.get('underlying')}:{row.get('signal')}:{row.get('pattern')}:{row.get('time') or row.get('created_at')}"
            if key not in seen:
                seen.add(key)
                output.append(row)
        output.sort(key=lambda x: str(x.get("created_at") or x.get("time") or ""), reverse=True)
        return output[:50]
    except Exception:
        return live[:50]


@router.get("/price-action/status")
def price_action_status() -> dict[str, Any]:
    stats = price_action_scanner.stats
    results = _results()
    return {"status": "READY" if results else "WAITING", "count": len(results), "scanner": stats, "method": "DAILY_PATTERN_15MIN_BREAKOUT", "research_only": True, "trading": "DISABLED", "note": "Historical 5-minute candles are scanned for H&S, triangles, falling, flags and rounding structures. A close plus trend/volume confirmation is required before a signal is emitted."}


@router.get("/price-action/results")
def price_action_results(min_score: float = 65.0) -> dict[str, Any]:
    results = [x for x in _results() if float(x.get("score") or 0) >= min_score]
    stats = price_action_scanner.stats
    return {"count": len(results), "results": results, "source_mode": stats.get("mode"), "cache_date": stats.get("cache_date"), "scanner": stats, "method": "DAILY_PATTERN_15MIN_BREAKOUT", "research_only": True, "trading": "DISABLED", "updated_at": datetime.now(IST).isoformat()}


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SLO Price Action</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#090e1b;color:#e8edf7;--p:#121a2c;--b:#26334d;--m:#8e9ab0;--g:#42d392;--r:#ff6f7d;--a:#f4c95d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d,#080d19 42%);min-height:100vh}.wrap{max-width:1550px;margin:auto;padding:26px}.top{display:flex;justify-content:space-between;gap:18px;margin-bottom:18px}.title{font-size:30px;font-weight:800}.sub,.small{color:var(--m);font-size:12px;margin-top:5px}.nav{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--b);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.nav a.active{color:#fff;border-color:#52678f}.status{padding:9px 13px;border:1px solid var(--b);border-radius:999px;background:#10182a;font-size:13px;height:max-content}.notice{padding:13px 15px;border:1px solid #3a3040;border-radius:10px;background:#171c2d;margin-bottom:15px;font-size:13px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:15px}.card,.panel{background:rgba(18,26,44,.96);border:1px solid var(--b);border-radius:14px}.card{padding:15px}.label{color:var(--m);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:24px;font-weight:800;margin-top:7px}.bull{color:var(--g)}.bear{color:var(--r)}.amber{color:var(--a)}.panel{overflow:hidden;margin-bottom:15px}.head{padding:16px 19px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center;gap:10px}.sortbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:11px 18px;border-bottom:1px solid var(--b);background:#0e1627}.sortbar label{color:var(--m);font-size:12px}.sortbar select,.sortbar button{border:1px solid var(--b);background:#10182a;color:#b9c7df;border-radius:7px;padding:7px 10px;cursor:pointer}.sortbar button.active{color:#fff;border-color:#52678f;background:#17233b}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1350px}th,td{text-align:left;padding:11px 13px;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--m);background:#0f1728}.sortable{cursor:pointer;user-select:none}.sortable:hover{color:#fff}.sortmark{font-size:10px;margin-left:4px;color:#71a7ff}.score{font-weight:800}.empty{padding:42px;text-align:center;color:var(--m)}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:18px}.metric{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:13px}.metric span{display:block;color:var(--m);font-size:11px}.metric b{display:block;margin-top:5px;font-size:15px}@media(max-width:1100px){.grid,.metrics{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.wrap{padding:14px}.grid{grid-template-columns:repeat(2,1fr)}.top{display:block}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">SLO Price Action</div><div class="sub">Historical 5-minute pattern detection • candle-close confirmation • research only</div><div class="nav"><a href="/dashboard">Flow Dashboard</a><a href="/strategy">SLO Options</a><a href="/dashboard/history">SLO History</a><a href="/price-action" class="active">Price Action</a><a href="/price-action/history">Price Action History</a></div></div><div id="status" class="status">Connecting…</div></div>
<div id="notice" class="notice">Waiting for the historical price-action scanner…</div>
<div class="grid"><div class="card"><div class="label">Signals</div><div id="count" class="value">—</div><div class="small">score ≥ 65</div></div><div class="card"><div class="label">Best Score</div><div id="best" class="value">—</div></div><div class="card"><div class="label">BUY</div><div id="buy" class="value bull">—</div></div><div class="card"><div class="label">SELL</div><div id="sell" class="value bear">—</div></div><div class="card"><div class="label">Patterns Found</div><div id="patterns" class="value">—</div><div id="patternList" class="small">—</div></div></div>
<div class="panel"><div class="head"><strong>Triggered Historical Price Action Signals</strong><span id="updated" class="small">—</span></div><div class="sortbar"><label for="sortField">Sort</label><select id="sortField" onchange="setSortField(this.value)"><option value="score">Score</option><option value="time">Time</option><option value="symbol">Symbol</option><option value="signal">Signal</option><option value="pattern">Pattern</option><option value="trigger_level">Trigger</option><option value="close_15min">15M Price</option><option value="vol_ratio_15m">15M Volume</option><option value="ema20_15m">EMA20</option><option value="ema50_15m">EMA50</option><option value="ema20_15m">15M Trend</option></select><button id="ascBtn" onclick="setSortDir('asc')">↑ ASC</button><button id="descBtn" class="active" onclick="setSortDir('desc')">↓ DESC</button><span id="sortInfo" class="small">Score ↓</span></div><div class="table"><table><thead><tr><th>#</th><th class="sortable" onclick="sortBy('time')">Time<span class="sortmark" id="mark-time"></span></th><th class="sortable" onclick="sortBy('symbol')">Symbol<span class="sortmark" id="mark-symbol"></span></th><th class="sortable" onclick="sortBy('signal')">Signal<span class="sortmark" id="mark-signal"></span></th><th class="sortable" onclick="sortBy('pattern')">Pattern<span class="sortmark" id="mark-pattern"></span></th><th class="sortable" onclick="sortBy('score')">Score<span class="sortmark" id="mark-score"></span></th><th class="sortable" onclick="sortBy('trigger_level')">Trigger<span class="sortmark" id="mark-trigger_level"></span></th><th class="sortable" onclick="sortBy('close_15min')">15M Price<span class="sortmark" id="mark-close_15min"></span></th><th class="sortable" onclick="sortBy('vol_ratio_15m')">5M Vol<span class="sortmark" id="mark-vol_ratio_15m"></span></th><th class="sortable" onclick="sortBy('ema20_15m')">EMA20<span class="sortmark" id="mark-ema20_15m"></span></th><th class="sortable" onclick="sortBy('ema50_15m')">EMA50<span class="sortmark" id="mark-ema50_15m"></span></th><th class="sortable" onclick="sortBy('ema20_15m')">15M Trend<span class="sortmark" id="mark-ema20_15m"></span></th><th>Reason</th></tr></thead><tbody id="rows"><tr><td colspan="13" class="empty">No confirmed price-action signal yet.</td></tr></tbody></table></div></div>
<div class="panel"><div class="head"><strong>Scanner Health</strong><span class="small">Groww daily-chart setup + 15-minute candles • no websocket dependency</span></div><div class="metrics"><div class="metric"><span>5-Min Requests</span><b id="intraday">—</b></div><div class="metric"><span>Checks</span><b id="checks">—</b></div><div class="metric"><span>Cached Underlyings</span><b id="cache">—</b></div><div class="metric"><span>Errors</span><b id="errors">—</b></div></div></div>
<div class="panel"><div class="head"><strong>Pattern Engine</strong><span class="small">Heuristic structural detection on daily OHLC setup + 15M confirmation</span></div><div class="metrics"><div class="metric"><span>H&amp;S</span><b>Head &amp; Shoulders + Inverse H&amp;S neckline breaks</b></div><div class="metric"><span>Triangles / Falling</span><b>Converging pivot boundaries + breakout/breakdown</b></div><div class="metric"><span>Flags / Rounding</span><b>Bull/Bear flags + rounding top/bottom breaks</b></div></div></div>
<div class="panel"><div class="head"><strong>Confirmation &amp; Safety</strong><span class="small">Research mode</span></div><div class="metrics"><div class="metric"><span>Score</span><b>Pattern quality + 15M Trend/EMA trend + volume + trigger proximity</b></div><div class="metric"><span>Confirmation</span><b>15-minute candle close through the daily pattern trigger</b></div><div class="metric"><span>History</span><b>Daily pattern history + current-day Groww 15-minute candles</b></div><div class="metric"><span>Safety</span><b class="amber">Research only — no orders are placed</b></div></div></div>
</div><script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const fmt=(v,d=2)=>v==null||v===''?'—':Number.isFinite(Number(v))?Number(v).toFixed(d):esc(v);let all=[];let sortField='score';let sortDir='desc';const sortLabels={score:'Score',time:'Time',symbol:'Symbol',signal:'Signal',pattern:'Pattern',trigger_level:'Trigger',close_15min:'15M Price',vol_ratio_15m:'15M Volume',ema20_15m:'EMA20',ema50_15m:'EMA50',ema20_15m:'15M Trend'};
function setSortField(v){sortField=v;render()}function setSortDir(v){sortDir=v;document.getElementById('ascBtn').classList.toggle('active',v==='asc');document.getElementById('descBtn').classList.toggle('active',v==='desc');render()}function sortBy(v){if(sortField===v)sortDir=sortDir==='asc'?'desc':'asc';else{sortField=v;sortDir='desc'}document.getElementById('sortField').value=v;document.getElementById('ascBtn').classList.toggle('active',sortDir==='asc');document.getElementById('descBtn').classList.toggle('active',sortDir==='desc');render()}function compare(a,b){const av=a?.[sortField],bv=b?.[sortField];if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;const an=Number(av),bn=Number(bv);if(Number.isFinite(an)&&Number.isFinite(bn))return an-bn;return String(av).localeCompare(String(bv),undefined,{numeric:true,sensitivity:'base'})}
function render(){const rows=[...all].sort((a,b)=>{const c=compare(a,b);return sortDir==='asc'?c:-c});document.getElementById('sortInfo').textContent=`${sortLabels[sortField]} ${sortDir==='asc'?'↑':'↓'}`;Object.keys(sortLabels).forEach(k=>{const e=document.getElementById('mark-'+k);if(e)e.textContent=k===sortField?(sortDir==='asc'?'↑':'↓'):''});const body=document.getElementById('rows');if(!rows.length){body.innerHTML='<tr><td colspan="13" class="empty">No confirmed price-action signal yet.</td></tr>';return}body.innerHTML=rows.map((x,i)=>`<tr><td>${i+1}</td><td>${esc(x.time||x.created_at)}</td><td><strong>${esc(x.symbol||x.underlying)}</strong></td><td class="${x.signal==='BUY'?'bull':'bear'}">${esc(x.signal)}</td><td>${esc(x.pattern)}</td><td class="score">${fmt(x.score)}</td><td>${fmt(x.trigger_level)}</td><td>${fmt(x.close_15min||x.price)}</td><td>${fmt(x.vol_ratio_15m)}</td><td>${fmt(x.ema20_15m)}</td><td>${fmt(x.ema50_15m)}</td><td>${fmt(x.ema20_15m)}</td><td>${esc(x.reason)}</td></tr>`).join('')}
async function refresh(){try{const r=await fetch('/price-action/results?min_score=65');if(!r.ok)throw Error('API '+r.status);const d=await r.json(),a=d.results||[],s=d.scanner||{},pc=s.pattern_counts||{};all=a;document.getElementById('count').textContent=a.length;document.getElementById('best').textContent=a.length?fmt(Math.max(...a.map(x=>Number(x.score)||0))):'—';document.getElementById('buy').textContent=a.filter(x=>x.signal==='BUY').length;document.getElementById('sell').textContent=a.filter(x=>x.signal==='SELL').length;const names=Object.entries(pc).sort((x,y)=>y[1]-x[1]);document.getElementById('patterns').textContent=names.length;document.getElementById('patternList').textContent=names.slice(0,4).map(x=>x[0]+' '+x[1]).join(' • ')||'waiting';document.getElementById('cache').textContent=s.cached_underlyings??'—';document.getElementById('intraday').textContent=s.intraday_requests??'—';document.getElementById('checks').textContent=s.checks??'—';document.getElementById('errors').textContent=s.errors??'—';document.getElementById('status').innerHTML=a.length?'<span class="bull">● Signal available</span>':'○ Historical scanner waiting';document.getElementById('notice').textContent=a.length?'Historical daily-pattern / 15-minute breakout signals are available. Review the pattern, trigger and confirmation metrics.':'No confirmed price-action signal at the selected threshold. Historical 5-minute candles are scanned without a live websocket.';document.getElementById('updated').textContent=new Date(d.updated_at).toLocaleTimeString('en-IN');render()}catch(e){document.getElementById('status').textContent='API unavailable';document.getElementById('notice').textContent='Unable to load Price Action signals: '+e.message}}
refresh();setInterval(refresh,10000);
</script></body></html>'''


@router.get("/price-action", response_class=HTMLResponse, include_in_schema=False)
def price_action_dashboard() -> str:
    return HTML
