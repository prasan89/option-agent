from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.signals.monitor import signal_monitor
from app.signals.store import signal_store

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
def recent_signals(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        return signal_store.recent(limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Signal database unavailable: {exc}") from exc


@router.get("/status")
def status() -> dict[str, Any]:
    return signal_monitor.stats


@router.post("/start")
def start() -> dict[str, Any]:
    try:
        signal_monitor.start()
        return {"status": "STARTED", **signal_monitor.stats}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/stop")
def stop() -> dict[str, Any]:
    signal_monitor.stop()
    return {"status": "STOPPED", **signal_monitor.stats}


@router.post("/run-once")
def run_once() -> dict[str, Any]:
    try:
        return signal_monitor.run_once()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
