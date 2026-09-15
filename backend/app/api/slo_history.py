from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.signals.store import signal_store

router = APIRouter(tags=["slo-history"])


def _rows() -> list[dict[str, Any]]:
    try:
        return signal_store.opportunity_history(day=date.today(), limit=500)
    except Exception:
        return []


@router.get("/dashboard/history-data")
def history_data() -> dict[str, Any]:
    rows = _rows()
    return {
        "date": date.today().isoformat(),
        "count": len(rows),
        "rows": rows,
        "research_only": True,
        "trading": "DISABLED",
    }


@router.get("/dashboard/history", response_class=HTMLResponse, include_in_schema=False)
def history_page() -> str:
    return HTML


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SLO Call History & Timing</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#080d19;color:#e8edf7;--p:#121a2c;--b:#26334d;--m:#8e9ab0;--g:#42d392;--r:#ff6f7d;--a:#f4c95d;--x:#71a7ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d,#080d19 45%);min-height:100vh}.wrap{max-width:1560px;margin:auto;padding:25px}.top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;margin-bottom:16px}.title{font-size:29px;font-weight:800}.sub{color:var(--m);font-size:12px;margin-top:5px}.nav{display:flex;gap:8px;margin-top:12px}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--b);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.badge{border:1px solid var(--b);border-radius:999px;padding:9px 13px;background:#10182a}.panel{background:rgba(18,26,44,.96);border:1px solid var(--b);border-radius:14px;overflow:hidden;margin-bottom:15px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:14px}.card{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:13px}.label{color:var(--m);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:22px;font-weight:800;margin-top:5px}.head{padding:15px 18px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}.small{color:var(--m);font-size:12px}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1500px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--m);background:#0f1728;position:sticky;top:0}.bull{color:var(--g)}.bear{color:var(--r)}.live{color:var(--g)}.gone{color:var(--a)}.empty{padding:45px;text-align:center;color:var(--m)}.note{padding:13px 18px;color:#b7c2d5;font-size:12px;border-top:1px solid var(--b)}
@media(max-width:800px){.wrap{padding:13px}.top{display:block}.badge{display:inline-block;margin-top:12px}.stats{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">SLO Call History & Timing</div><div class="sub">Persistent research calls detected during today's live session</div><div class="nav"><a href="/dashboard">Dashboard</a><a href="/strategy">SLO Strategy</a><a href="/price-action">Price Action</a><a href="/price-action/history">Price Action History</a></div></div><div id="clock" class="badge">Loading…</div></div>
<div class="panel"><div class="stats"><div class="card"><div class="label">Calls Recorded</div><div id="count" class="value">—</div></div><div class="card"><div class="label">Live Calls</div><div id="live" class="value live">—</div></div><div class="card"><div class="label">No Longer Qualifies</div><div id="gone" class="value">—</div></div><div class="card"><div class="label">Last Detection</div><div id="last" class="value" style="font-size:17px">—</div></div></div></div>
<div class="panel"><div class="head"><b>📌 Option Calls — First Seen / Last Seen</b><span id="day" class="small"></span></div><div class="table"><table><thead><tr><th>Status</th><th>First Seen (IST)</th><th>Last Seen (IST)</th><th>Last Qualified (IST)</th><th>Underlying</th><th>Option</th><th>Direction</th><th>Call</th><th>Score</th><th>Peak</th><th>Premium</th><th>Stop</th><th>Target</th><th>DTE</th><th>Delta</th><th>Theta</th><th>IV</th><th>Volume</th><th>OI</th></tr></thead><tbody id="rows"></tbody></table></div><div class="note">Times are persisted when the SLO research pipeline qualifies a candidate. The system is research-only; no broker order is placed. A candidate remains in history after it falls below the live filters.</div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const num=(v,d=2)=>v==null||v===''?'—':Number.isFinite(Number(v))?Number(v).toFixed(d):esc(v);
function ist(v){if(!v)return '—';try{return new Date(v).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})}catch(e){return esc(v)}}
async function refresh(){try{const r=await fetch('/dashboard/history-data');if(!r.ok)throw new Error(await r.text());const d=await r.json();const rows=d.rows||[];document.getElementById('clock').textContent=new Date().toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata',hour12:false})+' IST';document.getElementById('day').textContent=d.date+' • auto refresh 10s';document.getElementById('count').textContent=rows.length;document.getElementById('live').textContent=rows.filter(x=>x.status==='LIVE').length;document.getElementById('gone').textContent=rows.filter(x=>x.status!=='LIVE').length;document.getElementById('last').textContent=rows.length?ist(rows[0].last_seen_at):'—';const body=document.getElementById('rows');if(!rows.length){body.innerHTML='<tr><td colspan="19" class="empty">No SLO call has been persisted yet. Keep the Dashboard open during market hours so qualifying opportunities are timestamped.</td></tr>';return}body.innerHTML=rows.map(x=>{const dir=String(x.direction||'');const cls=dir==='BULLISH'?'bull':dir==='BEARISH'?'bear':'';return `<tr><td class="${x.status==='LIVE'?'live':'gone'}">${esc(x.status)}</td><td>${ist(x.first_seen_at)}</td><td>${ist(x.last_seen_at)}</td><td>${ist(x.last_qualified_at)}</td><td><b>${esc(x.underlying)}</b></td><td>${esc(x.symbol||'—')}</td><td class="${cls}">${esc(dir)}</td><td class="${cls}"><b>${esc(x.signal||'—')}</b></td><td>${num(x.score)}</td><td>${num(x.peak_score)}</td><td>${num(x.premium,4)}</td><td>${num(x.stop_premium,4)}</td><td>${num(x.target_premium,4)}</td><td>${esc(x.dte??'—')}</td><td>${num(x.delta,3)}</td><td>${num(x.theta,3)}</td><td>${num(x.iv,3)}</td><td>${esc(x.volume??'—')}</td><td>${esc(x.open_interest??'—')}</td></tr>`}).join('')}catch(e){document.getElementById('rows').innerHTML='<tr><td colspan="19" class="empty">History unavailable: '+esc(e.message)+'</td></tr>'}}
refresh();setInterval(refresh,10000);
</script></div></body></html>'''
