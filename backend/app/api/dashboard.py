from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI F&O Flow Dashboard</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#0b1020;color:#e8ecf5}*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#0b1020,#11182b);min-height:100vh}.wrap{max-width:1500px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:22px}.title{font-size:28px;font-weight:750}.sub{color:#8d98ad;margin-top:5px}.status{padding:8px 13px;border:1px solid #26324a;border-radius:999px;font-size:13px}.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:14px}.pipeline{display:grid;grid-template-columns:repeat(7,1fr);gap:8px;margin-bottom:22px}.card,.stage{background:#141c30;border:1px solid #25314a;border-radius:14px;padding:16px}.label{color:#8d98ad;font-size:12px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:25px;font-weight:700;margin-top:8px}.stage{text-align:center;font-size:12px}.stage .dot{font-size:18px;margin-bottom:5px}.ok{color:#42d392}.bad{color:#ff6f7d}.muted{color:#8d98ad}.panel{background:#141c30;border:1px solid #25314a;border-radius:14px;overflow:hidden;margin-bottom:16px}.panelhead{padding:18px 20px;border-bottom:1px solid #25314a;display:flex;justify-content:space-between}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:1100px}th,td{text-align:left;padding:13px 16px;border-bottom:1px solid #202a40;font-size:13px}th{color:#8d98ad;font-weight:600;background:#11182a}.bull{color:#42d392}.bear{color:#ff6f7d}.score{font-weight:750}.pill{padding:4px 8px;border-radius:6px;background:#202b43}.empty{padding:45px;text-align:center;color:#8d98ad}.notice{margin-bottom:14px;padding:12px 14px;border:1px solid #3a3040;border-radius:10px;background:#181b2c;color:#b9c2d4;font-size:13px}.coverage{display:flex;gap:18px;flex-wrap:wrap;color:#b9c2d4;font-size:13px}.coverage b{color:#e8ecf5}@media(max-width:1200px){.grid{grid-template-columns:repeat(3,1fr)}.pipeline{grid-template-columns:repeat(4,1fr)}}@media(max-width:800px){.grid{grid-template-columns:repeat(2,1fr)}.pipeline{grid-template-columns:repeat(2,1fr)}.wrap{padding:16px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">AI F&O Flow Dashboard</div><div class="sub">Research-only market intelligence • Trading disabled</div></div><div id="status" class="status">Connecting…</div></div>
<div id="notice" class="notice">Connecting to the research pipeline…</div>
<div class="grid"><div class="card"><div class="label">Persisted Signals</div><div id="count" class="value">—</div></div><div class="card"><div class="label">Monitor Checks</div><div id="checks" class="value">—</div></div><div class="card"><div class="label">Feed Contracts</div><div id="contracts" class="value">—</div></div><div class="card"><div class="label">Feed Underlyings</div><div id="underlyings" class="value">—</div></div><div class="card"><div class="label">Enrichment Calls</div><div id="enrichment" class="value">—</div></div></div>
<div id="pipeline" class="pipeline"></div>
<div class="panel"><div class="panelhead"><strong>Live Feed Coverage</strong><span class="muted" id="scaninfo">—</span></div><div style="padding:18px 20px" class="coverage"><span>Feed contracts: <b id="covavail">—</b></span><span>Contracts with LTP: <b id="covscan">—</b></span><span>Feed events: <b id="covquotes">—</b></span><span>Ranking: <b id="covready">—</b></span></div></div>
<div class="panel"><div class="panelhead"><strong>Top Active Underlyings</strong><span class="muted">Live feed • 1m price activity + targeted enrichment</span></div><div class="tablewrap"><table><thead><tr><th>Rank</th><th>Underlying</th><th>Score</th><th>Direction</th><th>Active Contracts</th><th>Up</th><th>Down</th><th>Max 1m Change</th><th>Top Contract</th></tr></thead><tbody id="underlyingRows"><tr><td colspan="9" class="empty">Waiting for one minute of live-feed history…</td></tr></tbody></table></div></div>
<div class="panel"><div class="panelhead"><strong>Latest Qualifying Signals</strong><span class="muted">Auto-refresh 10s</span></div><div class="tablewrap"><table><thead><tr><th>Time</th><th>Symbol</th><th>Underlying</th><th>Type</th><th>Bias</th><th>Direction</th><th>Score</th><th>Confidence</th><th>ML UP</th><th>ML DOWN</th><th>LTP</th><th>Event</th><th>Evidence</th></tr></thead><tbody id="rows"><tr><td colspan="13" class="empty">Loading…</td></tr></tbody></table></div></div>
</div><script>
const api=location.origin;
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function stage(name,value){const on=value==='RUNNING'||value===true;return `<div class="stage"><div class="dot ${on?'ok':'bad'}">${on?'●':'○'}</div><div>${esc(name)}</div></div>`}
async function refresh(){
 const [sysRes,statusRes,rowsRes,scanRes,underRes]=await Promise.allSettled([fetch(api+'/system/status'),fetch(api+'/signals/status'),fetch(api+'/signals?limit=100'),fetch(api+'/scanner/status'),fetch(api+'/scanner/underlyings?limit=25')]);
 let sys=null,st=null,rows=[],scan=null,under=[];
 if(sysRes.status==='fulfilled'&&sysRes.value.ok){try{sys=await sysRes.value.json()}catch{}}
 if(statusRes.status==='fulfilled'&&statusRes.value.ok){try{st=await statusRes.value.json()}catch{}}
 if(rowsRes.status==='fulfilled'&&rowsRes.value.ok){try{rows=await rowsRes.value.json()}catch{}}
 if(scanRes.status==='fulfilled'&&scanRes.value.ok){try{scan=await scanRes.value.json()}catch{}}
 if(underRes.status==='fulfilled'&&underRes.value.ok){try{const x=await underRes.value.json();under=x.underlyings||[]}catch{}}
 const db=st?.database_available===true;
 document.getElementById('status').textContent=db?(st?.running?'● Monitor running':'○ Monitor stopped'):(sys?'● API online':'API unavailable');
 document.getElementById('count').textContent=st?.persisted_signals??'—';
 document.getElementById('checks').textContent=st?.checks??'—';
 document.getElementById('contracts').textContent=scan?.symbols_available_last_check??'—';
 document.getElementById('underlyings').textContent=scan?.unique_underlyings_last_check??'—';
 document.getElementById('enrichment').textContent=scan?.enrichment_requests??'—';
 document.getElementById('covavail').textContent=scan?.symbols_available_last_check??'—';
 document.getElementById('covscan').textContent=scan?.symbols_scanned_last_check??'—';
 document.getElementById('covquotes').textContent=scan?.feed_events??'—';
 document.getElementById('covready').textContent=scan?.ranking_ready?'READY':'WARMING UP';
 document.getElementById('scaninfo').textContent=scan?.last_scan_timestamp?new Date(scan.last_scan_timestamp).toLocaleTimeString():'—';
 const p=sys?.pipeline||{};
 document.getElementById('pipeline').innerHTML=[stage('Groww feed',p.feed),stage('Flow engine',p.flow),stage('Scanner',p.scanner),stage('Score engine',p.intelligence_score),stage('Dataset',p.dataset),stage('ML',p.ml),stage('Signal monitor',p.signal_monitor)].join('');
 document.getElementById('notice').textContent=db?'Live Groww feed scans a broad representative F&O universe. Active contracts are enriched with targeted quotes and option-chain data. Signals are research-only; no orders are placed.':'API is online, but PostgreSQL is unavailable for persistence.';
 const ub=document.getElementById('underlyingRows');
 if(!under.length){ub.innerHTML='<tr><td colspan="9" class="empty">Waiting for one minute of live-feed history…</td></tr>'}else{ub.innerHTML=under.map((x,i)=>{const c=x.direction==='UP'?'bull':x.direction==='DOWN'?'bear':'';const t=x.top_contracts?.[0];return `<tr><td>${i+1}</td><td><strong>${esc(x.underlying)}</strong></td><td class="score">${esc(x.activity_score)}</td><td class="${c}">${esc(x.direction)}</td><td>${esc(x.contracts_active)}</td><td>${esc(x.contracts_up)}</td><td>${esc(x.contracts_down)}</td><td>${esc(x.max_change_pct)}%</td><td>${esc(t?.symbol||'—')} ${t?.change_pct==null?'':'('+esc(t.change_pct)+'%)'}</td></tr>`}).join('')}
 const body=document.getElementById('rows');
 if(!rows.length){body.innerHTML='<tr><td colspan="13" class="empty">No persisted qualifying signals yet.</td></tr>';return}
 body.innerHTML=rows.map(x=>{const c=x.bias==='BULLISH'?'bull':x.bias==='BEARISH'?'bear':'';return `<tr><td class="muted">${esc(new Date(x.created_at).toLocaleString())}</td><td><strong>${esc(x.symbol)}</strong></td><td>${esc(x.underlying)}</td><td>${esc(x.instrument_type)}</td><td class="${c}">${esc(x.bias)}</td><td class="${c}">${esc(x.direction)}</td><td class="score">${esc(x.score)}</td><td><span class="pill">${esc(x.confidence)}</span></td><td>${x.ml_probability_up==null?'—':esc((x.ml_probability_up*100).toFixed(1)+'%')}</td><td>${x.ml_probability_down==null?'—':esc((x.ml_probability_down*100).toFixed(1)+'%')}</td><td>${esc(x.ltp)}</td><td>${esc(x.event)}</td><td class="muted">${esc((x.evidence||[]).join(', '))}</td></tr>`}).join('');
}
refresh();setInterval(refresh,10000);
</script></body></html>'''


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    return HTML
