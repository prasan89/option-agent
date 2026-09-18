from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.signals.price_action_paper_tracker import price_action_paper_tracker

router = APIRouter(tags=["price-action-paper"])

HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Price Action Paper P&L</title>
<style>body{margin:0;background:#090e1b;color:#e8edf7;font:13px system-ui}.wrap{max-width:1500px;margin:auto;padding:24px}.nav{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}.nav a{color:#b9c7df;text-decoration:none;border:1px solid #26334d;padding:7px 10px;border-radius:8px;background:#10182a}.panel{background:#121a2c;border:1px solid #26334d;border-radius:14px;margin:14px 0;overflow:auto}.cards{display:grid;grid-template-columns:repeat(7,1fr);gap:10px}.card{padding:14px}.label{color:#8e9ab0;font-size:11px;text-transform:uppercase}.value{font-size:23px;font-weight:800;margin-top:5px}.profit{color:#42d392}.loss{color:#ff6f7d}.table{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1250px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #202a40;white-space:nowrap;font-size:12px}th{color:#8e9ab0;background:#0f1728}.sub{color:#8e9ab0}.empty{text-align:center;padding:35px;color:#8e9ab0}</style></head><body><div class="wrap">
<h1>CANDLESTICK_REVERSAL — Paper P&L</h1><div class="sub">Daily candlestick setup → 15-minute close confirmation → underlying paper P&L. No broker execution.</div>
<div class="nav"><a href="/price-action">Price Action</a><a href="/price-action/history">Price Action History</a><a href="/paper-tracker">SLO Option P&L</a><a href="/reversal">Reversal</a></div>
<div class="panel"><div class="cards"><div class="card"><div class="label">Signals</div><div id="signals" class="value">—</div></div><div class="card"><div class="label">Profit</div><div id="profit" class="value profit">—</div></div><div class="card"><div class="label">Loss</div><div id="loss" class="value loss">—</div></div><div class="card"><div class="label">Open</div><div id="open" class="value">—</div></div><div class="card"><div class="label">Target Hit</div><div id="target" class="value">—</div></div><div class="card"><div class="label">Stop Hit</div><div id="stop" class="value">—</div></div><div class="card"><div class="label">Mark P&L</div><div id="pnl" class="value">—</div></div></div></div>
<div class="panel"><div class="table"><table><thead><tr><th>Generated IST</th><th>Entry IST</th><th>Exit IST</th><th>Underlying</th><th>Pattern</th><th>Direction</th><th>Score</th><th>Entry</th><th>Current</th><th>Stop</th><th>Target</th><th>P&L</th><th>P&L %</th><th>Status</th><th>Reason</th></tr></thead><tbody id="rows"></tbody></table></div></div>
<script>
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));const n=(v,d=2)=>v==null?'—':Number(v).toFixed(d);const ist=v=>v?new Date(v).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'—';
async function refresh(){try{const d=await (await fetch('/price-action/paper-pnl/data')).json(),m=d.metrics||{};for(const k of ['signals','profit','loss','open','target','stop'])document.getElementById(k).textContent=m[k==='target'?'target_hit':k==='stop'?'stop_hit':k]??0;document.getElementById('pnl').textContent='₹ '+n(m.mark_to_market_pnl);const rows=d.rows||[];document.getElementById('rows').innerHTML=rows.length?rows.map(x=>'<tr><td>'+ist(x.generated_at)+'</td><td>'+ist(x.entry_at)+'</td><td>'+ist(x.exit_at)+'</td><td><b>'+esc(x.underlying)+'</b></td><td>'+esc(x.pattern)+'</td><td>'+esc(x.direction)+'</td><td>'+n(x.score)+'</td><td>'+n(x.entry_price)+'</td><td>'+n(x.current_price)+'</td><td>'+n(x.stop_price)+'</td><td>'+n(x.target_price)+'</td><td class="'+(x.mark_pnl>=0?'profit':'loss')+'">'+n(x.mark_pnl)+'</td><td>'+n(x.pnl_pct)+'%</td><td>'+esc(x.status)+'</td><td>'+esc(x.outcome_reason)+'</td></tr>').join(''):'<tr><td colspan="15" class="empty">No CANDLESTICK_REVERSAL signals tracked yet.</td></tr>'}catch(e){document.getElementById('rows').innerHTML='<tr><td colspan="15" class="empty">Tracker unavailable: '+esc(e.message)+'</td></tr>'}}refresh();setInterval(refresh,10000);
</script></div></body></html>"""

@router.get("/price-action/paper-pnl/data")
def data():
    price_action_paper_tracker.evaluate_open()
    return {"metrics":price_action_paper_tracker.metrics(),"rows":price_action_paper_tracker.rows(500)}

@router.get("/price-action/paper-pnl", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTML
