from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg

from app.core.config import settings


class SignalStore:
    """Small PostgreSQL persistence layer for research signals."""

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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_underlying ON signals(underlying)")
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

    def count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0])


signal_store = SignalStore()
