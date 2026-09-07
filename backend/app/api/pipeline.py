from typing import Any

from fastapi import APIRouter, HTTPException

from app.pipeline import research_pipeline

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/status")
def status() -> dict[str, Any]:
    return research_pipeline.stats


@router.post("/start")
def start() -> dict[str, Any]:
    try:
        return {"status": "STARTED", **research_pipeline.start()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/stop")
def stop() -> dict[str, Any]:
    return {"status": "STOPPED", **research_pipeline.stop()}
