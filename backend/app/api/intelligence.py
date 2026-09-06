from __future__ import annotations

from typing import Any
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.flow.intelligence import flow_intelligence

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class AnalyzeRequest(BaseModel):
    chain: dict[str, Any]
    previous: dict[str, Any] | None = None


@router.post("/analyze-chain")
def analyze_chain(request: AnalyzeRequest) -> dict[str, Any]:
    return flow_intelligence.analyze(request.chain, request.previous)


@router.get("/status")
def status() -> dict[str, Any]:
    return {"status": "READY", "features": ["PCR", "OI-price regimes", "volume pressure", "flow bias"], "trading": "DISABLED"}
