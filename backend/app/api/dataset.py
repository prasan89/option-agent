from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.dataset.collector import historical_dataset_collector

router=APIRouter(prefix="/dataset",tags=["dataset"])

@router.get("/status")
def status()->dict[str,Any]:return historical_dataset_collector.stats

@router.post("/start")
def start()->dict[str,Any]:
    try:historical_dataset_collector.start();return {"status":"STARTED",**historical_dataset_collector.stats}
    except RuntimeError as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc

@router.post("/stop")
def stop()->dict[str,Any]:historical_dataset_collector.stop();return {"status":"STOPPED",**historical_dataset_collector.stats}

@router.get("/recent")
def recent(limit:int=Query(default=25,ge=1,le=100))->dict[str,Any]:
    try:rows=historical_dataset_collector.recent(limit)
    except Exception as exc:raise HTTPException(status_code=503,detail=f"Database unavailable: {exc}") from exc
    return {"count":len(rows),"observations":rows}
