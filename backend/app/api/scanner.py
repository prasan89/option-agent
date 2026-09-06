from typing import Any

from fastapi import APIRouter, HTTPException

from app.intelligence.fno_scanner import fno_scanner

router = APIRouter(prefix="/scanner", tags=["scanner"])


@router.get("/status")
def status() -> dict[str, Any]:
    return fno_scanner.stats


@router.get("/latest")
def latest(limit: int = 25) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    data = fno_scanner.stats
    return {
        "checks": data["checks"],
        "symbols_scanned_last_check": data["symbols_scanned_last_check"],
        "rankings": data["latest_rankings"][:limit],
    }


@router.post("/start")
def start() -> dict[str, Any]:
    try:
        fno_scanner.start()
        return {"status": "STARTED", **fno_scanner.stats}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop() -> dict[str, Any]:
    fno_scanner.stop()
    return {"status": "STOPPED", **fno_scanner.stats}
