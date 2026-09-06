from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.flow.engine import flow_engine

router = APIRouter(prefix="/flow", tags=["flow"])


@router.get("/status")
def status() -> dict[str, Any]:
    return flow_engine.stats


@router.post("/start")
def start() -> dict[str, Any]:
    try:
        flow_engine.start()
        return {"status": "STARTED", **flow_engine.stats}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Flow engine error: {exc}") from exc


@router.post("/stop")
def stop() -> dict[str, Any]:
    flow_engine.stop()
    return {"status": "STOPPED", **flow_engine.stats}
