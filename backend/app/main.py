from fastapi import FastAPI

from app.core.config import settings

app = FastAPI(
    title="AI Options Flow Agent API",
    version=settings.app_version,
    description="Backend foundation for the AI Options Flow Agent.",
)


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
    return {
        "status": "UP",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "market_data": "NOT_CONNECTED",
        "flow_engine": "NOT_STARTED",
        "ai_agent": "NOT_STARTED",
        "trading": "DISABLED",
    }
