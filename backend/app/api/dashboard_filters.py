from __future__ import annotations

from fastapi import Request
from fastapi.responses import Response

FILTER_PATHS = {
    "/dashboard",
    "/strategy",
    "/dashboard/history",
    "/price-action",
    "/price-action/history",
}

FILTER_SCRIPT = r'''<style id="dashboard-filter-style">
.dashboard-filter-panel{margin:0 0 15px;padding:14px 16px;background:#10182a;border:1px solid #26334d;border-radius:12px;display:flex;gap:9px;align-items:center;flex-wrap:wrap}.dashboard-filter-panel .df-title{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#8e9ab0;font-weight:700;margin-right:4px}.dashboard-filter-panel input,.dashboard-filter-panel select{background:#0b1323;color:#e8edf7;border:1px solid #2b3a57;border-radius:7px;padding:8px 9px;font-size:12px;min-width:125px;outline:none}.dashboard-filter-panel input:focus,.dashboard-filter-panel select:focus{border-color:#52678f}.dashboard-filter-panel .df-search{min-width:190px}.dashboard-filter-panel .df-number{min-width:90px;width:90px}.dashboard-filter-panel button{background:#17233b;color:#d9e2f2;border:1px solid #3a4b6d;border-radius:7px;padding:8px 11px;font-size:12px;cursor:pointer}.dashboard-filter-panel button:hover{border-color:#6b82ad}.dashboard-filter-panel .df-count{color:#8e9ab0;font-size:11px;margin-left:auto}@media(max-width:700px){.dashboard-filter-panel .df-search{min-width:150px;width:100%}.dashboard-filter-panel .df-count{width:100%;margin-left:0}}
</style>
<div id="dashboardFilterPanel" class="dashboard-filter-panel">
<span class="df-title">Filters</span>
<input id="dfSearch" class="df-search" type="search" placeholder="Search symbol / underlying…">
<select id="dfUnderlying"><option value="">All underlyings</option></select>
<select id="dfDirection"><option value="">All directions</option></select>
<select id="dfSignal"><option value="">All signals</option></select>
<select id="dfStatus"><option value="">All statuses</option></select>
<select id="dfPattern"><option value="">All patterns</option></select>
<select id="dfOption"><option value="">All options</option></select>
<input id="dfMinScore" class="df-number" type="number" min="0" max="100" step="1" placeholder="Min score">
<input id="dfMaxDte" class="df-number" type="number" min="0" max="3650" step="1" placeholder="Max DTE">
<button id="dfClear" type="button">Clear</button><span id="dfCount" class="df-count">0 rows</span>
</div>
<script id="dashboard-filter-script">
(function(){
const path=location.pathname.replace(/\/$/,'')||'/dashboard';
const configs={
'/dashboard':{tbody:'oppRows',fields:['underlying','direction','option','minScore','maxDte']},
'/strategy':{tbody:'rows',fields:['underlying','direction','option','minScore','maxDte']},
'/dashboard/history':{tbody:'rows',fields:['underlying','direction','status','minScore']},
'/price-action':{tbody:'rows',fields:['underlying','signal','pattern','minScore']},
'/price-action/history':{tbody:'rows',fields:['underlying','signal','status','pattern','minScore']}
};
const cfg=configs[path];if(!cfg)return;
const panel=document.getElementById('dashboardFilterPanel');if(!panel)return;
const controls={search:document.getElementById('dfSearch'),underlying:document.getElementById('dfUnderlying'),direction:document.getElementById('dfDirection'),signal:document.getElementById('dfSignal'),status:document.getElementById('dfStatus'),pattern:document.getElementById('dfPattern'),option:document.getElementById('dfOption'),minScore:document.getElementById('dfMinScore'),maxDte:document.getElementById('dfMaxDte'),clear:document.getElementById('dfClear'),count:document.getElementById('dfCount')};
const table=document.getElementById(cfg.tbody)?.closest('table');if(!table){panel.remove();return;}
const headers=Array.from(table.querySelectorAll('thead th')).map(x=>x.textContent.trim().toLowerCase());
const indexFor=(names)=>{for(const n of names){const i=headers.findIndex(h=>h===n||h.includes(n));if(i>=0)return i;}return -1;};
const idx={underlying:indexFor(['underlying']),direction:indexFor(['direction']),signal:indexFor(['signal']),status:indexFor(['status']),pattern:indexFor(['pattern']),option:indexFor(['option','symbol']),score:indexFor(['slo score','score']),dte:indexFor(['dte'])};
function rows(){return Array.from(document.getElementById(cfg.tbody)?.querySelectorAll('tr')||[]).filter(r=>r.cells.length>1);}
function cell(row,key){const i=idx[key];return i>=0&&row.cells[i]?row.cells[i].textContent.trim():'';}
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function values(key){return [...new Set(rows().map(r=>cell(r,key)).filter(Boolean))].sort((a,b)=>a.localeCompare(b));}
function fillSelect(el,key,placeholder){if(!el)return;const current=el.value;const vals=values(key);el.innerHTML='<option value="">'+placeholder+'</option>'+vals.map(v=>'<option value="'+esc(v)+'">'+esc(v)+'</option>').join('');if(vals.includes(current))el.value=current;}
function matches(row){
const search=(controls.search.value||'').trim().toLowerCase();if(search&&!row.textContent.toLowerCase().includes(search))return false;
for(const key of ['underlying','direction','signal','status','pattern','option']){if(!cfg.fields.includes(key)||!controls[key])continue;const wanted=controls[key].value;if(wanted&&!cell(row,key).toLowerCase().includes(wanted.toLowerCase()))return false;}
const min=Number(controls.minScore?.value||'');if(cfg.fields.includes('minScore')&&Number.isFinite(min)&&controls.minScore.value!==''){const score=parseFloat(cell(row,'score').replace(/[^0-9.-]/g,''));if(!Number.isFinite(score)||score<min)return false;}
const maxDte=Number(controls.maxDte?.value||'');if(cfg.fields.includes('maxDte')&&Number.isFinite(maxDte)&&controls.maxDte.value!==''){const dte=parseFloat(cell(row,'dte').replace(/[^0-9.-]/g,''));if(!Number.isFinite(dte)||dte>maxDte)return false;}
return true;
}
function apply(){const all=rows();let shown=0;all.forEach(r=>{const ok=matches(r);r.style.display=ok?'':'none';if(ok)shown++;});if(controls.count)controls.count.textContent=shown+' / '+all.length+' rows';}
function refreshOptions(){fillSelect(controls.underlying,'underlying','All underlyings');fillSelect(controls.direction,'direction','All directions');fillSelect(controls.signal,'signal','All signals');fillSelect(controls.status,'status','All statuses');fillSelect(controls.pattern,'pattern','All patterns');fillSelect(controls.option,'option','All options');apply();}
function clear(){controls.search.value='';['underlying','direction','signal','status','pattern','option'].forEach(k=>{if(controls[k])controls[k].value='';});controls.minScore.value='';controls.maxDte.value='';apply();}
['search','underlying','direction','signal','status','pattern','option','minScore','maxDte'].forEach(k=>{if(controls[k])controls[k].addEventListener(k==='search'?'input':'change',apply);});
controls.clear.addEventListener('click',clear);refreshOptions();setInterval(refreshOptions,1000);
})();
</script>'''


def inject_filter_ui(html: str, path: str) -> str:
    normalized = path.rstrip("/") or "/dashboard"
    if normalized not in FILTER_PATHS or 'id="dashboardFilterPanel"' in html:
        return html
    marker = '<div id="notice"'
    if marker in html:
        return html.replace(marker, FILTER_SCRIPT + marker, 1)
    return html.replace("<body>", "<body>" + FILTER_SCRIPT, 1)


async def dashboard_filter_middleware(request: Request, call_next) -> Response:
    response = await call_next(request)
    if request.url.path.rstrip("/") not in FILTER_PATHS:
        return response
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type or not hasattr(response, "body_iterator"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    html = body.decode("utf-8", errors="replace")
    html = inject_filter_ui(html, request.url.path)
    headers = dict(response.headers)
    headers.pop("content-length", None)
    headers.pop("content-encoding", None)
    return Response(content=html, status_code=response.status_code, headers=headers, media_type="text/html")
