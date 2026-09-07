from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.intelligence.fno_scanner import fno_scanner
from app.research.store import research_store
from app.services.groww_client import groww_client
from app.signals.store import signal_store
from app.strategy.slo_engine import build_results

router = APIRouter(tags=["dashboard"])
IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 40)
PREOPEN_OPEN = time(9, 0)
PREOPEN_CLOSE = time(9, 15)


def _market_session() -> dict[str, Any]:
    now = datetime.now(IST)
    weekday = now.weekday() < 5
    if not weekday:
        state = "WEEKEND"
    elif PREOPEN_OPEN <= now.time() < PREOPEN_CLOSE:
        state = "PRE_OPEN"
    elif MARKET_OPEN <= now.time() <= MARKET_CLOSE:
        state = "LIVE"
    else:
        state = "CLOSED"
    return {
        "state": state,
        "label": "LIVE" if state == "LIVE" else "PRE-OPEN" if state == "PRE_OPEN" else "MARKET CLOSED",
        "timezone": "Asia/Kolkata",
        "now_ist": now.isoformat(),
        "regular_open": "09:15",
        "regular_close": "15:40",
        "weekday": weekday,
        "data_expected": state == "LIVE",
    }


def _regime(underlyings: list[dict[str, Any]]) -> dict[str, Any]:
    items = underlyings[:10]
    if not items:
        return {"state": "WARMING UP", "confidence": 0.0, "signed_score": 0.0, "bullish": 0, "bearish": 0, "neutral": 0}
    signed = []
    bullish = bearish = neutral = 0
    for item in items:
        score = float(item.get("activity_score") or 0)
        direction = str(item.get("direction") or "FLAT").upper()
        if direction == "UP":
            signed.append(score)
            bullish += 1
        elif direction == "DOWN":
            signed.append(-score)
            bearish += 1
        else:
            signed.append(0.0)
            neutral += 1
    avg = sum(signed) / max(1, len(signed))
    confidence = min(100.0, sum(abs(x) for x in signed) / max(1, len(signed)))
    if avg >= 15:
        state = "BULLISH FLOW"
    elif avg <= -15:
        state = "BEARISH FLOW"
    else:
        state = "MIXED / NEUTRAL"
    return {
        "state": state,
        "confidence": round(confidence, 1),
        "signed_score": round(avg, 2),
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "note": "Regime is derived from the live option-flow activity ranking; it is not an index-price trend model.",
    }


def _flow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [r for r in rows if str(r.get("instrument_type") or "").upper() == "CE"]
    puts = [r for r in rows if str(r.get("instrument_type") or "").upper() == "PE"]
    def total(items: list[dict[str, Any]], key: str) -> float:
        return sum(float(r.get(key) or 0) for r in items)
    call_activity = total(calls, "activity_score")
    put_activity = total(puts, "activity_score")
    call_oi = total(calls, "open_interest")
    put_oi = total(puts, "open_interest")
    call_volume = total(calls, "volume")
    put_volume = total(puts, "volume")
    total_activity = call_activity + put_activity
    return {
        "call_contracts": len(calls),
        "put_contracts": len(puts),
        "call_activity": round(call_activity, 2),
        "put_activity": round(put_activity, 2),
        "call_activity_pct": round(call_activity / total_activity * 100, 1) if total_activity else None,
        "put_activity_pct": round(put_activity / total_activity * 100, 1) if total_activity else None,
        "call_oi": round(call_oi, 2),
        "put_oi": round(put_oi, 2),
        "call_volume": round(call_volume, 2),
        "put_volume": round(put_volume, 2),
        "pcr_oi": round(put_oi / call_oi, 3) if call_oi else None,
        "pcr_volume": round(put_volume / call_volume, 3) if call_volume else None,
        "note": "Flow totals use the currently monitored/enriched representative live-feed slice.",
    }


def _performance() -> dict[str, Any]:
    try:
        return research_store.label_summary(horizon=5)
    except Exception as exc:
        return {"available": False, "error": str(exc), "note": "Research database is unavailable."}


def _opportunities() -> dict[str, Any]:
    scan = fno_scanner.stats
    return build_results(scan.get("latest_rankings", []), scan.get("top_underlyings", []), min_score=65.0)


