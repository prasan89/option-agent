import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.api.agent import router as agent_router
from app.api.dashboard import router as dashboard_router
from app.api.dashboard_fixed import router as dashboard_fixed_router
from app.api.dashboard_shell import router as dashboard_shell_router
from app.api.dashboard_filters import dashboard_filter_middleware
from app.api.dataset import router as dataset_router
from app.api.flow import router as flow_router
from app.api.fno_engine import router as fno_engine_router
from app.api.groww import router as groww_router
from app.api.intelligence import router as intelligence_router
from app.api.intelligence_score import router as intelligence_score_router
from app.api.jft import router as jft_router
from app.api.ml import router as ml_router
from app.api.paper_tracker import router as paper_tracker_router
from app.api.pipeline import router as pipeline_router
from app.api.price_action import router as price_action_router
from app.api.price_action_history import router as price_action_history_router
from app.api.price_action_paper import router as price_action_paper_router
from app.api.reversal import router as reversal_router
from app.api.scanner import router as scanner_router
from app.api.signals import router as signals_router
from app.api.slo_history import router as slo_history_router
from app.api.strategy import router as strategy_router
from app.api.strategy_dashboard import router as strategy_dashboard_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.migrations import run_migrations
from app.intelligence.historical_session import historical_session_analyzer
from app.jft.scanner import jft_scanner
from app.pipeline import research_pipeline
from app.price_action.scanner import price_action_scanner
from app.signals.monitor import signal_monitor
from app.signals.paper_tracker import paper_signal_tracker
from app.signals.price_action_paper_tracker import price_action_paper_tracker

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Options Flow Agent API", version=settings.app_version, description="AI-assisted quantitative options-flow analysis platform.")
app.middleware("http")(dashboard_filter_middleware)


@app.middleware("http")
async def reversal_navigation(request: Request, call_next):
    """Add dedicated research tabs to the existing dashboard navigation."""
    response = await call_next(request)
    if request.url.path not in {"/dashboard", "/strategy", "/dashboard/history", "/price-action", "/price-action/history", "/jft", "/reversal", "/reversal/history", "/paper-tracker", "/price-action/paper-pnl"}:
        return response
    content_type = str(response.headers.get("content-type", ""))
    if "text/html" not in content_type or not hasattr(response, "body_iterator"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError:
        return response
    if "/paper-tracker" not in html and 'href="/jft"' in html:
        links = '<a href="/reversal">Reversal</a><a href="/reversal/history">Reversal History</a><a href="/paper-tracker">Paper P&L Tracker</a>'
        active = '<a href="/jft" class="active">JFT Signals</a>'
        plain = '<a href="/jft">JFT Signals</a>'
        if active in html:
            html = html.replace(active, f'{plain}{links}', 1)
        elif plain in html:
            html = html.replace(plain, f'{plain}{links}', 1)
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(content=html, status_code=response.status_code, headers=headers, media_type="text/html")


app.include_router(groww_router)
app.include_router(flow_router)
app.include_router(intelligence_router)
app.include_router(intelligence_score_router)
app.include_router(scanner_router)
app.include_router(fno_engine_router)
app.include_router(dataset_router)
app.include_router(ml_router)
app.include_router(signals_router)
app.include_router(strategy_router)
app.include_router(pipeline_router)
app.include_router(dashboard_fixed_router)
app.include_router(dashboard_shell_router)
app.include_router(dashboard_router)
app.include_router(strategy_dashboard_router)
app.include_router(price_action_router)
app.include_router(price_action_history_router)
app.include_router(price_action_paper_router)
app.include_router(slo_history_router)
app.include_router(jft_router)
app.include_router(reversal_router)
app.include_router(paper_tracker_router)
app.include_router(agent_router)


@app.on_event("startup")
def startup() -> None:
    configure_logging()
    logger.info("Option Agent API starting: version=%s environment=%s", settings.app_version, settings.environment)
    run_migrations()
    try:
        paper_signal_tracker.init()
        price_action_paper_tracker.init()
    except Exception:
        logger.exception("Paper signal tracker initialization failed")
    research_pipeline.startup()


@app.on_event("shutdown")
def shutdown() -> None:
    logger.info("Option Agent API shutting down")
    research_pipeline.stop()
    signal_monitor.stop()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API exception: method=%s path=%s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/version", tags=["system"])
def version() -> dict[str, str]:
    return {"service": settings.app_name, "version": settings.app_version, "environment": settings.environment}


@app.get("/system/status", tags=["system"])
def system_status() -> dict[str, object]:
    from app.services.groww_client import groww_client
    from app.services.groww_feed import feed_service
    from app.flow.engine import flow_engine
    from app.intelligence.fno_scanner import fno_scanner
    from app.intelligence.score_engine import intelligence_score_engine
    return {
        "status": "UP",
        "version": settings.app_version,
        "environment": settings.environment,
        "market_data": {"configured": groww_client.configured},
        "groww_feed": feed_service.stats,
        "flow_engine": flow_engine.stats,
        "fno_scanner": fno_scanner.stats,
        "historical_session": historical_session_analyzer.stats,
        "price_action_scanner": price_action_scanner.stats,
        "jft_scanner": jft_scanner.stats,
        "intelligence_score": intelligence_score_engine.stats,
        "trading": "DISABLED",
    }
