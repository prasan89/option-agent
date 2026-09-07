from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from typing import Any

from app.core.event_bus import research_event_bus
from app.services.groww_client import groww_client
from app.services.groww_feed import feed_service

logger = logging.getLogger(__name__)


class FNOScanner:
    """Streaming F&O activity scanner backed by Groww live feed."""

    INTERVAL_SECONDS = 5
    PUBLISH_INTERVAL_SECONDS = 60
    QUOTE_ENRICH_LIMIT = 20
    CHAIN_ENRICH_LIMIT = 5
    OUTPUT_TOPIC = "fno.rankings"
    MAX_RESULTS = 100
    MAX_UNDERLYING_RESULTS = 50
    EXPIRY_MONTHS_AHEAD = 2
    HISTORY_SECONDS = 180
    MIN_SCORE = 0.05

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = False
        self._latest: list[dict[str, Any]] = []
        self._latest_underlyings: list[dict[str, Any]] = []
        self._checks = 0
        self._symbols_scanned = 0
        self._symbols_available = 0
        self._unique_underlyings = 0
        self._quotes_received = 0
        self._successful_batches = 0
        self._failed_batches = 0
        self._invalid_symbols = 0
        self._errors = 0
        self._feed_events = 0
        self._enrichment_requests = 0
        self._enrichment_errors = 0
        self._last_timestamp: str | None = None
        self._last_publish = 0.0
        self._subscription: str | None = None
        self._meta_by_token: dict[str, dict[str, Any]] = {}
        self._state: dict[str, dict[str, Any]] = {}
        self._history: dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=240))
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            ranking_ready = any(row.get("change_pct_since_last_minute") is not None for row in self._state.values())
            return {
                "running": self.running,
                "mode": "LIVE_FEED",
                "interval_seconds": self.INTERVAL_SECONDS,
                "publish_interval_seconds": self.PUBLISH_INTERVAL_SECONDS,
                "expiry_months_ahead": self.EXPIRY_MONTHS_AHEAD,
                "checks": self._checks,
                "ranking_ready": ranking_ready,
                "symbols_available_last_check": self._symbols_available,
                "symbols_scanned_last_check": self._symbols_scanned,
                "unique_underlyings_last_check": self._unique_underlyings,
                "quotes_received_last_check": self._quotes_received,
                "successful_batches_last_check": self._successful_batches,
                "failed_batches_last_check": self._failed_batches,
                "invalid_symbols_last_check": self._invalid_symbols,
                "invalid_symbols_cached": 0,
                "diagnostic_requests_last_check": 0,
                "feed_events": self._feed_events,
                "enrichment_requests": self._enrichment_requests,
                "enrichment_errors": self._enrichment_errors,
                "errors": self._errors,
                "last_scan_timestamp": self._last_timestamp,
                "top_underlyings": self._latest_underlyings[: self.MAX_UNDERLYING_RESULTS],
                "latest_rankings": self._latest[: self.MAX_RESULTS],
            }

    @staticmethod
    def _token(meta: dict[str, Any]) -> str | None:
        value = meta.get("feed_key")
        return str(value) if value else None

    @staticmethod
    def _payload_token(payload: dict[str, Any], token: str) -> dict[str, Any] | None:
        try:
            return payload["NSE"]["FNO"][token]
        except (KeyError, TypeError):
            return None

    @staticmethod
    def _ltp_and_ts(payload: dict[str, Any], token: str) -> tuple[float | None, int]:
        row = FNOScanner._payload_token(payload, token)
        if not isinstance(row, dict):
            return None, 0
        try:
            return float(row["ltp"]), int(float(row.get("tsInMillis") or 0))
        except (KeyError, TypeError, ValueError):
            return None, 0

    @staticmethod
    def _depth(payload: dict[str, Any], token: str) -> tuple[float | None, float | None, float, float]:
        row = FNOScanner._payload_token(payload, token)
        if not isinstance(row, dict):
            return None, None, 0.0, 0.0
        bids = [v for v in (row.get("buyBook") or {}).values() if isinstance(v, dict)]
        asks = [v for v in (row.get("sellBook") or {}).values() if isinstance(v, dict)]
        best_bid = max((float(v["price"]) for v in bids if "price" in v), default=None)
        best_ask = min((float(v["price"]) for v in asks if "price" in v), default=None)
        bid_qty = sum(float(v.get("qty", 0)) for v in bids)
        ask_qty = sum(float(v.get("qty", 0)) for v in asks)
        return best_bid, best_ask, bid_qty, ask_qty

    @staticmethod
    def _score(change: float | None) -> float:
        if change is None:
            return 0.0
        return round(min(100.0, abs(change) * 20.0), 2)

    @staticmethod
    def _direction(change: float | None) -> str:
        if change is None or abs(change) < 0.0001:
            return "FLAT"
        return "UP" if change > 0 else "DOWN"

    def _minute_change(self, token: str, timestamp_ms: int, price: float) -> float | None:
        history = self._history[token]
        cutoff = timestamp_ms - 60_000
        reference: tuple[int, float] | None = None
        for item in history:
            if item[0] <= cutoff:
                reference = item
            else:
                break
        history.append((timestamp_ms, price))
        if reference is None or reference[1] == 0:
            return None
        return (price - reference[1]) / abs(reference[1]) * 100.0

    def _process_feed_event(self, event: dict[str, Any]) -> None:
        meta = event.get("meta") or {}
        token = self._token(meta)
        if not token:
            return
        feed_type = event.get("feed_type")
        payload = event.get("payload") or {}
        if feed_type not in {"ltp", "market_depth"}:
            return
        row = self._meta_by_token.get(token)
        if row is None:
            return

        state = self._state.setdefault(token, {
            "symbol": row.get("trading_symbol"),
            "groww_exchange_symbol": f"NSE_{row.get('trading_symbol')}",
            "underlying": row.get("underlying_symbol"),
            "exchange": "NSE",
            "segment": "FNO",
            "instrument_type": row.get("instrument_type"),
            "expiry_date": row.get("expiry_date"),
            "strike_price": row.get("strike_price"),
            "exchange_token": token,
            "lot_size": row.get("lot_size"),
            "data_sources": ["GROWW_FEED"],
        })

        if feed_type == "ltp":
            price, timestamp_ms = self._ltp_and_ts(payload, token)
            if price is None or price <= 0:
                return
            if not timestamp_ms:
                timestamp_ms = int(time.time() * 1000)
            change = self._minute_change(token, timestamp_ms, price)
            state["ltp"] = round(price, 4)
            state["change_pct_since_last_minute"] = None if change is None else round(change, 4)
            state["activity_score"] = self._score(change)
            state["direction"] = self._direction(change)
            state["timestamp_ms"] = timestamp_ms
        else:
            bid, ask, bid_qty, ask_qty = self._depth(payload, token)
            state["best_bid"] = bid
            state["best_ask"] = ask
            state["bid_qty"] = bid_qty
            state["ask_qty"] = ask_qty
            if bid_qty + ask_qty > 0:
                state["depth_imbalance"] = round((bid_qty - ask_qty) / (bid_qty + ask_qty), 4)
            state["timestamp_ms"] = int(time.time() * 1000)
        state["data_completeness"] = "FEED_LTP_DEPTH"
        with self._lock:
            self._feed_events += 1

    def _on_feed_event(self, event: dict[str, Any]) -> None:
        try:
            self._process_feed_event(event)
        except Exception:
            with self._lock:
                self._errors += 1
            logger.exception("Failed to process streaming F&O event")

    @staticmethod
    def _enrich_quote(row: dict[str, Any], quote: dict[str, Any]) -> None:
        mappings = {
            "open_interest": "open_interest",
            "volume": "volume",
            "last_trade_quantity": "last_trade_quantity",
            "last_trade_time": "last_trade_time",
            "implied_volatility": "implied_volatility",
            "total_buy_quantity": "total_buy_quantity",
            "total_sell_quantity": "total_sell_quantity",
            "oi_day_change": "oi_day_change",
            "oi_day_change_percentage": "oi_day_change_percentage",
            "average_price": "average_price",
        }
        for source, target in mappings.items():
            if source in quote:
                row[target] = quote[source]
        depth = quote.get("depth")
        if isinstance(depth, dict):
            buy = depth.get("buy") or []
            sell = depth.get("sell") or []
            if buy:
                row["best_bid"] = buy[0].get("price")
                row["bid_qty"] = sum(float(x.get("quantity", 0)) for x in buy if isinstance(x, dict))
            if sell:
                row["best_ask"] = sell[0].get("price")
                row["ask_qty"] = sum(float(x.get("quantity", 0)) for x in sell if isinstance(x, dict))
        sources = set(row.get("data_sources") or [])
        sources.add("QUOTE")
        row["data_sources"] = sorted(sources)
        row["data_completeness"] = "ENRICHED_QUOTE"

    def _enrich(self, rows: list[dict[str, Any]]) -> None:
        candidates = [r for r in rows if (r.get("activity_score") or 0) >= self.MIN_SCORE]
        candidates.sort(key=lambda r: float(r.get("activity_score") or 0), reverse=True)
        for row in candidates[: self.QUOTE_ENRICH_LIMIT]:
            try:
                quote = groww_client.quote(str(row["symbol"]))
                if isinstance(quote, dict):
                    self._enrich_quote(row, quote)
                with self._lock:
                    self._enrichment_requests += 1
            except Exception as exc:
                with self._lock:
                    self._enrichment_errors += 1
                logger.warning("Quote enrichment failed for %s: %s", row.get("symbol"), exc)

        top_underlyings: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in candidates:
            underlying = str(row.get("underlying") or "").strip()
            expiry = str(row.get("expiry_date") or "")[:10]
            if underlying and underlying not in seen:
                seen.add(underlying)
                top_underlyings.append((underlying, expiry))
            if len(top_underlyings) >= self.CHAIN_ENRICH_LIMIT:
                break

        by_symbol = {str(r.get("symbol")): r for r in rows}
        for underlying, expiry_text in top_underlyings:
            try:
                expiry = date.fromisoformat(expiry_text)
                chain = groww_client.option_chain(expiry, underlying=underlying)
                strikes = chain.get("strikes") if isinstance(chain, dict) else None
                if not isinstance(strikes, dict):
                    continue
                for strike_data in strikes.values():
                    if not isinstance(strike_data, dict):
                        continue
                    for typ in ("CE", "PE"):
                        contract = strike_data.get(typ)
                        if not isinstance(contract, dict):
                            continue
                        symbol = str(contract.get("trading_symbol") or "")
                        target = by_symbol.get(symbol)
                        if target is None:
                            continue
                        target["open_interest"] = contract.get("open_interest")
                        target["volume"] = contract.get("volume")
                        greeks = contract.get("greeks") or {}
                        if isinstance(greeks, dict):
                            for key in ("delta", "gamma", "theta", "vega", "rho", "iv"):
                                if key in greeks:
                                    target[key] = greeks[key]
                        sources = set(target.get("data_sources") or [])
                        sources.add("OPTION_CHAIN")
                        target["data_sources"] = sorted(sources)
                        target["data_completeness"] = "ENRICHED_OPTION_CHAIN"
                with self._lock:
                    self._enrichment_requests += 1
            except Exception as exc:
                with self._lock:
                    self._enrichment_errors += 1
                logger.warning("Option-chain enrichment failed for %s %s: %s", underlying, expiry_text, exc)

    @classmethod
    def _aggregate_underlyings(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            underlying = str(row.get("underlying") or "").strip()
            if underlying:
                grouped[underlying].append(row)
        output: list[dict[str, Any]] = []
        for underlying, contracts in grouped.items():
            active = [r for r in contracts if r.get("change_pct_since_last_minute") is not None]
            if not active:
                continue
            ordered = sorted(active, key=lambda r: float(r.get("activity_score") or 0), reverse=True)
            top = ordered[:5]
            signed = sum(float(r.get("change_pct_since_last_minute") or 0) for r in top)
            scores = [float(r.get("activity_score") or 0) for r in top]
            score = round(min(100.0, sum(scores) / len(scores)), 2) if scores else 0.0
            up = sum(1 for r in active if float(r.get("change_pct_since_last_minute") or 0) > 0)
            down = sum(1 for r in active if float(r.get("change_pct_since_last_minute") or 0) < 0)
            if score <= 0:
                continue
            output.append({
                "underlying": underlying,
                "activity_score": score,
                "direction": "UP" if signed > 0 else "DOWN" if signed < 0 else "FLAT",
                "contracts_active": len(active),
                "contracts_up": up,
                "contracts_down": down,
                "max_change_pct": round(float(top[0].get("change_pct_since_last_minute") or 0), 4),
                "top_contracts": [
                    {
                        "symbol": r["symbol"], "change_pct": r["change_pct_since_last_minute"],
                        "instrument_type": r.get("instrument_type"), "expiry_date": r.get("expiry_date"),
                        "strike_price": r.get("strike_price"), "ltp": r.get("ltp"),
                        "activity_score": r.get("activity_score"), "open_interest": r.get("open_interest"),
                        "volume": r.get("volume"),
                    }
                    for r in top
                ],
                "data_sources": sorted({s for r in top for s in (r.get("data_sources") or [])}),
                "data_completeness": "ENRICHED" if any(len(r.get("data_sources") or []) > 1 for r in top) else "FEED_LTP_DEPTH",
            })
        output.sort(key=lambda x: (x["activity_score"], abs(x["max_change_pct"])), reverse=True)
        return output[: cls.MAX_UNDERLYING_RESULTS]

    def _publish_snapshot(self) -> None:
        with self._lock:
            rows = [dict(row) for row in self._state.values() if row.get("ltp") is not None]
        if not rows:
            return
        if not any(row.get("change_pct_since_last_minute") is not None for row in rows):
            return
        if time.time() - self._last_publish < self.PUBLISH_INTERVAL_SECONDS:
            return

        self._enrich(rows)
        rows.sort(key=lambda r: (float(r.get("activity_score") or 0), abs(float(r.get("change_pct_since_last_minute") or 0))), reverse=True)
        underlying_rankings = self._aggregate_underlyings(rows)
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._checks += 1
            self._latest = rows[: self.MAX_RESULTS]
            self._latest_underlyings = underlying_rankings
            self._symbols_available = len(self._meta_by_token)
            self._symbols_scanned = len(rows)
            self._unique_underlyings = len({str(r.get("underlying") or "") for r in rows if r.get("underlying")})
            self._quotes_received = len(rows)
            self._successful_batches = 1
            self._failed_batches = 0
            self._last_timestamp = timestamp
            self._last_publish = time.time()

        research_event_bus.publish(self.OUTPUT_TOPIC, {
            "timestamp": timestamp,
            "check": self._checks,
            "ranking_ready": True,
            "mode": "LIVE_FEED",
            "symbols_available": len(self._meta_by_token),
            "symbols_scanned": len(rows),
            "quotes_received": len(rows),
            "successful_batches": 1,
            "failed_batches": 0,
            "invalid_symbols": 0,
            "unique_underlyings": self._unique_underlyings,
            "observations": rows,
            "rankings": rows[: self.MAX_RESULTS],
            "underlying_rankings": underlying_rankings,
        })

    def _run(self) -> None:
        while self._running:
            try:
                self._publish_snapshot()
            except Exception:
                with self._lock:
                    self._errors += 1
                logger.exception("F&O streaming scanner iteration failed")
            time.sleep(self.INTERVAL_SECONDS)
        self._running = False

    def start(self) -> None:
        if self.running:
            raise RuntimeError("F&O scanner is already running")
        if not groww_client.configured:
            raise RuntimeError("Groww credentials are not configured")
        instruments = groww_client.fno_instruments(active_only=True)
        feed_tokens = {str(item.get("exchange_token")) for item in feed_service.instruments}
        if not feed_tokens:
            raise RuntimeError("Groww live feed has no subscribed instruments")
        self._meta_by_token = {
            str(row.get("exchange_token")): row
            for row in instruments
            if str(row.get("exchange_token")) in feed_tokens
        }
        if not self._meta_by_token:
            raise RuntimeError("No scanner metadata matched the Groww live-feed subscriptions")
        self._subscription = research_event_bus.subscribe("market.raw", self._on_feed_event)
        self._running = True
        self._thread = threading.Thread(target=self._run, name="fno-stream-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._subscription:
            research_event_bus.unsubscribe(self._subscription)
            self._subscription = None


fno_scanner = FNOScanner()