def _system_summary() -> dict[str, Any]:
    scan = fno_scanner.stats
    return {
        "scanner_running": scan.get("running", False),
        "ranking_ready": scan.get("ranking_ready", False),
        "feed_contracts": scan.get("symbols_available_last_check", 0),
        "contracts_with_ltp": scan.get("symbols_scanned_last_check", 0),
        "feed_events": scan.get("feed_events", 0),
        "enrichment_requests": scan.get("enrichment_requests", 0),
        "enrichment_errors": scan.get("enrichment_errors", 0),
        "last_scan_timestamp": scan.get("last_scan_timestamp"),
        "errors": scan.get("errors", 0),
    }


@router.get("/dashboard/data")
def dashboard_data() -> dict[str, Any]:
    scan = fno_scanner.stats
    opportunities = _opportunities()
    underlyings = scan.get("top_underlyings", [])[:25]
    rows = scan.get("latest_rankings", [])
    try:
        signals = signal_store.recent(50)
        signal_count = signal_store.count()
    except Exception:
        signals = []
        signal_count = None
    return {
        "market": _market_session(),
        "regime": _regime(underlyings),
        "flow": _flow(rows),
        "underlyings": underlyings,
        "opportunities": opportunities.get("results", [])[:20],
        "strategy": {
            "count": opportunities.get("count", 0),
            "method": opportunities.get("method"),
            "research_only": True,
            "trading": "DISABLED",
            "note": opportunities.get("note"),
        },
        "signals": signals,
        "signal_count": signal_count,
        "system": _system_summary(),
        "performance": _performance(),
    }


@router.get("/dashboard/performance")
def dashboard_performance() -> dict[str, Any]:
    return _performance()


@router.get("/dashboard/underlying/{underlying}")
def dashboard_underlying(underlying: str) -> dict[str, Any]:
    name = underlying.strip().upper()
    scan = fno_scanner.stats
    rows = [r for r in scan.get("latest_rankings", []) if str(r.get("underlying") or "").upper() == name]
    under = next((x for x in scan.get("top_underlyings", []) if str(x.get("underlying") or "").upper() == name), None)
    if not rows and not under:
        raise HTTPException(status_code=404, detail="Underlying is not present in the current live-feed slice")

    expiries = sorted({str(r.get("expiry_date"))[:10] for r in rows if r.get("expiry_date")})
    expiry_text = expiries[0] if expiries else None
    chain_rows: list[dict[str, Any]] = []
    chain_error = None
    if expiry_text:
        try:
            from datetime import date
            chain = groww_client.option_chain(date.fromisoformat(expiry_text), underlying=name)
            strikes = chain.get("strikes") if isinstance(chain, dict) else {}
            if isinstance(strikes, dict):
                for strike_key, strike_data in strikes.items():
                    if not isinstance(strike_data, dict):
                        continue
                    try:
                        strike = float(strike_key)
                    except (TypeError, ValueError):
                        strike = float(strike_data.get("strike_price") or 0)
                    for typ in ("CE", "PE"):
                        contract = strike_data.get(typ)
                        if not isinstance(contract, dict):
                            continue
                        greeks = contract.get("greeks") or {}
                        chain_rows.append({
                            "strike": strike,
                            "option_type": typ,
                            "symbol": contract.get("trading_symbol"),
                            "ltp": contract.get("ltp"),
                            "bid": contract.get("bid"),
                            "ask": contract.get("ask"),
                            "volume": contract.get("volume"),
                            "open_interest": contract.get("open_interest"),
                            "iv": greeks.get("iv"),
                            "delta": greeks.get("delta"),
                            "gamma": greeks.get("gamma"),
                            "theta": greeks.get("theta"),
                            "vega": greeks.get("vega"),
                        })
                chain_rows.sort(key=lambda x: (x["strike"], x["option_type"]))
        except Exception as exc:
            chain_error = str(exc)

    return {
        "underlying": name,
        "summary": under or {},
        "contracts": sorted(rows, key=lambda x: float(x.get("activity_score") or 0), reverse=True)[:30],
        "expiry": expiry_text,
        "option_chain": chain_rows[:160],
        "chain_error": chain_error,
        "research_only": True,
        "trading": "DISABLED",
    }


HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI F&O Intelligence Dashboard</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;background:#080d19;color:#e8edf7;--panel:#121a2c;--panel2:#0e1627;--border:#26334d;--muted:#8e9ab0;--green:#42d392;--red:#ff6f7d;--amber:#f4c95d;--blue:#71a7ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 0,#17243d 0,#080d19 42%);min-height:100vh}.wrap{max-width:1560px;margin:auto;padding:26px}.top{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:18px}.title{font-size:30px;font-weight:800;letter-spacing:-.02em}.sub{color:var(--muted);margin-top:5px}.nav{display:flex;gap:8px;margin-top:12px}.nav a{color:#b9c7df;text-decoration:none;border:1px solid var(--border);padding:7px 11px;border-radius:8px;background:#10182a;font-size:12px}.statusbox{display:flex;align-items:center;gap:10px}.status{padding:9px 13px;border:1px solid var(--border);border-radius:999px;font-size:13px;background:#10182a}.dotlive{color:var(--green)}.dotclosed{color:var(--amber)}.notice{padding:12px 15px;border:1px solid #3a3040;border-radius:10px;background:#171c2d;color:#bdc7d8;font-size:13px;margin-bottom:15px}.grid5{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:14px}.card,.panel,.stage{background:rgba(18,26,44,.96);border:1px solid var(--border);border-radius:14px}.card{padding:15px}.label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.value{font-size:24px;font-weight:800;margin-top:7px}.small{font-size:12px;color:var(--muted);margin-top:4px}.bull{color:var(--green)}.bear{color:var(--red)}.neutral{color:var(--muted)}.amber{color:var(--amber)}.blue{color:var(--blue)}.panel{overflow:hidden;margin-bottom:15px}.panelhead{padding:16px 19px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:10px}.paneltitle{font-weight:750;font-size:15px}.tablewrap{overflow:auto}table{width:100%;border-collapse:collapse;min-width:900px}th,td{text-align:left;padding:11px 13px;border-bottom:1px solid #202a40;font-size:12px;white-space:nowrap}th{color:var(--muted);font-weight:600;background:#0f1728;position:sticky;top:0}tbody tr:hover{background:#172139;cursor:pointer}.pill{padding:4px 7px;border-radius:6px;background:#202c44;color:#cbd5e8}.score{font-weight:800}.layout{display:grid;grid-template-columns:1.2fr .8fr;gap:15px}.regime{padding:18px}.regimeState{font-size:25px;font-weight:800;margin:4px 0 13px}.bars{display:grid;gap:10px}.barrow{display:grid;grid-template-columns:92px 1fr 45px;align-items:center;gap:10px;font-size:12px}.bar{height:8px;background:#202b40;border-radius:999px;overflow:hidden}.bar i{display:block;height:100%;background:var(--blue);border-radius:999px}.flowgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:18px}.flowbox{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:13px}.flowname{font-size:12px;color:var(--muted)}.flowpct{font-size:23px;font-weight:800;margin-top:3px}.flowbar{height:9px;background:#222d42;border-radius:99px;margin-top:9px;overflow:hidden}.flowbar i{display:block;height:100%;background:var(--green)}.flowbar.put i{background:var(--red)}.opp{min-width:1100px}.linklike{color:#a9c9ff;text-decoration:underline;text-decoration-style:dotted;cursor:pointer}.empty{padding:38px;text-align:center;color:var(--muted)}.health{display:grid;grid-template-columns:repeat(7,1fr);gap:8px;padding:15px}.stage{padding:12px;text-align:center;font-size:12px}.stageDot{font-size:16px;margin-bottom:4px}.ok{color:var(--green)}.off{color:var(--red)}.metricline{display:flex;gap:18px;flex-wrap:wrap;padding:0 18px 17px;color:#b7c2d5;font-size:12px}.metricline b{color:#eef2f8}.chainpanel{display:none}.chainpanel.open{display:block}.detail{padding:18px}.detailgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.detailcard{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:12px}.detailcard b{display:block;font-size:17px;margin-top:5px}.closebtn{border:1px solid var(--border);background:#10182a;color:#cbd5e8;border-radius:7px;padding:7px 10px;cursor:pointer}.researchNote{font-size:11px;color:var(--muted);padding:0 18px 16px}.perfgrid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;padding:16px}.perfcard{background:#0e1627;border:1px solid #22304a;border-radius:10px;padding:12px}.perfcard b{display:block;font-size:18px;margin-top:4px}@media(max-width:1200px){.grid5{grid-template-columns:repeat(3,1fr)}.layout{grid-template-columns:1fr}.health{grid-template-columns:repeat(4,1fr)}.perfgrid{grid-template-columns:repeat(3,1fr)}}@media(max-width:750px){.wrap{padding:14px}.grid5{grid-template-columns:repeat(2,1fr)}.health{grid-template-columns:repeat(2,1fr)}.perfgrid{grid-template-columns:repeat(2,1fr)}.top{display:block}.statusbox{margin-top:15px}.detailgrid{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="title">AI F&O Intelligence</div><div class="sub">Research workstation • live option-flow intelligence • trading disabled</div><div class="nav"><a href="/dashboard">Dashboard</a><a href="/strategy">SLO Strategy</a></div></div><div class="statusbox"><div id="clock" class="status">IST --:--:--</div><div id="marketStatus" class="status">Connecting…</div></div></div>
<div id="notice" class="notice">Loading market and pipeline state…</div>
<div class="grid5"><div class="card"><div class="label">Market</div><div id="marketValue" class="value">—</div><div id="marketSub" class="small">—</div></div><div class="card"><div class="label">Flow Regime</div><div id="regimeValue" class="value">—</div><div id="regimeSub" class="small">—</div></div><div class="card"><div class="label">Top Score</div><div id="topScore" class="value">—</div><div id="topScoreSub" class="small">—</div></div><div class="card"><div class="label">Opportunities</div><div id="oppCount" class="value">—</div><div id="oppSub" class="small">SLO V1 research filter</div></div><div class="card"><div class="label">Research Samples</div><div id="sampleCount" class="value">—</div><div id="sampleSub" class="small">5m labeled outcomes</div></div></div>
<div class="layout"><div class="panel"><div class="panelhead"><span class="paneltitle">Market Regime</span><span id="regimeNote" class="small"></span></div><div class="regime"><div id="regimeBig" class="regimeState">WARMING UP</div><div id="regimeConfidence" class="small">Confidence —</div><div class="bars" style="margin-top:15px"><div class="barrow"><span>Flow strength</span><div class="bar"><i id="regimeBar" style="width:0%"></i></div><span id="regimePct">0</span></div><div class="barrow"><span>Bullish</span><div class="bar"><i id="bullBar" style="width:0%"></i></div><span id="bullPct">0</span></div><div class="barrow"><span>Bearish</span><div class="bar"><i id="bearBar" style="width:0%"></i></div><span id="bearPct">0</span></div></div></div></div><div class="panel"><div class="panelhead"><span class="paneltitle">CE vs PE Flow</span><span class="small">current feed slice</span></div><div class="flowgrid"><div class="flowbox"><div class="flowname">CALL ACTIVITY</div><div id="callPct" class="flowpct bull">—</div><div class="flowbar"><i id="callBar" style="width:0%"></i></div><div id="callMeta" class="small">—</div></div><div class="flowbox"><div class="flowname">PUT ACTIVITY</div><div id="putPct" class="flowpct bear">—</div><div class="flowbar put"><i id="putBar" style="width:0%"></i></div><div id="putMeta" class="small">—</div></div><div class="flowbox"><div class="flowname">PCR OI</div><div id="pcrOi" class="flowpct">—</div><div class="small">Put OI / Call OI</div></div><div class="flowbox"><div class="flowname">PCR VOLUME</div><div id="pcrVol" class="flowpct">—</div><div class="small">Put volume / Call volume</div></div></div><div class="researchNote">Flow metrics are derived from the representative contracts currently monitored and enriched; they are not exchange-wide participant flow.</div></div></div>
<div class="panel"><div class="panelhead"><span class="paneltitle">🔥 Top Research Opportunities</span><span class="small">Click a row for evidence</span></div><div class="tablewrap"><table class="opp"><thead><tr><th>#</th><th>Underlying</th><th>Option</th><th>Direction</th><th>SLO</th><th>Premium</th><th>Stop</th><th>Target</th><th>Breakeven</th><th>DTE</th><th>Delta</th><th>Theta</th><th>IV</th><th>Volume</th><th>OI</th></tr></thead><tbody id="oppRows"><tr><td colspan="15" class="empty">Waiting for live rankings…</td></tr></tbody></table></div><div id="opportunityDetail" class="detail" style="display:none"></div></div>
<div class="panel"><div class="panelhead"><span class="paneltitle">Top Active Underlyings</span><span class="small">Click an underlying for option-chain drill-down</span></div><div class="tablewrap"><table><thead><tr><th>#</th><th>Underlying</th><th>Flow Score</th><th>Direction</th><th>Active</th><th>Up</th><th>Down</th><th>Max 1m</th><th>Top Contract</th><th>OI</th><th>Volume</th><th>PCR OI</th></tr></thead><tbody id="underRows"><tr><td colspan="12" class="empty">Waiting for one minute of live-feed history…</td></tr></tbody></table></div></div>
<div class="panel"><div class="panelhead"><span class="paneltitle">Signal Timeline</span><span class="small">Persisted qualifying research signals</span></div><div class="tablewrap"><table><thead><tr><th>Time</th><th>Symbol</th><th>Underlying</th><th>Bias</th><th>Direction</th><th>Score</th><th>Confidence</th><th>LTP</th><th>Event</th><th>Evidence</th></tr></thead><tbody id="signalRows"><tr><td colspan="10" class="empty">No persisted qualifying signals yet.</td></tr></tbody></table></div></div>
<div class="panel"><div class="panelhead"><span class="paneltitle">5-minute Dataset Outcomes</span><span class="small">Research labels, not strategy P&amp;L</span></div><div id="perfGrid" class="perfgrid"></div><div class="researchNote">These metrics describe labeled dataset outcomes used for research/model training. They should not be interpreted as live-trading performance or a validated edge.</div></div>
<div class="panel"><div class="panelhead"><span class="paneltitle">System Health</span><span id="healthMeta" class="small">—</span></div><div id="health" class="health"></div><div class="metricline"><span>Feed contracts <b id="feedContracts">—</b></span><span>Contracts with LTP <b id="ltpContracts">—</b></span><span>Feed events <b id="feedEvents">—</b></span><span>Enrichment <b id="enrich">—</b></span><span>Last scan <b id="lastScan">—</b></span><span>Errors <b id="errors">—</b></span></div></div>
<div id="chainPanel" class="panel chainpanel"><div class="panelhead"><span class="paneltitle" id="chainTitle">Underlying Detail</span><button class="closebtn" onclick="closeChain()">Close</button></div><div id="chainDetail" class="detail"></div></div>
</div><script>
const api=location.origin;let latest=null;
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function fmt(v,d=2){if(v===null||v===undefined||v==='')return '—';const n=Number(v);return Number.isFinite(n)?n.toFixed(d):esc(v)}
function pct(v){return v===null||v===undefined?'—':fmt(v,1)+'%'}
function cls(v){return v==='BULLISH'||v==='UP'||v==='BUY_CALL'?'bull':v==='BEARISH'||v==='DOWN'||v==='BUY_PUT'?'bear':'neutral'}
function healthStage(name,on){return `<div class="stage"><div class="stageDot ${on?'ok':'off'}">${on?'●':'○'}</div><div>${esc(name)}</div></div>`}
function renderOpportunityDetail(x){const d=document.getElementById('opportunityDetail');d.style.display='block';d.innerHTML=`<div class="panelhead" style="padding:0 0 12px;border:0"><span class="paneltitle">${esc(x.symbol||'Opportunity')} — Why this score?</span><button class="closebtn" onclick="document.getElementById('opportunityDetail').style.display='none'">Close</button></div><div class="detailgrid"><div class="detailcard"><span class="label">Direction</span><b class="${cls(x.direction)}">${esc(x.direction)}</b></div><div class="detailcard"><span class="label">SLO score</span><b>${fmt(x.total_score)}</b></div><div class="detailcard"><span class="label">Signal state</span><b>${x.total_score>=80?'STRONG RESEARCH SETUP':x.total_score>=65?'WATCH':'WAIT'}</b></div><div class="detailcard"><span class="label">Research status</span><b class="amber">NO ORDERS</b></div></div><div class="bars" style="margin-top:14px"><div class="barrow"><span>Direction 45%</span><div class="bar"><i style="width:${Math.min(100,Math.abs(Number(x.direction_score)||0))}%"></i></div><span>${fmt(Math.abs(x.direction_score||0))}</span></div><div class="barrow"><span>Liquidity 20%</span><div class="bar"><i style="width:${Math.min(100,Number(x.liquidity_score)||0)}%"></i></div><span>${fmt(x.liquidity_score)}</span></div><div class="barrow"><span>Theta 20%</span><div class="bar"><i style="width:${Math.min(100,Number(x.theta_score)||0)}%"></i></div><span>${fmt(x.theta_score)}</span></div><div class="barrow"><span>Volatility 15%</span><div class="bar"><i style="width:${Math.min(100,Number(x.volatility_score)||0)}%"></i></div><span>${fmt(x.volatility_score)}</span></div></div><div class="small" style="margin-top:13px">${esc(x.reason||'Flow direction and SLO candidate scoring agree.')}</div>`;d.scrollIntoView({behavior:'smooth',block:'center'})}
function renderChain(data){const panel=document.getElementById('chainPanel');panel.classList.add('open');document.getElementById('chainTitle').textContent=`${data.underlying} — Option Chain ${data.expiry||''}`;const s=data.summary||{};const rows=data.option_chain||[];const contracts=data.contracts||[];let html=`<div class="detailgrid"><div class="detailcard"><span class="label">Flow score</span><b>${fmt(s.activity_score)}</b></div><div class="detailcard"><span class="label">Direction</span><b class="${cls(s.direction==='UP'?'UP':s.direction==='DOWN'?'DOWN':'FLAT')}">${esc(s.direction||'—')}</b></div><div class="detailcard"><span class="label">Active contracts</span><b>${esc(s.contracts_active||0)}</b></div><div class="detailcard"><span class="label">Max 1m move</span><b>${fmt(s.max_change_pct,3)}%</b></div></div><h3 style="margin:20px 0 10px">Enriched Contracts</h3><div class="tablewrap"><table><thead><tr><th>Symbol</th><th>Type</th><th>Strike</th><th>LTP</th><th>1m</th><th>Score</th><th>OI</th><th>Volume</th></tr></thead><tbody>${contracts.slice(0,15).map(r=>`<tr><td>${esc(r.symbol)}</td><td class="${cls(r.direction)}">${esc(r.instrument_type)}</td><td>${fmt(r.strike_price)}</td><td>${fmt(r.ltp)}</td><td>${fmt(r.change_pct_since_last_minute,3)}%</td><td>${fmt(r.activity_score)}</td><td>${fmt(r.open_interest,0)}</td><td>${fmt(r.volume,0)}</td></tr>`).join('')||'<tr><td colspan="8" class="empty">No enriched contracts.</td></tr>'}</tbody></table></div><h3 style="margin:20px 0 10px">Option Chain</h3><div class="tablewrap"><table><thead><tr><th>Strike</th><th>Call LTP</th><th>Call OI</th><th>Call Vol</th><th>Call IV</th><th>Put LTP</th><th>Put OI</th><th>Put Vol</th><th>Put IV</th></tr></thead><tbody>`;const byStrike={};rows.forEach(r=>{byStrike[r.strike]=byStrike[r.strike]||{};byStrike[r.strike][r.option_type]=r});html+=Object.keys(byStrike).sort((a,b)=>Number(a)-Number(b)).map(k=>{const ce=byStrike[k].CE||{},pe=byStrike[k].PE||{};return `<tr><td><strong>${fmt(k,0)}</strong></td><td>${fmt(ce.ltp)}</td><td>${fmt(ce.open_interest,0)}</td><td>${fmt(ce.volume,0)}</td><td>${fmt(ce.iv,2)}</td><td>${fmt(pe.ltp)}</td><td>${fmt(pe.open_interest,0)}</td><td>${fmt(pe.volume,0)}</td><td>${fmt(pe.iv,2)}</td></tr>`}).join('')||'<tr><td colspan="9" class="empty">Option-chain data unavailable.</td></tr>';html+=`</tbody></table></div>${data.chain_error?`<div class="small" style="margin-top:10px">Chain enrichment error: ${esc(data.chain_error)}</div>`:''}<div class="researchNote" style="padding:14px 0 0">This drill-down uses Groww option-chain data for the selected underlying/expiry. It is research-only.</div>`;document.getElementById('chainDetail').innerHTML=html;panel.scrollIntoView({behavior:'smooth',block:'start'})}
function closeChain(){document.getElementById('chainPanel').classList.remove('open')}
async function openUnderlying(name){try{const r=await fetch(api+'/dashboard/underlying/'+encodeURIComponent(name));if(!r.ok)throw new Error(await r.text());renderChain(await r.json())}catch(e){alert('Unable to load '+name+': '+e.message)}}
function render(data,sys){latest=data;const m=data.market||{};const r=data.regime||{};const f=data.flow||{};const opp=data.opportunities||[];const under=data.underlyings||[];const signals=data.signals||[];const perf=data.performance||{};document.getElementById('clock').textContent='IST '+new Date(m.now_ist||Date.now()).toLocaleTimeString('en-IN');document.getElementById('marketStatus').innerHTML=`<span class="${m.state==='LIVE'?'dotlive':'dotclosed'}">●</span> ${esc(m.label)}`;document.getElementById('marketValue').textContent=m.state==='LIVE'?'LIVE':m.state==='PRE_OPEN'?'PRE-OPEN':'CLOSED';document.getElementById('marketSub').textContent=`NSE F&O ${m.regular_open}–${m.regular_close} IST`;document.getElementById('regimeValue').textContent=r.state||'WARMING UP';document.getElementById('regimeValue').className='value '+(r.state?.includes('BULL')?'bull':r.state?.includes('BEAR')?'bear':'neutral');document.getElementById('regimeSub').textContent=`Confidence ${fmt(r.confidence,1)}%`;document.getElementById('topScore').textContent=opp.length?fmt(opp[0].total_score):'—';document.getElementById('topScoreSub').textContent=opp.length?`${opp[0].underlying} ${opp[0].symbol}`:'No qualifying candidate';document.getElementById('oppCount').textContent=opp.length;document.getElementById('sampleCount').textContent=perf.samples??'—';document.getElementById('sampleSub').textContent=perf.samples?`Positive ${fmt(perf.positive_rate,1)}%`: '5m labeled outcomes';document.getElementById('notice').textContent=m.state==='LIVE'?`Live market session. Feed coverage is ${data.system.feed_contracts||0} contracts with targeted enrichment. Signals are research-only; no orders are placed.`:m.state==='PRE_OPEN'?'NSE F&O is in the pre-open window. Continuous live-flow ranking starts after 09:15 IST.':'NSE F&O regular equity-derivatives trading is closed. The system can remain healthy while live-feed counters stay unchanged; last session data is retained for research.';document.getElementById('regimeBig').textContent=r.state||'WARMING UP';document.getElementById('regimeBig').className='regimeState '+(r.state?.includes('BULL')?'bull':r.state?.includes('BEAR')?'bear':'neutral');document.getElementById('regimeConfidence').textContent=`Confidence ${fmt(r.confidence,1)}% • ${r.bullish||0} bullish / ${r.bearish||0} bearish / ${r.neutral||0} neutral`;document.getElementById('regimeBar').style.width=Math.min(100,Math.abs(Number(r.signed_score)||0))+'%';const total=(r.bullish||0)+(r.bearish||0)+(r.neutral||0)||1;document.getElementById('bullBar').style.width=((r.bullish||0)/total*100)+'%';document.getElementById('bearBar').style.width=((r.bearish||0)/total*100)+'%';document.getElementById('regimePct').textContent=fmt(Math.abs(r.signed_score)||0);document.getElementById('bullPct').textContent=r.bullish||0;document.getElementById('bearPct').textContent=r.bearish||0;document.getElementById('regimeNote').textContent=r.note||'';document.getElementById('callPct').textContent=pct(f.call_activity_pct);document.getElementById('putPct').textContent=pct(f.put_activity_pct);document.getElementById('callBar').style.width=(f.call_activity_pct||0)+'%';document.getElementById('putBar').style.width=(f.put_activity_pct||0)+'%';document.getElementById('callMeta').textContent=`${f.call_contracts||0} contracts • OI ${fmt(f.call_oi,0)} • Vol ${fmt(f.call_volume,0)}`;document.getElementById('putMeta').textContent=`${f.put_contracts||0} contracts • OI ${fmt(f.put_oi,0)} • Vol ${fmt(f.put_volume,0)}`;document.getElementById('pcrOi').textContent=fmt(f.pcr_oi,3);document.getElementById('pcrVol').textContent=fmt(f.pcr_volume,3);
const ob=document.getElementById('oppRows');ob.innerHTML=opp.length?opp.map((x,i)=>`<tr onclick='renderOpportunityDetail(${JSON.stringify(x).replace(/'/g,"&#39;")})'><td>${i+1}</td><td><strong>${esc(x.underlying)}</strong></td><td>${esc(x.symbol)}</td><td class="${cls(x.direction)}">${esc(x.direction)}</td><td class="score">${fmt(x.total_score)}</td><td>${fmt(x.premium)}</td><td>${fmt(x.stop_premium)}</td><td>${fmt(x.target_premium)}</td><td>${fmt(x.breakeven)}</td><td>${esc(x.dte)}</td><td>${fmt(x.delta,3)}</td><td>${fmt(x.theta,3)}</td><td>${x.iv==null?'—':fmt(x.iv*100,2)+'%'}</td><td>${fmt(x.volume,0)}</td><td>${fmt(x.open_interest,0)}</td></tr>`).join(''):'<tr><td colspan="15" class="empty">No qualifying SLO candidates yet. The system will remain in WAIT until the research threshold and liquidity conditions are met.</td></tr>';
const ub=document.getElementById('underRows');ub.innerHTML=under.length?under.map((x,i)=>{const t=x.top_contracts?.[0]||{};const oi=(x.top_contracts||[]).reduce((a,r)=>a+Number(r.open_interest||0),0);const vol=(x.top_contracts||[]).reduce((a,r)=>a+Number(r.volume||0),0);const ce=(x.top_contracts||[]).filter(r=>String(r.instrument_type).toUpperCase()==='CE').reduce((a,r)=>a+Number(r.open_interest||0),0);const pe=(x.top_contracts||[]).filter(r=>String(r.instrument_type).toUpperCase()==='PE').reduce((a,r)=>a+Number(r.open_interest||0),0);return `<tr onclick="openUnderlying('${esc(x.underlying).replace(/'/g,"\\'")}')"><td>${i+1}</td><td><span class="linklike"><strong>${esc(x.underlying)}</strong></span></td><td class="score">${fmt(x.activity_score)}</td><td class="${x.direction==='UP'?'bull':x.direction==='DOWN'?'bear':'neutral'}">${esc(x.direction)}</td><td>${esc(x.contracts_active)}</td><td>${esc(x.contracts_up)}</td><td>${esc(x.contracts_down)}</td><td>${fmt(x.max_change_pct,3)}%</td><td>${esc(t.symbol||'—')}</td><td>${fmt(oi,0)}</td><td>${fmt(vol,0)}</td><td>${pe&&ce?fmt(pe/ce,2):'—'}</td></tr>`}).join(''):'<tr><td colspan="12" class="empty">Waiting for one minute of live-feed history…</td></tr>';
const sb=document.getElementById('signalRows');sb.innerHTML=signals.length?signals.map(x=>`<tr><td class="small">${esc(new Date(x.created_at).toLocaleString('en-IN'))}</td><td><strong>${esc(x.symbol)}</strong></td><td>${esc(x.underlying)}</td><td class="${cls(x.bias)}">${esc(x.bias)}</td><td class="${cls(x.direction)}">${esc(x.direction)}</td><td class="score">${fmt(x.score)}</td><td><span class="pill">${esc(x.confidence)}</span></td><td>${fmt(x.ltp)}</td><td>${esc(x.event)}</td><td class="small">${esc((x.evidence||[]).join(', '))}</td></tr>`).join(''):'<tr><td colspan="10" class="empty">No persisted qualifying signals yet.</td></tr>';
const pg=document.getElementById('perfGrid');if(perf.samples){pg.innerHTML=[['Samples',perf.samples],['Positive',perf.positive],['Negative',perf.negative],['Positive rate',pct(perf.positive_rate)],['Avg return',perf.avg_return_pct==null?'—':fmt(perf.avg_return_pct,3)+'%'],['Best / Worst',`${fmt(perf.best_return_pct,3)}% / ${fmt(perf.worst_return_pct,3)}%`]].map(x=>`<div class="perfcard"><span class="label">${x[0]}</span><b>${x[1]}</b></div>`).join('')}else pg.innerHTML='<div class="empty">No labeled outcomes yet.</div>';
const s=data.system||{};document.getElementById('health').innerHTML=[healthStage('Scanner',s.scanner_running),healthStage('Ranking',s.ranking_ready),healthStage('Database',data.signal_count!==null),healthStage('Research',true),healthStage('Trading',false),healthStage('Strategy',true),healthStage('Feed',m.state!=='LIVE'?true:s.feed_contracts>0)].join('');document.getElementById('healthMeta').textContent=`Signals persisted: ${data.signal_count??'—'} • Strategy: ${data.strategy?.method||'—'}`;document.getElementById('feedContracts').textContent=s.feed_contracts??'—';document.getElementById('ltpContracts').textContent=s.contracts_with_ltp??'—';document.getElementById('feedEvents').textContent=s.feed_events??'—';document.getElementById('enrich').textContent=s.enrichment_requests??'—';document.getElementById('lastScan').textContent=s.last_scan_timestamp?new Date(s.last_scan_timestamp).toLocaleTimeString('en-IN'):'—';document.getElementById('errors').textContent=s.errors??'—';}
async function refresh(){try{const [d,s]=await Promise.all([fetch(api+'/dashboard/data'),fetch(api+'/system/status')]);if(!d.ok)throw new Error(await d.text());const data=await d.json();let sys={};try{sys=await s.json()}catch{};render(data,sys)}catch(e){document.getElementById('notice').textContent='Dashboard data unavailable: '+e.message}}
refresh();setInterval(refresh,10000);
</script></body></html>'''


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> str:
    return HTML
