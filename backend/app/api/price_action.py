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
            key = f"{row.get('underlying')}:{row.get('signal')}:{row.get('trigger_level')}:{row.get('time') or row.get('created_at')}"
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
    return {
        "status": "READY" if results else "WAITING",
        "count": len(results),
        "scanner": stats,
        "method": "SLO_PRICE_ACTION_LIVE_GROWW",
        "research_only": True,
        "trading": "DISABLED",
        "note": "Daily price-action patterns are confirmed with 5-minute Groww candles and volume before a BUY/SELL signal is emitted.",
    }


@router.get("/price-action/results")
def price_action_results(min_score: float = 65.0) -> dict[str, Any]:
    results = [x for x in _results() if float(x.get("score") or 0) >= min_score]
    stats = price_action_scanner.stats
    return {
        "count": len(results),
        "results": results,
        "source_mode": stats.get("mode"),
        "cache_date": stats.get("cache_date"),
        "scanner": stats,
        "method": "SLO_PRICE_ACTION_LIVE_GROWW",
        "research_only": True,
        "trading": "DISABLED",
        "updated_at": datetime.now(IST).isoformat(),
    }


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SLO Price Action</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#090e1b;color:#e8edf7;--p:#121a2c;--b:#26334d;--m:#8e9ab0;--g:#42d392;--r:#ff6f7d;--a:#f4c95d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d,#080d19 42%);min-height:100vh}.wrap{max-width:1550px;margin:auto;padding:26px}.top{display:flex;justify-content:space-between;gap:18px;margin-bottom:18px}.title{font-size:30px;font-weight:800}.sub,.small{color:var(--m);font-size:12px;margin-top:5px}.nav{display:flex;gap:8px;margin-top:12px}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--b);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.nav a.active{color:#fff;border-color:#52678f}.status{padding:9px 13px;border:1px solid var(--b);border-radius:999px;background:#10182a;font-size:13px;height:max-content}.notice{padding:13px 15px;border:1px solid #3a3040;border-radius:10px;background:#171c2d;margin-bottom:15px;font-size:13px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:15px}.card,.panel{background:rgba(18,26,44,.96);border:1px solid var(--b);border-radius:14px}.card{padding:15px}.label{color:var(--m);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:24px;font-weight:800;margin-top:7px}.bull{color:var(--g)}.bear{color:var(--r)}.amber{color:var(--a)}.panel{overflow:hidden;margin-bottom:15px}.head{padding:16px 19px;border-bottom:1px solid var(--b);display:flex;justify-content:space-between;align-items:center}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1200px}th,td{text-align:left;padding:11px 13px;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--m);background:#0f1728}.score{font-weight:800}.empty{padding:42px;text-align:center;color:var(--m)}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:18px}.metric{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:13px}.metric span{display:block;color:var(--m);font-size:11px}.metric b{display:block;margin-top:5px;font-size:15px}.tag{padding:4px 7px;border-radius:6px;background:#202c44}@media(max-width:1100px){.grid{grid-template-columns:repeat(3,1fr)}.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:700px){.wrap{padding:14px}.grid{grid-template-columns:repeat(2,1fr)}.top{display:block}.status{margin-top:14px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">SLO Price Action</div><div class="sub">Daily pattern detection • 5-minute confirmation • research only</div><div class="nav"><a href="/dashboard">Flow Dashboard</a><a href="/strategy">SLO Options</a><a href="/price-action" class="active">Price Action</a></div></div><div id="status" class="status">Connecting…</div></div>
<div id="notice" class="notice">Waiting for the price-action scanner…</div>
<div class="grid"><div class="card"><div class="label">Signals</div><div id="count" class="value">—</div><div class="small">score ≥ 65</div></div><div class="card"><div class="label">Best Score</div><div id="best" class="value">—</div></div><div class="card"><div class="label">BUY</div><div id="buy" class="value bull">—</div></div><div class="card"><div class="label">SELL</div><div id="sell" class="value bear">—</div></div><div class="card"><div class="label">Cached Underlyings</div><div id="cache" class="value">—</div><div id="cacheDate" class="small">—</div></div></div>
<div class="panel"><div class="head"><strong>Triggered Price Action Signals</strong><span id="updated" class="small">—</span></div><div class="table"><table><thead><tr><th>#</th><th>Time</th><th>Symbol</th><th>Signal</th><th>Pattern</th><th>Score</th><th>Trigger</th><th>5M Price</th><th>5M Vol</th><th>EMA20</th><th>EMA50</th><th>Fib</th><th>Reason</th></tr></thead><tbody id="rows"><tr><td colspan="13" class="empty">No confirmed price-action signal yet.</td></tr></tbody></table></div></div>
<div class="panel"><div class="head"><strong>Scanner Health</strong><span class="small">Groww historical candles</span></div><div class="metrics"><div class="metric"><span>Daily Requests</span><b id="daily">—</b></div><div class="metric"><span>5-Min Requests</span><b id="intraday">—</b></div><div class="metric"><span>Checks</span><b id="checks">—</b></div><div class="metric"><span>Errors</span><b id="errors">—</b></div></div></div>
<div class="panel"><div class="head"><strong>Method</strong><span class="small">Reference: slo-price-action</span></div><div class="metrics"><div class="metric"><span>Pattern layer</span><b>Breakouts, breakdowns, H&amp;S, triangles, wedges, flags and rounding patterns</b></div><div class="metric"><span>Score</span><b>Pattern 55% + trend 20% + volume 15% + Fibonacci context</b></div><div class="metric"><span>Confirmation</span><b>5-minute trigger cross + volume ratio ≥ 1.0x</b></div><div class="metric"><span>Safety</span><b class="amber">Research only — no orders are placed</b></div></div></div>
</div><script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const fmt=(v,d=2)=>v==null||v===''?'—':Number.isFinite(Number(v))?Number(v).toFixed(d):esc(v);
async function refresh(){try{const r=await fetch('/price-action/results?min_score=65');if(!r.ok)throw Error('API '+r.status);const d=await r.json(),a=d.results||[],s=d.scanner||{};document.getElementById('count').textContent=a.length;document.getElementById('best').textContent=a.length?fmt(a[0].score):'—';document.getElementById('buy').textContent=a.filter(x=>x.signal==='BUY').length;document.getElementById('sell').textContent=a.filter(x=>x.signal==='SELL').length;document.getElementById('cache').textContent=s.cached_underlyings??'—';document.getElementById('cacheDate').textContent=s.cache_date||'waiting';document.getElementById('daily').textContent=s.daily_requests??'—';document.getElementById('intraday').textContent=s.intraday_requests??'—';document.getElementById('checks').textContent=s.checks??'—';document.getElementById('errors').textContent=s.errors??'—';document.getElementById('status').innerHTML=a.length?'<span class="bull">● Signal available</span>':'○ Scanner waiting';document.getElementById('notice').textContent=a.length?'A confirmed SLO Price Action signal has been generated. Review the pattern, trigger and 5-minute confirmation.':'No confirmed price-action signal at the selected threshold. The scanner checks during NSE F&O market hours.';document.getElementById('updated').textContent=new Date(d.updated_at).toLocaleTimeString('en-IN');const body=document.getElementById('rows');if(!a.length){body.innerHTML='<tr><td colspan="13" class="empty">No confirmed price-action signal yet.</td></tr>';return}body.innerHTML=a.map((x,i)=>`<tr><td>${i+1}</td><td>${esc(x.time||x.created_at)}</td><td><strong>${esc(x.symbol)}</strong></td><td class="${x.signal==='BUY'?'bull':'bear'}">${esc(x.signal)}</td><td>${esc(x.pattern)}</td><td class="score">${fmt(x.score)}</td><td>${fmt(x.trigger_level)}</td><td>${fmt(x.close_5min||x.price)}</td><td>${fmt(x.vol_ratio_5min)}</td><td>${fmt(x.ema20)}</td><td>${fmt(x.ema50)}</td><td>${fmt(x.fib_level,3)}</td><td>${esc(x.reason)}</td></tr>`).join('')}catch(e){document.getElementById('status').textContent='API unavailable';document.getElementById('notice').textContent='Unable to load Price Action signals: '+e.message}}
refresh();setInterval(refresh,10000);
</script></body></html>'''


@router.get("/price-action", response_class=HTMLResponse, include_in_schema=False)
def price_action_dashboard() -> str:
    return HTML
