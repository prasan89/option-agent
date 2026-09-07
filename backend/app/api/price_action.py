from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.intelligence.fno_scanner import fno_scanner
from app.intelligence.historical_session import historical_session_analyzer
from app.strategy.price_action_engine import build_price_action_signals

router = APIRouter(tags=["price-action"])
IST = ZoneInfo("Asia/Kolkata")


def _source_payload() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    scan = fno_scanner.stats
    hist = historical_session_analyzer.stats
    if hist.get("results"):
        # Historical price-action signals can be displayed after hours. They
        # are only promoted when the underlying analyzer provides real fields.
        return hist.get("latest_rows", []), hist.get("underlying_rankings", []), hist
    return scan.get("latest_rankings", []), scan.get("top_underlyings", []), scan


@router.get("/price-action/status")
def price_action_status() -> dict[str, Any]:
    rows, underlyings, source = _source_payload()
    results = build_price_action_signals(rows, underlyings)
    return {
        "status": "READY" if results["count"] else "WAITING",
        "count": results["count"],
        "method": results["method"],
        "source_mode": source.get("mode"),
        "target_date": source.get("target_date"),
        "research_only": True,
        "trading": "DISABLED",
        "note": results["note"],
    }


@router.get("/price-action/results")
def price_action_results(min_score: float = 65.0) -> dict[str, Any]:
    rows, underlyings, source = _source_payload()
    results = build_price_action_signals(rows, underlyings, min_score=min_score)
    return {
        **results,
        "source_mode": source.get("mode"),
        "target_date": source.get("target_date"),
        "updated_at": datetime.now(IST).isoformat(),
    }


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SLO Price Action Research</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#090e1b;color:#e8edf7;--panel:#121a2c;--border:#26334d;--muted:#8e9ab0;--green:#42d392;--red:#ff6f7d;--amber:#f4c95d;--blue:#71a7ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d 0,#080d19 42%);min-height:100vh}.wrap{max-width:1550px;margin:auto;padding:26px}.top{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:18px}.title{font-size:30px;font-weight:800}.sub{color:var(--muted);margin-top:5px}.nav{display:flex;gap:8px;margin-top:12px}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--border);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.status{padding:9px 13px;border:1px solid var(--border);border-radius:999px;background:#10182a;font-size:13px}.notice{padding:12px 15px;border:1px solid #3a3040;border-radius:10px;background:#171c2d;color:#bdc7d8;font-size:13px;margin-bottom:15px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:14px}.card,.panel{background:rgba(18,26,44,.96);border:1px solid var(--border);border-radius:14px}.card{padding:15px}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:24px;font-weight:800;margin-top:7px}.small{font-size:12px;color:var(--muted);margin-top:4px}.bull{color:var(--green)}.bear{color:var(--red)}.amber{color:var(--amber)}.panel{overflow:hidden;margin-bottom:15px}.panelhead{padding:16px 19px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:10px}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1200px}th,td{text-align:left;padding:11px 13px;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--muted);font-weight:600;background:#0f1728;position:sticky;top:0}.score{font-weight:800}.empty{padding:40px;text-align:center;color:var(--muted)}.detail{padding:18px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.metric{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:12px}.metric span{display:block;color:var(--muted);font-size:11px}.metric b{display:block;margin-top:5px;font-size:16px}.tag{padding:4px 7px;border-radius:6px;background:#202c44}@media(max-width:1100px){.grid{grid-template-columns:repeat(3,1fr)}.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:700px){.grid{grid-template-columns:repeat(2,1fr)}.wrap{padding:14px}.top{display:block}.status{margin-top:14px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">SLO Price Action</div><div class="sub">Daily pattern detection • intraday confirmation • research only</div><div class="nav"><a href="/dashboard">Flow Dashboard</a><a href="/strategy">SLO Options</a><a href="/price-action">Price Action</a></div></div><div id="status" class="status">Connecting…</div></div>
<div id="notice" class="notice">Loading price-action research state…</div>
<div class="grid"><div class="card"><div class="label">Signals</div><div id="count" class="value">—</div><div class="small">score ≥ 65</div></div><div class="card"><div class="label">Best Score</div><div id="best" class="value">—</div></div><div class="card"><div class="label">Breakouts</div><div id="buy" class="value bull">—</div></div><div class="card"><div class="label">Breakdowns</div><div id="sell" class="value bear">—</div></div><div class="card"><div class="label">Source</div><div id="source" class="value" style="font-size:16px">—</div><div id="date" class="small">—</div></div></div>
<div class="panel"><div class="panelhead"><strong>Triggered Price Action Signals</strong><span id="updated" class="small">—</span></div><div class="tablewrap"><table><thead><tr><th>#</th><th>Symbol</th><th>Underlying</th><th>Signal</th><th>Pattern</th><th>Score</th><th>Status</th><th>Trigger</th><th>Price</th><th>EMA20</th><th>EMA50</th><th>Volume Ratio</th><th>Fib</th><th>Reason</th></tr></thead><tbody id="rows"><tr><td colspan="14" class="empty">No confirmed price-action signals yet.</td></tr></tbody></table></div></div>
<div class="panel"><div class="panelhead"><strong>Method</strong><span class="small">Adapted from slo-price-action</span></div><div class="detail"><div class="metrics"><div class="metric"><span>Pattern families</span><b>Breakout/Breakdown, H&amp;S, Harmonics, Flags, Triangles, Wedges, Channels, Cup &amp; Handle, Rounding</b></div><div class="metric"><span>Score enrichment</span><b>Pattern 55% + trend 20% + volume 15% + Fibonacci context</b></div><div class="metric"><span>Decision</span><b>Dominant BUY / SELL; conflicts become WATCH</b></div><div class="metric"><span>Status</span><b class="amber">Research only — no orders</b></div></div><p class="small">The reference repository detects patterns from daily candles, enriches them with EMA20/EMA50, volume and Fibonacci context, then arbitrates a dominant plan. This tab only shows signals when equivalent price-action fields are present; it does not fabricate a pattern from option movement alone.</p></div></div>
</div><script>
const api=location.origin;function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}function fmt(v,d=2){if(v==null||v==='')return '—';const n=Number(v);return Number.isFinite(n)?n.toFixed(d):esc(v)}
async function refresh(){try{const r=await fetch(api+'/price-action/results?min_score=65');if(!r.ok)throw Error('API '+r.status);const d=await r.json();const a=d.results||[];document.getElementById('count').textContent=a.length;document.getElementById('best').textContent=a.length?fmt(a[0].score):'—';document.getElementById('buy').textContent=a.filter(x=>x.signal==='BUY').length;document.getElementById('sell').textContent=a.filter(x=>x.signal==='SELL').length;document.getElementById('source').textContent=d.source_mode||'—';document.getElementById('date').textContent=d.target_date||'live';document.getElementById('status').textContent=a.length?'● Signal available':'○ Waiting';document.getElementById('notice').textContent=a.length?'Price-action signal detected. Review trigger, pattern and confirmation before any decision.':'No confirmed SLO Price Action signal at the selected threshold. The scanner will populate this tab when a real price-action signal is available.';document.getElementById('updated').textContent=new Date(d.updated_at).toLocaleTimeString('en-IN');const body=document.getElementById('rows');if(!a.length){body.innerHTML='<tr><td colspan="14" class="empty">No confirmed price-action signals yet.</td></tr>';return}body.innerHTML=a.map((x,i)=>`<tr><td>${i+1}</td><td><strong>${esc(x.symbol)}</strong></td><td>${esc(x.underlying)}</td><td class="${x.signal==='BUY'?'bull':'bear'}">${esc(x.signal)}</td><td>${esc(x.pattern)}</td><td class="score">${fmt(x.score)}</td><td><span class="tag">${esc(x.trigger_state)}</span></td><td>${fmt(x.trigger_level)}</td><td>${fmt(x.price)}</td><td>${fmt(x.ema20)}</td><td>${fmt(x.ema50)}</td><td>${fmt(x.volume_ratio)}</td><td>${fmt(x.fib_level,3)}</td><td>${esc(x.reason)}</td></tr>`).join('')}catch(e){document.getElementById('status').textContent='API unavailable';document.getElementById('notice').textContent='Unable to load Price Action signals: '+e.message}}
refresh();setInterval(refresh,10000);
</script></body></html>'''


@router.get("/price-action", response_class=HTMLResponse, include_in_schema=False)
def price_action_dashboard() -> str:
    return HTML
