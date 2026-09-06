from typing import Any

from fastapi import APIRouter, HTTPException

from app.intelligence.score_engine import intelligence_score_engine

router = APIRouter(prefix="/intelligence-score", tags=["intelligence"])


@router.get("/status")
def status() -> dict[str, Any]:
    return intelligence_score_engine.stats


@router.get("/top")
def top(limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    return {"signals": intelligence_score_engine.stats["top_signals"][:limit]}


@router.post("/start")
def start() -> dict[str, Any]:
    try:
        intelligence_score_engine.start()
        return {"status": "STARTED", **intelligence_score_engine.stats}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop() -> dict[str, Any]:
    intelligence_score_engine.stop()
    return {"status": "STOPPED", **intelligence_score_engine.stats}
