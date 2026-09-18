from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import psycopg

from app.core.config import settings


class SignalStore:
    """PostgreSQL persistence layer for research signals and opportunity history."""

    def __init__(self) -> None:
        self.database_url = self._normalize_url(settings.database_url)

    @staticmethod
    def _normalize_url(url: str) -> str:
        return url.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect(self):
        return psycopg.connect(self.database_url)

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id BIGSERIAL PRIMARY KEY,
                    signal_key TEXT NOT NULL UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    underlying TEXT,
                    instrument_type TEXT,
                    expiry_date TEXT,
                    strike_price DOUBLE PRECISION,
                    ltp DOUBLE PRECISION,
                    direction TEXT NOT NULL,
                    bias TEXT NOT NULL,
                    score DOUBLE PRECISION NOT NULL,
                    confidence TEXT,
                    ml_probability_up DOUBLE PRECISION,
                    ml_probability_down DOUBLE PRECISION,
                    event TEXT,
                    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                    payload JSONB NOT NULL
                )
                """
            )
            for column, definition in (
                ("underlying", "TEXT"),
                ("instrument_type", "TEXT"),
                ("expiry_date", "TEXT"),
                ("strike_price", "DOUBLE PRECISION"),
                ("ltp", "DOUBLE PRECISION"),
                ("confidence", "TEXT"),
                ("ml_probability_up", "DOUBLE PRECISION"),
                ("ml_probability_down", "DOUBLE PRECISION"),
                ("event", "TEXT"),
                ("evidence", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
                ("payload", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
            ):
                conn.execute(f"ALTER TABLE signals ADD COLUMN IF NOT EXISTS {column} {definition}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_underlying ON signals(underlying)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS opportunity_history (
                    id BIGSERIAL PRIMARY KEY,
                    opportunity_key TEXT NOT NULL UNIQUE,
                    trading_day DATE NOT NULL,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    last_qualified_at TIMESTAMPTZ NOT NULL,
                    underlying TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    option_type TEXT,
                    strike DOUBLE PRECISION,
                    expiry TEXT,
                    direction TEXT,
                    signal TEXT,
                    score DOUBLE PRECISION NOT NULL,
                    peak_score DOUBLE PRECISION NOT NULL,
                    premium DOUBLE PRECISION,
                    stop_premium DOUBLE PRECISION,
                    target_premium DOUBLE PRECISION,
                    breakeven DOUBLE PRECISION,
                    dte INTEGER,
                    delta DOUBLE PRECISION,
                    gamma DOUBLE PRECISION,
                    theta DOUBLE PRECISION,
                    vega DOUBLE PRECISION,
                    iv DOUBLE PRECISION,
                    volume DOUBLE PRECISION,
                    open_interest DOUBLE PRECISION,
                    status TEXT NOT NULL DEFAULT 'LIVE',
                    reason TEXT,
                    payload JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_history_day ON opportunity_history(trading_day, last_seen_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_history_underlying ON opportunity_history(underlying, trading_day)")
            conn.commit()

    def insert_many(self, signals: list[dict[str, Any]]) -> int:
        if not signals:
            return 0
        inserted = 0
        with self.connect() as conn:
            for s in signals:
                cur = conn.execute(
                    """
                    INSERT INTO signals
                    (signal_key, created_at, symbol, underlying, instrument_type, expiry_date,
                     strike_price, ltp, direction, bias, score, confidence,
                     ml_probability_up, ml_probability_down, event, evidence, payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (signal_key) DO NOTHING
                    """,
                    (
                        s["signal_key"],
                        s.get("created_at") or datetime.now(timezone.utc),
                        s.get("symbol", ""), s.get("underlying"), s.get("instrument_type"),
                        s.get("expiry_date"), s.get("strike_price"), s.get("ltp"),
                        s.get("direction", "UNKNOWN"), s.get("bias", "NEUTRAL"),
                        s.get("score", 0.0), s.get("confidence"),
                        s.get("ml_probability_up"), s.get("ml_probability_down"),
                        s.get("event"), json.dumps(s.get("evidence", [])), json.dumps(s),
                    ),
                )
                inserted += cur.rowcount
            conn.commit()
        return inserted

    def upsert_opportunities(self, opportunities: list[dict[str, Any]], now: datetime | None = None) -> int:
        """Persist every qualifying opportunity without losing it when live filters change."""
        if not opportunities:
            return 0
        now = now or datetime.now(timezone.utc)
        day = now.date()
        written = 0
        with self.connect() as conn:
            for item in opportunities:
                key = ":".join([
                    str(day), str(item.get("underlying") or ""), str(item.get("symbol") or ""),
                    str(item.get("option_type") or ""), str(item.get("strike") or ""), str(item.get("expiry") or ""),
                ])
                cur = conn.execute(
                    """
                    INSERT INTO opportunity_history
                    (opportunity_key, trading_day, first_seen_at, last_seen_at, last_qualified_at,
                     underlying, symbol, option_type, strike, expiry, direction, signal, score, peak_score,
                     premium, stop_premium, target_premium, breakeven, dte, delta, gamma, theta, vega, iv,
                     volume, open_interest, status, reason, payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (opportunity_key) DO UPDATE SET
                        last_seen_at=EXCLUDED.last_seen_at,
                        last_qualified_at=EXCLUDED.last_qualified_at,
                        score=EXCLUDED.score,
                        peak_score=GREATEST(opportunity_history.peak_score, EXCLUDED.peak_score),
                        premium=EXCLUDED.premium,
                        stop_premium=EXCLUDED.stop_premium,
                        target_premium=EXCLUDED.target_premium,
                        breakeven=EXCLUDED.breakeven,
                        delta=EXCLUDED.delta, gamma=EXCLUDED.gamma, theta=EXCLUDED.theta,
                        vega=EXCLUDED.vega, iv=EXCLUDED.iv, volume=EXCLUDED.volume,
                        open_interest=EXCLUDED.open_interest,
                        status='LIVE', reason=EXCLUDED.reason, payload=EXCLUDED.payload
                    """,
                    (
                        key, day, now, now, now,
                        item.get("underlying", ""), item.get("symbol", ""), item.get("option_type"),
                        item.get("strike"), item.get("expiry"), item.get("direction"), item.get("signal"),
                        item.get("total_score", 0.0), item.get("total_score", 0.0), item.get("premium"),
                        item.get("stop_premium"), item.get("target_premium"), item.get("breakeven"),
                        item.get("dte"), item.get("delta"), item.get("gamma"), item.get("theta"),
                        item.get("vega"), item.get("iv"), item.get("volume"), item.get("open_interest"),
                        "LIVE", item.get("reason"), json.dumps(item),
                    ),
                )
                written += cur.rowcount
            conn.execute(
                """
                UPDATE opportunity_history
                SET status='NO_LONGER_QUALIFIES'
                WHERE trading_day=%s
                  AND last_seen_at < %s
                  AND status='LIVE'
                """,
                (day, now),
            )
            conn.commit()
        return written

    def opportunity_history(self, day=None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        day = day or datetime.now(timezone.utc).date()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT opportunity_key, trading_day, first_seen_at, last_seen_at, last_qualified_at,
                       underlying, symbol, option_type, strike, expiry, direction, signal, score, peak_score,
                       premium, stop_premium, target_premium, breakeven, dte, delta, gamma, theta, vega, iv,
                       volume, open_interest, status, reason, payload
                FROM opportunity_history
                WHERE trading_day=%s
                ORDER BY last_seen_at DESC, score DESC
                LIMIT %s
                """, (day, limit)
            ).fetchall()
            columns = [d.name for d in conn.execute("SELECT * FROM opportunity_history LIMIT 0").description]
        result = []
        for row in rows:
            item = dict(zip(columns, row))
            for field in ("first_seen_at", "last_seen_at", "last_qualified_at"):
                if isinstance(item.get(field), datetime):
                    item[field] = item[field].isoformat()
            result.append(item)
        return result

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, signal_key, created_at, symbol, underlying, instrument_type,
                       expiry_date, strike_price, ltp, direction, bias, score, confidence,
                       ml_probability_up, ml_probability_down, event, evidence, payload
                FROM signals ORDER BY created_at DESC LIMIT %s
                """, (limit,)
            ).fetchall()
            columns = [d.name for d in conn.execute("SELECT * FROM signals LIMIT 0").description]
        result = []
        for row in rows:
            item = dict(zip(columns, row))
            if isinstance(item.get("created_at"), datetime):
                item["created_at"] = item["created_at"].isoformat()
            result.append(item)
        return result

    def price_action_history(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return persisted price-action setups and confirmations only."""
        limit = max(1, min(limit, 5000))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, signal_key, created_at, symbol, underlying, ltp,
                       direction, bias, score, confidence, event, evidence, payload
                FROM signals
                WHERE instrument_type='PRICE_ACTION'
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """, (limit,)
            ).fetchall()
            columns = [d.name for d in conn.execute(
                "SELECT id, signal_key, created_at, symbol, underlying, ltp, direction, bias, score, confidence, event, evidence, payload FROM signals LIMIT 0"
            ).description]
        result=[]
        for row in rows:
            item=dict(zip(columns,row))
            if isinstance(item.get("created_at"),datetime):
                item["created_at"]=item["created_at"].isoformat()
            result.append(item)
        return result

    def reversal_history(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return every persisted JFT reversal event, newest first."""
        limit = max(1, min(limit, 5000))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, signal_key, created_at, symbol, underlying, ltp,
                       direction, bias, score, confidence, event, evidence, payload
                FROM signals
                WHERE instrument_type='JFT'
                  AND (event='JFT_REVERSAL' OR payload->>'trigger'='REVERSAL')
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """, (limit,)
            ).fetchall()
            columns = [d.name for d in conn.execute(
                "SELECT id, signal_key, created_at, symbol, underlying, ltp, direction, bias, score, confidence, event, evidence, payload FROM signals LIMIT 0"
            ).description]
        result = []
        for row in rows:
            item = dict(zip(columns, row))
            if isinstance(item.get("created_at"), datetime):
                item["created_at"] = item["created_at"].isoformat()
            result.append(item)
        return result

    def count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0])


signal_store = SignalStore()
