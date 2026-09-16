from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.signals.store import signal_store

router = APIRouter(tags=["reversal"])
IST = ZoneInfo("Asia/Kolkata")


def _rows(limit: int = 5000) -> list[dict[str, Any]]:
    return signal_store.reversal_history(limit=limit)


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload") or {}
    if isinstance(p, dict):
        return p
    return {}


@router.get("/reversal/data")
def reversal_data() -> dict[str, Any]:
    rows = _rows()
    events = []
    for row in rows:
        p = _payload(row)
        events.append({
            "id": row.get("id"),
            "underlying": row.get("underlying") or row.get("symbol"),
            "signal": row.get("direction"),
            "option_action": p.get("option_action"),
            "pattern": p.get("pattern"),
            "time": p.get("time") or row.get("created_at"),
            "created_at": row.get("created_at"),
            "price": p.get("price") or row.get("ltp"),
            "trigger_level": p.get("trigger_level"),
            "stop_level": p.get("stop_level"),
            "stop_reference": p.get("stop_reference"),
            "reason": p.get("reason"),
            "research_only": True,
            "trading": "DISABLED",
        })
    return {
        "count": len(events),
        "results": events,
        "updated_at": datetime.now(IST).isoformat(),
        "method": "HISTORICAL_5MIN_REVERSAL_HISTORY",
        "rule": "3 BIG RED -> GREEN close above candle 3 high = BUY CALL; inverse = BUY PUT",
        "research_only": True,
        "trading": "DISABLED",
    }


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reversal Signals</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#090e1b;color:#e8edf7;--b:#26334d;--m:#8e9ab0;--g:#42d392;--r:#ff6f7d;--a:#f4c95d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d,#080d19 42%);min-height:100vh}.wrap{max-width:1600px;margin:auto;padding:25px}.top{display:flex;justify-content:space-between;gap:18px}.title{font-size:30px;font-weight:800}.sub{color:var(--m);font-size:12px;margin-top:5px}.nav{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--b);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.nav a.active{color:#fff;border-color:#52678f}.status{padding:9px 13px;border:1px solid var(--b);border-radius:999px;background:#10182a;height:max-content}.notice,.panel,.card{background:rgba(18,26,44,.96);border:1px solid var(--b);border-radius:14px}.notice{padding:13px;margin:16px 0;font-size:13px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:15px}.card{padding:14px}.label{color:var(--m);font-size:11px;text-transform:uppercase}.value{font-size:23px;font-weight:800;margin-top:6px}.panel{overflow:hidden;margin-bottom:15px}.head{padding:15px 18px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}.sortbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:11px 18px;border-bottom:1px solid var(--b);background:#0e1627}.sortbar label,.small{color:var(--m);font-size:12px}.sortbar select,.sortbar button{border:1px solid var(--b);background:#10182a;color:#b9c7df;border-radius:7px;padding:7px 10px;cursor:pointer}.sortbar button.active{color:#fff;border-color:#52678f;background:#17233b}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1250px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--m);background:#0f1728}.sortable{cursor:pointer}.bull{color:var(--g)}.bear{color:var(--r)}.empty{padding:42px;text-align:center;color:var(--m)}.note{padding:13px 18px;color:#b7c2d5;font-size:12px;border-top:1px solid var(--b)}
@media(max-width:1000px){.grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.wrap{padding:13px}.grid{grid-template-columns:repeat(2,1fr)}.top{display:block}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">Reversal Signals</div><div class="sub">Dedicated historical 5-minute reversal scanner • research only • trading disabled</div><div class="nav"><a href="/dashboard">Dashboard</a><a href="/strategy">SLO Options</a><a href="/dashboard/history">SLO History</a><a href="/price-action">Price Action</a><a href="/price-action/history">Price Action History</a><a href="/jft">JFT Signals</a><a href="/reversal" class="active">Reversal</a><a href="/reversal/history">Reversal History</a></div></div><div id="status" class="status">Loading…</div></div>
<div class="notice">Pattern: <b>3 BIG RED → GREEN</b> or <b>3 BIG GREEN → RED</b>. The confirmation candle must close beyond candle 3's high/low. This page shows the most recent detected reversal events; the separate history page contains the full persisted history.</div>
<div class="grid"><div class="card"><div class="label">Reversals</div><div id="count" class="value">—</div></div><div class="card"><div class="label">BUY CALL</div><div id="calls" class="value bull">—</div></div><div class="card"><div class="label">BUY PUT</div><div id="puts" class="value bear">—</div></div><div class="card"><div class="label">Stocks</div><div id="stocks" class="value">—</div></div><div class="card"><div class="label">Latest Time</div><div id="latest" class="value">—</div></div></div>
<div class="panel"><div class="head"><b>Latest Reversal Signals</b><span id="updated" class="small">—</span></div><div class="table"><table><thead><tr><th>Time</th><th>Underlying</th><th>Signal</th><th>Pattern</th><th>5M Price</th><th>Trigger</th><th>SL</th><th>Stop Reference</th><th>Reason</th></tr></thead><tbody id="rows"></tbody></table></div><div class="note"><a href="/reversal/history" style="color:#b9c7df">Open full Reversal History →</a></div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const n=v=>v==null?'—':Number.isFinite(Number(v))?Number(v).toFixed(2):esc(v);async function refresh(){try{const r=await fetch('/reversal/data');if(!r.ok)throw Error('API '+r.status);const d=await r.json(),a=d.results||[];document.getElementById('count').textContent=a.length;document.getElementById('calls').textContent=a.filter(x=>x.signal==='BUY CALL').length;document.getElementById('puts').textContent=a.filter(x=>x.signal==='BUY PUT').length;document.getElementById('stocks').textContent=new Set(a.map(x=>x.underlying)).size;document.getElementById('latest').textContent=a[0]?.time?new Date(a[0].time).toLocaleTimeString('en-IN'):'—';document.getElementById('updated').textContent=d.updated_at?new Date(d.updated_at).toLocaleTimeString('en-IN'):'—';document.getElementById('status').textContent='● Historical data';document.getElementById('rows').innerHTML=a.slice(0,100).map(x=>`<tr><td>${esc(x.time)}</td><td><b>${esc(x.underlying)}</b></td><td class="${x.signal==='BUY CALL'?'bull':'bear'}"><b>${esc(x.signal)}</b></td><td>${esc(x.pattern)}</td><td>${n(x.price)}</td><td>${n(x.trigger_level)}</td><td>${n(x.stop_level)}</td><td>${esc(x.stop_reference)}</td><td>${esc(x.reason)}</td></tr>`).join('')||'<tr><td colspan="9" class="empty">No reversal detected yet.</td></tr>'}catch(e){document.getElementById('status').textContent='API unavailable';document.getElementById('rows').innerHTML='<tr><td colspan="9" class="empty">'+esc(e.message)+'</td></tr>'}}refresh();setInterval(refresh,10000);
</script></div></body></html>'''


@router.get("/reversal", response_class=HTMLResponse, include_in_schema=False)
def reversal_dashboard() -> str:
    return HTML


@router.get("/reversal/history", response_class=HTMLResponse, include_in_schema=False)
def reversal_history_dashboard() -> str:
    return HISTORY_HTML


HISTORY_HTML = HTML.replace(
    '<title>Reversal Signals</title>', '<title>Reversal History</title>', 1
).replace(
    '<div class="title">Reversal Signals</div>', '<div class="title">Reversal History</div>', 1
).replace(
    'Dedicated historical 5-minute reversal scanner • research only • trading disabled',
    'All persisted historical reversal events • research only • trading disabled', 1
).replace(
    '<a href="/reversal" class="active">Reversal</a><a href="/reversal/history">Reversal History</a>',
    '<a href="/reversal">Reversal</a><a href="/reversal/history" class="active">Reversal History</a>', 1
).replace(
    'The confirmation candle must close beyond candle 3\'s high/low. This page shows the most recent detected reversal events; the separate history page contains the full persisted history.',
    'The confirmation candle must close beyond candle 3\'s high/low. This table is the persisted historical record, including the exact 5-minute confirmation time.', 1
).replace(
    '<div class="table"><table><thead><tr><th>Time</th><th>Underlying</th><th>Signal</th><th>Pattern</th><th>5M Price</th><th>Trigger</th><th>SL</th><th>Stop Reference</th><th>Reason</th></tr></thead>',
    '<div class="sortbar"><label>Sort</label><select id="sortField" onchange="sortField=this.value;render()"><option value="time">Time</option><option value="underlying">Underlying</option><option value="signal">Signal</option><option value="price">5M Price</option><option value="trigger_level">Trigger</option><option value="stop_level">SL</option></select><button id="asc" onclick="sortDir=\'asc\';render()">↑ ASC</button><button id="desc" class="active" onclick="sortDir=\'desc\';render()">↓ DESC</button><span id="sortInfo" class="small">Time ↓</span></div><div class="table"><table><thead><tr><th>Time</th><th>Underlying</th><th>Signal</th><th>Pattern</th><th>5M Price</th><th>Trigger</th><th>SL</th><th>Stop Reference</th><th>Reason</th></tr></thead>', 1
).replace(
    '<div class="note"><a href="/reversal/history" style="color:#b9c7df">Open full Reversal History →</a></div>',
    '<div class="note">History is read from PostgreSQL signal records, so events remain available after scanner restarts.</div>', 1
).replace(
    "const esc=v=>String(v??'').replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[m]));const n=v=>v==null?'—':Number.isFinite(Number(v))?Number(v).toFixed(2):esc(v);async function refresh()",
    "const esc=v=>String(v??'').replace(/[&<>\"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[m]));const n=v=>v==null?'—':Number.isFinite(Number(v))?Number(v).toFixed(2):esc(v);let all=[];let sortField='time',sortDir='desc';function render(){const a=[...all].sort((x,y)=>{const av=x[sortField],bv=y[sortField];const an=Number(av),bn=Number(bv);let c=Number.isFinite(an)&&Number.isFinite(bn)?an-bn:String(av??'').localeCompare(String(bv??''),undefined,{numeric:true,sensitivity:'base'});return sortDir==='asc'?c:-c});document.getElementById('sortInfo').textContent=(sortField==='time'?'Time':sortField)+' '+(sortDir==='asc'?'↑':'↓');document.getElementById('asc').classList.toggle('active',sortDir==='asc');document.getElementById('desc').classList.toggle('active',sortDir==='desc');document.getElementById('rows').innerHTML=a.map(x=>`<tr><td>${esc(x.time)}</td><td><b>${esc(x.underlying)}</b></td><td class=\"${x.signal==='BUY CALL'?'bull':'bear'}\"><b>${esc(x.signal)}</b></td><td>${esc(x.pattern)}</td><td>${n(x.price)}</td><td>${n(x.trigger_level)}</td><td>${n(x.stop_level)}</td><td>${esc(x.stop_reference)}</td><td>${esc(x.reason)}</td></tr>`).join('')||'<tr><td colspan=\"9\" class=\"empty\">No reversal history found.</td></tr>'}async function refresh()",
    1
).replace(
    'const d=await r.json(),a=d.results||[];document.getElementById(\'count\').textContent=a.length;',
    'const d=await r.json(),a=d.results||[];all=a;document.getElementById(\'count\').textContent=a.length;', 1
).replace(
    'document.getElementById(\'rows\').innerHTML=a.slice(0,100).map(x=>`<tr><td>${esc(x.time)}</td><td><b>${esc(x.underlying)}</b></td><td class="${x.signal===\'BUY CALL\'?\'bull\':\'bear\'}"><b>${esc(x.signal)}</b></td><td>${esc(x.pattern)}</td><td>${n(x.price)}</td><td>${n(x.trigger_level)}</td><td>${n(x.stop_level)}</td><td>${esc(x.stop_reference)}</td><td>${esc(x.reason)}</td></tr>`).join(\'\')||\'<tr><td colspan="9" class="empty">No reversal detected yet.</td></tr>\'}catch',
    'render()}catch', 1
).replace(
    'refresh();setInterval(refresh,10000);', 'refresh();setInterval(refresh,10000);', 1
)
