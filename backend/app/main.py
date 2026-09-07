from fastapi import FastAPI

from app.api.agent import router as agent_router
from app.api.dashboard import router as dashboard_router
from app.api.dataset import router as dataset_router
from app.api.flow import router as flow_router
from app.api.groww import router as groww_router
from app.api.intelligence import router as intelligence_router
from app.api.intelligence_score import router as intelligence_score_router
from app.api.ml import router as ml_router
from app.api.scanner import router as scanner_router
from app.api.signals import router as signals_router
from app.core.config import settings
from app.signals.monitor import signal_monitor

app = FastAPI(title="AI Options Flow Agent API", version=settings.app_version, description="AI-assisted quantitative options-flow analysis platform.")
app.include_router(groww_router)
app.include_router(flow_router)
app.include_router(intelligence_router)
app.include_router(intelligence_score_router)
app.include_router(scanner_router)
app.include_router(dataset_router)
app.include_router(ml_router)
app.include_router(signals_router)
app.include_router(dashboard_router)
app.include_router(agent_router)


@app.on_event("startup")
def startup() -> None:
    try:
        signal_monitor.start()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Signal monitor could not start")


@app.on_event("shutdown")
def shutdown() -> None:
    signal_monitor.stop()


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
    from app.dataset.collector import historical_dataset_collector
    from app.ml.engine import ml_engine
    return {
        "status": "UP", "service": settings.app_name, "version": settings.app_version,
        "environment": settings.environment,
        "market_data": "CONFIGURED" if groww_client.configured else "NOT_CONFIGURED",
        "groww_feed": "RUNNING" if feed_service.running else "STOPPED",
        "flow_engine": "RUNNING" if flow_engine.running else "STOPPED",
        "flow_intelligence": "READY",
        "fno_scanner": "RUNNING" if fno_scanner.running else "STOPPED",
        "intelligence_score_engine": "RUNNING" if intelligence_score_engine.running else "STOPPED",
        "historical_dataset": "RUNNING" if historical_dataset_collector.running else "STOPPED",
        "ml_engine": "RUNNING" if ml_engine.running else "STOPPED",
        "model_ready": ml_engine.stats["model_ready"],
        "signal_monitor": "RUNNING" if signal_monitor.running else "STOPPED",
        "persisted_signals": signal_monitor.stats["persisted_signals"],
        "ai_agent": "READY", "trading": "DISABLED",
    }
}
