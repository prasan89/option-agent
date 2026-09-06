from fastapi import FastAPI

from app.api.groww import router as groww_router
from app.core.config import settings

app = FastAPI(
    title="AI Options Flow Agent API",
    version=settings.app_version,
    description="AI-assisted options-flow research platform. Phase 1 is read-only market data.",
)
app.include_router(groww_router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/version", tags=["system"])
def version() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }


@app.get("/system/status", tags=["system"])
def system_status() -> dict[str, object]:
    from app.services.groww_feed import feed_service
    from app.services.groww_client import groww_client

    return {
        "status": "UP",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "market_data": "CONNECTED" if groww_client.configured else "NOT_CONFIGURED",
        "groww_feed": "RUNNING" if feed_service.running else "STOPPED",
        "flow_engine": "NOT_STARTED",
        "ai_agent": "NOT_STARTED",
        "trading": "DISABLED",
    }
