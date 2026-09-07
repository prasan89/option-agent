from __future__ import annotations

from datetime import date
from typing import Any

from growwapi import GrowwAPI

from app.core.config import settings


class GrowwNotConfiguredError(RuntimeError):
    """Raised when Groww credentials are not configured."""


class GrowwClient:
    """Read-only Groww market-data client for the research phases."""

    def __init__(self) -> None:
        self._client: GrowwAPI | None = None

    @property
    def configured(self) -> bool:
        return bool(settings.groww_access_token or (settings.groww_api_key and settings.groww_api_secret))

    def _get_client(self) -> GrowwAPI:
        if self._client is not None:
            return self._client
        if settings.groww_access_token:
            self._client = GrowwAPI(settings.groww_access_token)
            return self._client
        if not (settings.groww_api_key and settings.groww_api_secret):
            raise GrowwNotConfiguredError("Groww credentials are not configured")
        token = GrowwAPI.get_access_token(api_key=settings.groww_api_key, secret=settings.groww_api_secret)
        self._client = GrowwAPI(token)
        return self._client

    def profile(self) -> dict[str, Any]:
        return self._get_client().get_user_profile()

    @staticmethod
    def _normalize_ltp_symbol(symbol: str) -> str:
        """Normalize an NSE F&O trading symbol for Groww's LTP API.

        Groww's live LTP endpoint expects exchange-prefixed symbols such as
        ``NSE_NIFTY26NOV19250CE``. The instrument master exposes the bare
        ``trading_symbol`` (``NIFTY26NOV19250CE``), so callers can use either
        form and this client keeps the API-specific formatting in one place.
        """
        value = str(symbol).strip()
        if not value:
            return value
        if value.startswith("NSE_"):
            return value
        return f"NSE_{value}"

    def ltp(self, exchange_symbols: list[str]) -> dict[str, Any]:
        normalized = [self._normalize_ltp_symbol(symbol) for symbol in exchange_symbols]
        return self._get_client().get_ltp(
            exchange_trading_symbols=normalized,
            segment=GrowwAPI.SEGMENT_FNO,
        )

    def option_chain(self, expiry_date: date) -> dict[str, Any]:
        return self._get_client().get_option_chain(
            exchange=GrowwAPI.EXCHANGE_NSE,
            underlying="NIFTY",
            expiry_date=expiry_date.isoformat(),
        )

    def quote(self, trading_symbol: str) -> dict[str, Any]:
        return self._get_client().get_quote(
            exchange=GrowwAPI.EXCHANGE_NSE,
            segment=GrowwAPI.SEGMENT_FNO,
            trading_symbol=trading_symbol,
        )

    def all_instruments(self) -> Any:
        return self._get_client().get_all_instruments()

    def fno_instruments(self, active_only: bool = True) -> list[dict[str, Any]]:
        """Return the complete NSE F&O instrument universe from the master.

        This deliberately does not restrict the underlying to NIFTY. Expired
        contracts are excluded when an expiry column is available.
        """
        df = self.all_instruments()
        if df is None or len(df) == 0:
            return []
        df = df.copy()
        if "exchange" in df.columns:
            df = df[df["exchange"].astype(str).str.upper().isin({"NSE", "NSE_EQ", "NSE_FNO"})]
        if "segment" in df.columns:
            df = df[df["segment"].astype(str).str.upper() == "FNO"]
        if active_only and "expiry_date" in df.columns:
            expiry = df["expiry_date"].astype(str).str[:10]
            df = df[(expiry == "") | (expiry == "nan") | (expiry >= date.today().isoformat())]

        columns = [
            "exchange", "exchange_token", "trading_symbol", "groww_symbol",
            "underlying_symbol", "expiry_date", "strike_price", "instrument_type",
            "lot_size", "tick_size", "segment",
        ]
        selected = [c for c in columns if c in df.columns]
        if not selected:
            selected = list(df.columns)
        return df[selected].fillna("").to_dict(orient="records")

    def nifty_fno_instruments(
        self,
        expiry_date: date | None = None,
        strike_min: float | None = None,
        strike_max: float | None = None,
    ) -> list[dict[str, Any]]:
        df = self.all_instruments()
        if df is None or len(df) == 0:
            return []
        df = df.copy()
        if "segment" in df.columns:
            df = df[df["segment"].astype(str).str.upper() == "FNO"]
        if "underlying_symbol" in df.columns:
            df = df[df["underlying_symbol"].astype(str).str.upper() == "NIFTY"]
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
