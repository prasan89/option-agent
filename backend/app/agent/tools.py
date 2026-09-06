from __future__ import annotations

from datetime import date
from typing import Any, Callable

from app.services.groww_client import groww_client


class MarketToolRegistry:
    """Deterministic market-data tools exposed to the future LLM agent.

    The LLM will select tools; Python remains authoritative for market-data
    retrieval and calculations.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {
            "get_nifty_option_chain": self.get_nifty_option_chain,
            "get_nifty_instruments": self.get_nifty_instruments,
            "get_ltp": self.get_ltp,
            "get_groww_profile": self.get_groww_profile,
        }

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {"name": "get_nifty_option_chain", "description": "Fetch NIFTY option-chain data for an expiry.", "parameters": {"expiry_date": "YYYY-MM-DD"}},
            {"name": "get_nifty_instruments", "description": "Find NIFTY F&O contracts in a strike range.", "parameters": {"expiry_date": "YYYY-MM-DD", "strike_min": "number", "strike_max": "number"}},
            {"name": "get_ltp", "description": "Fetch latest prices for up to 50 Groww exchange symbols.", "parameters": {"exchange_symbols": "array[string]"}},
            {"name": "get_groww_profile", "description": "Check the authenticated Groww account connectivity and enabled segments.", "parameters": {}},
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._tools:
            raise ValueError(f"Unknown market tool: {name}")
        return self._tools[name](**arguments)

    def get_nifty_option_chain(self, expiry_date: str) -> dict[str, Any]:
        return groww_client.option_chain(date.fromisoformat(expiry_date))

    def get_nifty_instruments(
        self,
        expiry_date: str | None = None,
        strike_min: float | None = None,
        strike_max: float | None = None,
    ) -> list[dict[str, Any]]:
        expiry = date.fromisoformat(expiry_date) if expiry_date else None
        return groww_client.nifty_fno_instruments(expiry, strike_min, strike_max)

    def get_ltp(self, exchange_symbols: list[str]) -> dict[str, Any]:
        if len(exchange_symbols) > 50:
            raise ValueError("Maximum 50 symbols per tool call")
        return groww_client.ltp(exchange_symbols)

    def get_groww_profile(self) -> dict[str, Any]:
        return groww_client.profile()


market_tools = MarketToolRegistry()
