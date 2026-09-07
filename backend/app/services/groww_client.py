from __future__ import annotations

from datetime import date, timedelta
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
    MAX_FNO_MONTHS_AHEAD = 2

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

    @staticmethod
    def _month_end(month_start: date) -> date:
        if month_start.month == 12:
            next_month = date(month_start.year + 1, 1, 1)
        else:
            next_month = date(month_start.year, month_start.month + 1, 1)
        return next_month - timedelta(days=1)

    @classmethod
    def _expiry_window(cls) -> tuple[date, date]:
        today = date.today()
        current_month_start = date(today.year, today.month, 1)
        # Current month + the next MAX_FNO_MONTHS_AHEAD calendar months.
        # With MAX_FNO_MONTHS_AHEAD=2 in September, the last allowed expiry
        # is November 30; December contracts are deliberately excluded.
        target_month_index = current_month_start.month - 1 + cls.MAX_FNO_MONTHS_AHEAD
        target_year = current_month_start.year + target_month_index // 12
        target_month = target_month_index % 12 + 1
        return today, cls._month_end(date(target_year, target_month, 1))

    @classmethod
    def _active_expiry_mask(cls, df: Any) -> Any:
        expiry = df["expiry_date"].astype(str).str[:10]
        today, max_expiry = cls._expiry_window()
        return (
            expiry.str.fullmatch(r"\d{4}-\d{2}-\d{2}", na=False)
            & (expiry >= today.isoformat())
            & (expiry <= max_expiry.isoformat())
        )

    @staticmethod
    def _flag_mask(df: Any, column: str) -> Any:
        return df[column].astype(str).str.strip().str.lower().isin({"1", "true", "yes"})

    @classmethod
    def _tradable_mask(cls, df: Any) -> Any:
        is_reserved = cls._flag_mask(df, "is_reserved")
        buy_allowed = cls._flag_mask(df, "buy_allowed")
        sell_allowed = cls._flag_mask(df, "sell_allowed")
        return ~is_reserved & (buy_allowed | sell_allowed)

    @staticmethod
    def _quality_mask(df: Any) -> Any:
        symbol = df["trading_symbol"].fillna("").astype(str).str.strip().str.upper()
        # Groww's instrument master can contain non-tradable/test contracts.
        return symbol.ne("") & ~symbol.str.contains("NSETEST", regex=False, na=False)

    def fno_instruments(self, active_only: bool = True) -> list[dict[str, Any]]:
        df = self.all_instruments()
        if df is None or len(df) == 0:
            return []
        df = df.copy()
        if "exchange" in df.columns:
            df = df[df["exchange"].astype(str).str.upper().eq("NSE")]
        if "segment" in df.columns:
            df = df[df["segment"].astype(str).str.upper().eq("FNO")]
        required = {"expiry_date", "is_reserved", "buy_allowed", "sell_allowed", "trading_symbol"}
        missing = required.difference(df.columns)
        if missing:
            raise RuntimeError(f"Groww instrument master is missing required fields: {', '.join(sorted(missing))}")
        df = df[self._quality_mask(df)]
        df = df[self._tradable_mask(df)]
        if active_only:
            df = df[self._active_expiry_mask(df)]
        columns = [
            "exchange",
            "exchange_token",
            "trading_symbol",
            "groww_symbol",
            "underlying_symbol",
            "expiry_date",
            "strike_price",
            "instrument_type",
            "lot_size",
            "tick_size",
            "segment",
            "is_reserved",
            "buy_allowed",
            "sell_allowed",
        ]
        selected = [c for c in columns if c in df.columns]
        return df[selected].fillna("").to_dict(orient="records")

    def nifty_fno_instruments(self, expiry_date: date | None = None, strike_min: float | None = None, strike_max: float | None = None) -> list[dict[str, Any]]:
        df = self.all_instruments()
        if df is None or len(df) == 0:
            return []
        df = df.copy()
        if "exchange" in df.columns:
            df = df[df["exchange"].astype(str).str.upper().eq("NSE")]
        if "segment" in df.columns:
            df = df[df["segment"].astype(str).str.upper().eq("FNO")]
        if "underlying_symbol" in df.columns:
            df = df[df["underlying_symbol"].astype(str).str.upper().eq("NIFTY")]
        required = {"expiry_date", "is_reserved", "buy_allowed", "sell_allowed", "trading_symbol"}
        missing = required.difference(df.columns)
        if missing:
            raise RuntimeError(f"Groww instrument master is missing required fields: {', '.join(sorted(missing))}")
        df = df[self._quality_mask(df)]
        df = df[self._tradable_mask(df)]
        df = df[self._active_expiry_mask(df)]
        if expiry_date is not None:
            df = df[df["expiry_date"].astype(str).str[:10] == expiry_date.isoformat()]
        if strike_min is not None and "strike_price" in df.columns:
            df = df[df["strike_price"].astype(float) >= strike_min]
        if strike_max is not None and "strike_price" in df.columns:
            df = df[df["strike_price"].astype(float) <= strike_max]
        columns = [
            "exchange",
            "exchange_token",
            "trading_symbol",
            "groww_symbol",
            "underlying_symbol",
            "expiry_date",
            "strike_price",
            "instrument_type",
            "lot_size",
            "tick_size",
            "is_reserved",
            "buy_allowed",
            "sell_allowed",
        ]
        selected = [c for c in columns if c in df.columns]
        return df[selected].fillna("").to_dict(orient="records")


groww_client = GrowwClient()
