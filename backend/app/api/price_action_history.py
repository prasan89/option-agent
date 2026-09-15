from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.price_action.scanner import price_action_scanner
from app.signals.store import signal_store

router = APIRouter(tags=["price-action-history"])


def _history() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for item in signal_store.recent(500):
            if item.get("instrument_type") != "PRICE_ACTION":
                continue
            payload = item.get("payload") or {}
            if isinstance(payload, dict):
                row = dict(payload)
                row.setdefault("created_at", item.get("created_at"))
                rows.append(row)
    except Exception:
        pass

    # Include the in-memory scanner state immediately after deployment, even
    # before a database refresh is visible to the browser.
    rows.extend(price_action_scanner.stats.get("signals", []))
    seen: set[tuple[str, str, str, str, str]] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        key = (
            str(row.get("underlying")), str(row.get("signal")),
            str(row.get("pattern")), str(row.get("trigger_level")),
            str(row.get("status")),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    output.sort(key=lambda x: str(x.get("created_at") or x.get("time") or ""), reverse=True)
    return output[:500]


@router.get("/price-action/history-data")
def price_action_history_data() -> dict[str, Any]:
    rows = _history()
    return {
        "count": len(rows),
        "setups": sum(1 for x in rows if x.get("status") == "SETUP"),
        "confirmed": sum(1 for x in rows if x.get("status") == "CONFIRMED"),
        "results": rows,
        "scanner": price_action_scanner.stats,
        "research_only": True,
        "trading": "DISABLED",
    }


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Price Action History</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#090e1b;color:#e8edf7;--p:#121a2c;--b:#26334d;--m:#8e9ab0;--g:#42d392;--r:#ff6f7d;--a:#f4c95d;--blue:#71a7ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d,#080d19 42%);min-height:100vh}.wrap{max-width:1550px;margin:auto;padding:26px}.top{display:flex;justify-content:space-between;gap:18px;margin-bottom:18px}.title{font-size:30px;font-weight:800}.sub,.small{color:var(--m);font-size:12px;margin-top:5px}.nav{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--b);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.nav a.active{color:#fff;border-color:#52678f}.status{padding:9px 13px;border:1px solid var(--b);border-radius:999px;background:#10182a;font-size:13px;height:max-content}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:15px}.card,.panel{background:rgba(18,26,44,.96);border:1px solid var(--b);border-radius:14px}.card{padding:15px}.label{color:var(--m);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:24px;font-weight:800;margin-top:7px}.bull{color:var(--g)}.bear{color:var(--r)}.amber{color:var(--a)}.blue{color:var(--blue)}.panel{overflow:hidden;margin-bottom:15px}.head{padding:16px 19px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center;gap:10px}.tabs{display:flex;gap:8px;padding:13px 18px;border-bottom:1px solid var(--b)}button{border:1px solid var(--b);background:#10182a;color:#b9c7df;border-radius:7px;padding:7px 11px;cursor:pointer}button.active{color:#fff;border-color:#52678f;background:#17233b}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1250px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--m);background:#0f1728;position:sticky;top:0}.score{font-weight:800}.empty{padding:42px;text-align:center;color:var(--m)}.tag{padding:4px 7px;border-radius:6px;background:#202c44}.setup{color:var(--a)}.confirmed{color:var(--g)}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:18px}.metric{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:13px}.metric span{display:block;color:var(--m);font-size:11px}.metric b{display:block;margin-top:5px;font-size:15px}@media(max-width:900px){.grid,.metrics{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">Price Action History</div><div class="sub">Persistent daily research record — setups remain visible after the live state changes</div><div class="nav"><a href="/dashboard">Flow Dashboard</a><a href="/strategy">SLO Options</a><a href="/price-action">Price Action</a><a href="/price-action/history" class="active">Price Action History</a></div></div><div id="status" class="status">Loading…</div></div>
<div class="grid"><div class="card"><div class="label">All Records</div><div id="count" class="value">—</div></div><div class="card"><div class="label">Setups</div><div id="setups" class="value amber">—</div><div class="small">daily setup + 5m watch</div></div><div class="card"><div class="label">Confirmed</div><div id="confirmed" class="value bull">—</div><div class="small">5m trigger + volume</div></div><div class="card"><div class="label">Scanner</div><div id="scanner" class="value">—</div><div id="scanmeta" class="small">—</div></div></div>
<div class="panel"><div class="head"><strong>Today's Price Action Lifecycle</strong><span id="updated" class="small">—</span></div><div class="tabs"><button id="allBtn" class="active" onclick="setFilter('ALL')">All</button><button id="setupBtn" onclick="setFilter('SETUP')">Setups</button><button id="confirmedBtn" onclick="setFilter('CONFIRMED')">Confirmed</button><button id="buyBtn" onclick="setFilter('BUY')">BUY</button><button id="sellBtn" onclick="setFilter('SELL')">SELL</button></div><div class="table"><table><thead><tr><th>#</th><th>Time</th><th>Underlying</th><th>Signal</th><th>Status</th><th>Pattern</th><th>Score</th><th>Trigger</th><th>5M Price</th><th>5M Vol</th><th>EMA20</th><th>EMA50</th><th>Daily Close</th><th>Reason</th></tr></thead><tbody id="rows"><tr><td colspan="14" class="empty">Loading history…</td></tr></tbody></table></div></div>
<div class="panel"><div class="head"><strong>What this fixes</strong></div><div class="metrics"><div class="metric"><span>Before</span><b>A setup existed only in memory and was emitted only for breakout/breakdown watches.</b></div><div class="metric"><span>Now</span><b>Every qualifying daily setup is persisted after its 5-minute evaluation.</b></div><div class="metric"><span>Lifecycle</span><b>SETUP → CONFIRMED is visible as separate research records.</b></div><div class="metric"><span>Safety</span><b class="amber">Research only — trading remains disabled.</b></div></div></div>
</div><script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const fmt=(v,d=2)=>v==null||v===''?'—':Number.isFinite(Number(v))?Number(v).toFixed(d):esc(v);let all=[];let filter='ALL';
function setFilter(v){filter=v;document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('active'));const map={ALL:'allBtn',SETUP:'setupBtn',CONFIRMED:'confirmedBtn',BUY:'buyBtn',SELL:'sellBtn'};document.getElementById(map[v]).classList.add('active');render()}
function render(){let rows=all.filter(x=>filter==='ALL'||x.status===filter||x.signal===filter);const body=document.getElementById('rows');if(!rows.length){body.innerHTML='<tr><td colspan="14" class="empty">No records for this filter yet.</td></tr>';return}body.innerHTML=rows.map((x,i)=>`<tr><td>${i+1}</td><td>${esc(x.time||x.created_at)}</td><td><strong>${esc(x.underlying)}</strong></td><td class="${x.signal==='BUY'?'bull':'bear'}">${esc(x.signal)}</td><td class="${x.status==='CONFIRMED'?'confirmed':'setup'}">${esc(x.status)}</td><td>${esc(x.pattern)}</td><td class="score">${fmt(x.score)}</td><td>${fmt(x.trigger_level)}</td><td>${fmt(x.close_5min||x.price)}</td><td>${fmt(x.vol_ratio_5min)}x</td><td>${fmt(x.ema20)}</td><td>${fmt(x.ema50)}</td><td>${fmt(x.daily_close)}</td><td>${esc(x.reason)}</td></tr>`).join('')}
async function refresh(){try{const r=await fetch('/price-action/history-data');if(!r.ok)throw Error('API '+r.status);const d=await r.json();all=d.results||[];document.getElementById('count').textContent=d.count;document.getElementById('setups').textContent=d.setups;document.getElementById('confirmed').textContent=d.confirmed;const s=d.scanner||{};document.getElementById('scanner').textContent=s.running?'RUNNING':'WAITING';document.getElementById('scanmeta').textContent=`${s.cached_underlyings||0} cached • ${s.checks||0} checks • ${s.errors||0} errors`;document.getElementById('status').innerHTML='<span class="bull">● History connected</span>';document.getElementById('updated').textContent=new Date().toLocaleTimeString('en-IN');render()}catch(e){document.getElementById('status').textContent='API unavailable'}}refresh();setInterval(refresh,10000);
</script></body></html>'''


@router.get("/price-action/history", response_class=HTMLResponse, include_in_schema=False)
def price_action_history() -> str:
    return HTML
