from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.jft.scanner import jft_scanner

router = APIRouter(tags=["jft"])
IST = ZoneInfo("Asia/Kolkata")


@router.get("/jft/status")
def jft_status() -> dict[str, Any]:
    stats = jft_scanner.stats
    return {
        "status": "READY" if stats.get("signals") else "WAITING",
        "scanner": stats,
        "method": "HISTORICAL_5MIN_JFT",
        "rule": "5M CLOSE CROSS R3 => BUY CALL / SL R2; 5M CLOSE CROSS S3 => BUY PUT / SL S2",
        "research_only": True,
        "trading": "DISABLED",
    }


@router.get("/jft/results")
def jft_results() -> dict[str, Any]:
    stats = jft_scanner.stats
    rows = stats.get("signals", [])
    return {
        "count": len(rows),
        "results": rows,
        "scanner": stats,
        "updated_at": datetime.now(IST).isoformat(),
        "research_only": True,
        "trading": "DISABLED",
    }


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>JFT Signals</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#090e1b;color:#e8edf7;--b:#26334d;--m:#8e9ab0;--g:#42d392;--r:#ff6f7d;--a:#f4c95d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d,#080d19 42%);min-height:100vh}.wrap{max-width:1600px;margin:auto;padding:25px}.top{display:flex;justify-content:space-between;gap:18px}.title{font-size:30px;font-weight:800}.sub{color:var(--m);font-size:12px;margin-top:5px}.nav{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--b);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.nav a.active{color:#fff;border-color:#52678f}.status{padding:9px 13px;border:1px solid var(--b);border-radius:999px;background:#10182a;height:max-content}.notice,.panel,.card{background:rgba(18,26,44,.96);border:1px solid var(--b);border-radius:14px}.notice{padding:13px;margin:16px 0;font-size:13px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:15px}.card{padding:14px}.label{color:var(--m);font-size:11px;text-transform:uppercase}.value{font-size:23px;font-weight:800;margin-top:6px}.panel{overflow:hidden;margin-bottom:15px}.head{padding:15px 18px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}.sortbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:11px 18px;border-bottom:1px solid var(--b);background:#0e1627}.sortbar label,.small{color:var(--m);font-size:12px}.sortbar select,.sortbar button{border:1px solid var(--b);background:#10182a;color:#b9c7df;border-radius:7px;padding:7px 10px;cursor:pointer}.sortbar button.active{color:#fff;border-color:#52678f;background:#17233b}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1500px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--m);background:#0f1728}.sortable{cursor:pointer}.sortmark{font-size:10px;margin-left:4px;color:#71a7ff}.bull{color:var(--g)}.bear{color:var(--r)}.empty{padding:42px;text-align:center;color:var(--m)}.note{padding:13px 18px;color:#b7c2d5;font-size:12px;border-top:1px solid var(--b)}@media(max-width:1000px){.grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:650px){.wrap{padding:13px}.grid{grid-template-columns:repeat(2,1fr)}.top{display:block}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">JFT Signals</div><div class="sub">Historical 5-minute R3 / S3 level-cross detection • research only • trading disabled</div><div class="nav"><a href="/dashboard">Dashboard</a><a href="/strategy">SLO Options</a><a href="/dashboard/history">SLO History</a><a href="/price-action">Price Action</a><a href="/price-action/history">Price Action History</a><a href="/jft" class="active">JFT Signals</a></div></div><div id="status" class="status">Loading…</div></div>
<div id="notice" class="notice">JFT scans historical 5-minute candles and detects the first close crossing R3 or S3 for each session.</div>
<div class="grid"><div class="card"><div class="label">Signals</div><div id="count" class="value">—</div></div><div class="card"><div class="label">BUY CALL</div><div id="calls" class="value bull">—</div></div><div class="card"><div class="label">BUY PUT</div><div id="puts" class="value bear">—</div></div><div class="card"><div class="label">Underlyings</div><div id="underlyings" class="value">—</div></div><div class="card"><div class="label">5M Requests</div><div id="requests" class="value">—</div></div></div>
<div class="panel"><div class="head"><b>JFT Level-Cross Signals</b><span id="updated" class="small">—</span></div><div class="sortbar"><label>Sort</label><select id="sortField" onchange="setSortField(this.value)"><option value="created_at">Time</option><option value="underlying">Underlying</option><option value="signal">Signal</option><option value="trigger_level">Trigger</option><option value="stop_level">Stop</option><option value="price">5M Price</option><option value="r3">R3</option><option value="r2">R2</option><option value="s3">S3</option><option value="s2">S2</option></select><button id="asc" onclick="setSortDir('asc')">↑ ASC</button><button id="desc" class="active" onclick="setSortDir('desc')">↓ DESC</button><span id="sortInfo" class="small">Time ↓</span></div><div class="table"><table><thead><tr><th>#</th><th>Time</th><th class="sortable" onclick="sortBy('underlying')">Underlying<span id="m-underlying" class="sortmark"></span></th><th class="sortable" onclick="sortBy('signal')">Signal<span id="m-signal" class="sortmark"></span></th><th>Trigger</th><th class="sortable" onclick="sortBy('trigger_level')">Trigger Level<span id="m-trigger_level" class="sortmark"></span></th><th class="sortable" onclick="sortBy('stop_level')">SL<span id="m-stop_level" class="sortmark"></span></th><th>5M Price</th><th>Pivot</th><th>R2</th><th>R3</th><th>S2</th><th>S3</th><th>Reason</th></tr></thead><tbody id="rows"></tbody></table></div><div class="note">JFT rule: a completed 5-minute candle close crossing above the previous session's R3 generates BUY CALL with SL at R2. A close crossing below S3 generates BUY PUT with SL at S2. Historical signals are research records only.</div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const n=(v,d=2)=>v==null?'—':Number.isFinite(Number(v))?Number(v).toFixed(d):esc(v);let all=[];let sortField='created_at';let sortDir='desc';const labels={created_at:'Time',underlying:'Underlying',signal:'Signal',trigger_level:'Trigger',stop_level:'Stop',price:'5M Price',r3:'R3',r2:'R2',s3:'S3',s2:'S2'};function setSortField(v){sortField=v;render()}function setSortDir(v){sortDir=v;document.getElementById('asc').classList.toggle('active',v==='asc');document.getElementById('desc').classList.toggle('active',v==='desc');render()}function sortBy(v){if(sortField===v)sortDir=sortDir==='asc'?'desc':'asc';else{sortField=v;sortDir='desc'}document.getElementById('sortField').value=v;document.getElementById('asc').classList.toggle('active',sortDir==='asc');document.getElementById('desc').classList.toggle('active',sortDir==='desc');render()}function cmp(a,b){const av=a?.[sortField],bv=b?.[sortField];if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;const an=Number(av),bn=Number(bv);if(Number.isFinite(an)&&Number.isFinite(bn))return an-bn;return String(av).localeCompare(String(bv),undefined,{numeric:true,sensitivity:'base'})}function render(){const rows=[...all].sort((a,b)=>{const c=cmp(a,b);return sortDir==='asc'?c:-c});document.getElementById('sortInfo').textContent=labels[sortField]+' '+(sortDir==='asc'?'↑':'↓');const body=document.getElementById('rows');if(!rows.length){body.innerHTML='<tr><td colspan="14" class="empty">No JFT level-cross signal detected yet.</td></tr>';return}body.innerHTML=rows.map((x,i)=>`<tr><td>${i+1}</td><td>${esc(x.time||x.created_at)}</td><td><b>${esc(x.underlying)}</b></td><td class="${x.direction==='BULLISH'?'bull':'bear'}"><b>${esc(x.signal)}</b></td><td>${esc(x.trigger)}</td><td>${n(x.trigger_level)}</td><td>${n(x.stop_level)}</td><td>${n(x.price)}</td><td>${n(x.pivot)}</td><td>${n(x.r2)}</td><td>${n(x.r3)}</td><td>${n(x.s2)}</td><td>${n(x.s3)}</td><td>${esc(x.reason)}</td></tr>`).join('')}async function refresh(){try{const r=await fetch('/jft/results');if(!r.ok)throw Error('API '+r.status);const d=await r.json(),a=d.results||[],s=d.scanner||{};all=a;document.getElementById('count').textContent=a.length;document.getElementById('calls').textContent=a.filter(x=>x.signal==='BUY CALL').length;document.getElementById('puts').textContent=a.filter(x=>x.signal==='BUY PUT').length;document.getElementById('underlyings').textContent=new Set(a.map(x=>x.underlying)).size;document.getElementById('requests').textContent=s.requests??'—';document.getElementById('status').textContent=s.running?'● Scanner running':'○ Scanner stopped';document.getElementById('updated').textContent=d.updated_at?new Date(d.updated_at).toLocaleTimeString('en-IN'):'—';render()}catch(e){document.getElementById('status').textContent='API unavailable';document.getElementById('notice').textContent='Unable to load JFT signals: '+e.message}}refresh();setInterval(refresh,10000);
</script></div></body></html>'''


@router.get("/jft", response_class=HTMLResponse, include_in_schema=False)
def jft_dashboard() -> str:
    return HTML
