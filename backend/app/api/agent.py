from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agent.tools import market_tools
from app.services.groww_client import GrowwNotConfiguredError

router = APIRouter(prefix="/agent", tags=["agent"])


class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("/status")
def agent_status() -> dict[str, Any]:
    return {
        "status": "READY",
        "mode": "tool-enabled-market-agent",
        "llm_connected": False,
        "trading": "DISABLED",
        "tools": [tool["name"] for tool in market_tools.definitions()],
    }


@router.get("/tools")
def tool_definitions() -> list[dict[str, Any]]:
    return market_tools.definitions()


@router.post("/tools/call")
def call_tool(request: ToolCallRequest) -> dict[str, Any]:
    try:
        result = market_tools.call(request.tool, request.arguments)
        return {"tool": request.tool, "result": result}
    except GrowwNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Market tool failed: {exc}") from exc
