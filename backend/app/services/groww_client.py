from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from growwapi import GrowwAPI

from app.core.config import settings


class GrowwNotConfiguredError(RuntimeError):
    """Raised when Groww credentials are not configured."""


class GrowwClient:
    """Read-only Groww market-data client for the research phases."""

    API_BASE_URL = "https://api.groww.in"
    API_VERSION = "1.0"

    def __init__(self) -> None:
        self._client: GrowwAPI | None = None
        self._access_token = ""

    @property
    def configured(self) -> bool:
        return bool(settings.groww_access_token or (settings.groww_api_key and settings.groww_api_secret))

    def _get_client(self) -> GrowwAPI:
        if self._client is not None:
            return self._client
        if settings.groww_access_token:
            self._access_token = settings.groww_access_token
            self._client = GrowwAPI(self._access_token)
            return self._client
        if not (settings.groww_api_key and settings.groww_api_secret):
            raise GrowwNotConfiguredError("Groww credentials are not configured")
        self._access_token = GrowwAPI.get_access_token(api_key=settings.groww_api_key, secret=settings.groww_api_secret)
        self._client = GrowwAPI(self._access_token)
        return self._client

    def profile(self) -> dict[str, Any]:
        return self._get_client().get_user_profile()

    @staticmethod
    def _normalize_ltp_symbol(symbol: str) -> str:
        value = str(symbol).strip()
        if not value:
            return value
        return value if value.startswith("NSE_") else f"NSE_{value}"

    def ltp(self, exchange_symbols: list[str]) -> dict[str, Any]:
        """Fetch F&O LTPs using Groww's documented REST live-data endpoint."""
        normalized: list[str] = []
        seen: set[str] = set()
        for symbol in exchange_symbols:
            value = self._normalize_ltp_symbol(symbol)
            if value and value not in seen:
                normalized.append(value)
                seen.add(value)
        if not normalized:
            return {}
        if len(normalized) > 50:
            raise ValueError("Groww LTP supports at most 50 instruments per request")

        if not self._access_token:
            self._get_client()
        if not self._access_token:
            raise GrowwNotConfiguredError("Groww access token is unavailable")

        params = {
            "segment": GrowwAPI.SEGMENT_FNO,
            "exchange_symbols": ",".join(normalized),
        }
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
            "X-API-VERSION": self.API_VERSION,
        }
        with httpx.Client(timeout=15.0) as client:
            response = client.get(f"{self.API_BASE_URL}/v1/live-data/ltp", params=params, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"Groww LTP HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
            return payload["payload"]
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("Groww LTP returned an unexpected response")

    def option_chain(self, expiry_date: date) -> dict[str, Any]:
        return self._get_client().get_option_chain(exchange=GrowwAPI.EXCHANGE_NSE, underlying="NIFTY", expiry_date=expiry_date.isoformat())

    def quote(self, trading_symbol: str) -> dict[str, Any]:
        return self._get_client().get_quote(exchange=GrowwAPI.EXCHANGE_NSE, segment=GrowwAPI.SEGMENT_FNO, trading_symbol=trading_symbol)

    def all_instruments(self) -> Any:
        return self._get_client().get_all_instruments()

    def fno_instruments(self, active_only: bool = True) -> list[dict[str, Any]]:
        df = self.all_instruments()
        if df is None or len(df) == 0:
            return []
        df = df.copy()
        if "exchange" in df.columns:
            df = df[df["exchange"].astype(str).str.upper().eq("NSE")]
        if "segment" in df.columns:
            df = df[df["segment"].astype(str).str.upper().eq("FNO")]
        if active_only and "expiry_date" in df.columns:
            expiry = df["expiry_date"].astype(str).str[:10]
            df = df[(expiry == "") | (expiry == "nan") | (expiry >= date.today().isoformat())]
        columns = ["exchange", "exchange_token", "trading_symbol", "groww_symbol", "underlying_symbol", "expiry_date", "strike_price", "instrument_type", "lot_size", "tick_size", "segment"]
        selected = [c for c in columns if c in df.columns]
        if not selected:
            selected = list(df.columns)
        return df[selected].fillna("").to_dict(orient="records")

    def nifty_fno_instruments(self, expiry_date: date | None = None, strike_min: float | None = None, strike_max: float | None = None) -> list[dict[str, Any]]:
        df = self.all_instruments()
        if df is None or len(df) == 0:
            return []
        df = df.copy()
        if "segment" in df.columns:
            df = df[df["segment"].astype(str).str.upper().eq("FNO")]
        if "underlying_symbol" in df.columns:
            df = df[df["underlying_symbol"].astype(str).str.upper().eq("NIFTY")]
        if expiry_date is not None and "expiry_date" in df.columns:
            df = df[df["expiry_date"].astype(str).str[:10] == expiry_date.isoformat()]
        if strike_min is not None and "strike_price" in df.columns:
            df = df[df["strike_price"].astype(float) >= strike_min]
        if strike_max is not None and "strike_price" in df.columns:
            df = df[df["strike_price"].astype(float) <= strike_max]
        columns = ["exchange", "exchange_token", "trading_symbol", "groww_symbol", "underlying_symbol", "expiry_date", "strike_price", "instrument_type", "lot_size", "tick_size"]
        selected = [c for c in columns if c in df.columns]
        return df[selected].fillna("").to_dict(orient="records")


groww_client = GrowwClient()
