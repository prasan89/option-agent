from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI F&O Flow Dashboard</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#0b1020;color:#e8ecf5}*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#0b1020,#11182b);min-height:100vh}.wrap{max-width:1400px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}.title{font-size:28px;font-weight:750}.sub{color:#8d98ad;margin-top:5px}.status{padding:8px 13px;border:1px solid #26324a;border-radius:999px;font-size:13px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}.card{background:#141c30;border:1px solid #25314a;border-radius:14px;padding:18px}.label{color:#8d98ad;font-size:12px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:25px;font-weight:700;margin-top:8px}.panel{background:#141c30;border:1px solid #25314a;border-radius:14px;overflow:hidden}.panelhead{padding:18px 20px;border-bottom:1px solid #25314a;display:flex;justify-content:space-between}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1000px}th,td{text-align:left;padding:13px 16px;border-bottom:1px solid #202a40;font-size:13px}th{color:#8d98ad;font-weight:600;background:#11182a}.bull{color:#42d392}.bear{color:#ff6f7d}.muted{color:#8d98ad}.score{font-weight:750}.pill{padding:4px 8px;border-radius:6px;background:#202b43}.empty{padding:45px;text-align:center;color:#8d98ad}@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}.wrap{padding:16px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">AI F&O Flow Dashboard</div><div class="sub">Research signals • 5-minute persistence cycle • Trading disabled</div></div><div id="status" class="status">Connecting…</div></div>
<div class="grid"><div class="card"><div class="label">Persisted Signals</div><div id="count" class="value">—</div></div><div class="card"><div class="label">Monitor Checks</div><div id="checks" class="value">—</div></div><div class="card"><div class="label">Last Check</div><div id="last" class="value" style="font-size:16px">—</div></div><div class="card"><div class="label">Minimum Score</div><div id="minscore" class="value">—</div></div></div>
<div class="panel"><div class="panelhead"><strong>Latest Signals</strong><span class="muted">Auto-refresh 15s</span></div><div class="tablewrap"><table><thead><tr><th>Time</th><th>Symbol</th><th>Underlying</th><th>Type</th><th>Bias</th><th>Direction</th><th>Score</th><th>Confidence</th><th>LTP</th><th>Event</th><th>Evidence</th></tr></thead><tbody id="rows"><tr><td colspan="11" class="empty">Loading…</td></tr></tbody></table></div></div>
</div><script>
const api=location.origin;
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
async function refresh(){try{const [s,r]=await Promise.all([fetch(api+'/signals/status'),fetch(api+'/signals?limit=100')]);if(!s.ok||!r.ok)throw Error();const st=await s.json(), rows=await r.json();document.getElementById('status').textContent=st.running?'● Monitor running':'○ Monitor stopped';document.getElementById('count').textContent=st.persisted_signals??'—';document.getElementById('checks').textContent=st.checks??'—';document.getElementById('last').textContent=st.last_check?new Date(st.last_check).toLocaleTimeString():'—';document.getElementById('minscore').textContent='±'+st.minimum_score;const body=document.getElementById('rows');if(!rows.length){body.innerHTML='<tr><td colspan="11" class="empty">No qualifying signals yet. The monitor will persist signals when score and confidence thresholds are met.</td></tr>';return}body.innerHTML=rows.map(x=>{const c=x.bias==='BULLISH'?'bull':x.bias==='BEARISH'?'bear':'';return `<tr><td class="muted">${esc(new Date(x.created_at).toLocaleString())}</td><td><strong>${esc(x.symbol)}</strong></td><td>${esc(x.underlying)}</td><td>${esc(x.instrument_type)}</td><td class="${c}">${esc(x.bias)}</td><td class="${c}">${esc(x.direction)}</td><td class="score">${esc(x.score)}</td><td><span class="pill">${esc(x.confidence)}</span></td><td>${esc(x.ltp)}</td><td>${esc(x.event)}</td><td class="muted">${esc((x.evidence||[]).join(', '))}</td></tr>`}).join('')}catch(e){document.getElementById('status').textContent='Database/API unavailable'}}refresh();setInterval(refresh,15000);
</script></body></html>'''


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    return HTML
