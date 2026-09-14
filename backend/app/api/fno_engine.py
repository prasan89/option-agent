from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.strategy.fno_engine import fno_opportunity_engine

router = APIRouter(prefix="/fno", tags=["fno-engine"])


@router.get("/status")
def status() -> dict[str, Any]:
    return fno_opportunity_engine.status()


@router.post("/rank")
def rank(rows: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(int(limit), 10))
    results = fno_opportunity_engine.rank(rows, limit=limit)
    return {
        "timestamp": fno_opportunity_engine.last_run,
        "count": len(results),
        "results": results,
        "risk": fno_opportunity_engine.status(),
        "research_only": True,
        "trading": "DISABLED",
    }


@router.post("/paper/reset")
def paper_reset() -> dict[str, Any]:
    fno_opportunity_engine.reset_day()
    return fno_opportunity_engine.status()


@router.post("/paper/fill")
def paper_fill(trade: dict[str, Any]) -> dict[str, Any]:
    allowed, reason = fno_opportunity_engine.can_trade()
    if not allowed:
        return {"accepted": False, "reason": reason, "risk": fno_opportunity_engine.status()}
    return fno_opportunity_engine.register_paper_fill(trade)


@router.post("/paper/close")
def paper_close(pnl: float) -> dict[str, Any]:
    return fno_opportunity_engine.close_paper_fill(pnl)
