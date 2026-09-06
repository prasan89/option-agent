from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.groww_client import GrowwNotConfiguredError, groww_client
from app.services.groww_feed import feed_service

router = APIRouter(prefix="/groww", tags=["groww"])


class FeedInstrument(BaseModel):
    exchange: str = Field(default="NSE")
    segment: str = Field(default="FNO")
    exchange_token: str


class FeedStartRequest(BaseModel):
    instruments: list[FeedInstrument] = Field(min_length=1, max_length=1000)


def _groww_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=f"Groww API error: {exc}")


@router.get("/status")
def status() -> dict[str, Any]:
    return {
        "configured": groww_client.configured,
        "feed_running": feed_service.running,
        "feed_instruments": len(feed_service.instruments),
        "trading": "DISABLED",
    }


@router.get("/profile")
def profile() -> dict[str, Any]:
    try:
        return groww_client.profile()
    except GrowwNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise _groww_error(exc) from exc


@router.get("/option-chain")
def option_chain(expiry_date: date = Query(..., description="NIFTY expiry in YYYY-MM-DD")) -> dict[str, Any]:
    try:
        return groww_client.option_chain(expiry_date)
    except GrowwNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise _groww_error(exc) from exc


@router.get("/ltp")
def ltp(exchange_symbols: list[str] = Query(..., description="Groww symbols such as NSE_NIFTY...")) -> dict[str, Any]:
    if len(exchange_symbols) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 instruments per LTP request")
    try:
        return groww_client.ltp(exchange_symbols)
    except GrowwNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise _groww_error(exc) from exc


@router.get("/nifty/instruments")
def nifty_instruments(
    expiry_date: date | None = Query(default=None),
    strike_min: float | None = Query(default=None, ge=0),
    strike_max: float | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    try:
        return groww_client.nifty_fno_instruments(expiry_date, strike_min, strike_max)
    except GrowwNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise _groww_error(exc) from exc


@router.post("/feed/start")
def start_feed(request: FeedStartRequest) -> dict[str, Any]:
    if feed_service.running:
        raise HTTPException(status_code=409, detail="Groww feed is already running")
    try:
        instruments = [instrument.model_dump() for instrument in request.instruments]
        feed_service.start(instruments)
        return {
            "status": "STARTED",
            "instruments": len(instruments),
            "redis_stream": feed_service.STREAM_KEY,
            "trading": "DISABLED",
        }
    except GrowwNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise _groww_error(exc) from exc
