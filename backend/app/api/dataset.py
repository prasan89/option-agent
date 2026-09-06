from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.dataset.collector import historical_dataset_collector

router = APIRouter(prefix="/dataset", tags=["dataset"])


@router.get("/status")
def status() -> dict[str, Any]:
    return historical_dataset_collector.stats


@router.post("/start")
def start() -> dict[str, Any]:
    try:
        historical_dataset_collector.start()
        return {"status": "STARTED", **historical_dataset_collector.stats}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop() -> dict[str, Any]:
    historical_dataset_collector.stop()
    return {"status": "STOPPED", **historical_dataset_collector.stats}


@router.get("/recent")
def recent(limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    import json
    import redis

    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    rows = client.xrevrange(historical_dataset_collector.OUTPUT_STREAM, count=limit)
    return {
        "count": len(rows),
        "observations": [
            {"id": entry_id, **json.loads(values.get("observation", "{}"))}
            for entry_id, values in rows
        ],
    }
