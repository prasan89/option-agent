"""Compatibility entrypoint for the historical multi-pattern scanner."""

from app.price_action.scanner_multi import PriceActionScanner, price_action_scanner

__all__ = ["PriceActionScanner", "price_action_scanner"]
