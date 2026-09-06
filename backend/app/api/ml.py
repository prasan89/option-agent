from typing import Any

from fastapi import APIRouter, HTTPException

from app.ml.engine import ml_engine

router = APIRouter(prefix="/ml", tags=["ml"])


@router.get("/status")
def status() -> dict[str, Any]:
    return ml_engine.stats


@router.get("/latest")
def latest(limit: int = 25) -> dict[str, Any]:
    return {"observations": ml_engine.latest(max(1, min(limit, 100)))}


@router.post("/start")
def start() -> dict[str, Any]:
    try:
        ml_engine.start()
        return {"status": "STARTED", **ml_engine.stats}
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stop")
def stop() -> dict[str, Any]:
    ml_engine.stop()
    return {"status": "STOPPED", **ml_engine.stats}


@router.post("/train")
def train() -> dict[str, Any]:
    return ml_engine.train()


@router.post("/predict")
def predict(features: dict[str, Any]) -> dict[str, Any]:
    return ml_engine.predict(features)
