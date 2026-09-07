from __future__ import annotations

import json
from typing import Any

from app.core.config import settings


class ResearchStore:
    """PostgreSQL persistence for durable research data when Redis is unavailable."""

    def __init__(self) -> None:
        self.database_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect(self):
        import psycopg
        return psycopg.connect(self.database_url)

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dataset_observations (
                    id BIGSERIAL PRIMARY KEY, observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    timestamp_ms BIGINT, source TEXT NOT NULL, symbol TEXT NOT NULL,
                    underlying TEXT, instrument_type TEXT, expiry_date TEXT,
                    strike_price DOUBLE PRECISION, ltp DOUBLE PRECISION,
                    change_pct_1m DOUBLE PRECISION, activity_score DOUBLE PRECISION,
                    direction TEXT, flow_event TEXT, flow_score DOUBLE PRECISION,
                    intelligence_score DOUBLE PRECISION, confidence TEXT, bias TEXT,
                    price_change_pct DOUBLE PRECISION, depth_imbalance DOUBLE PRECISION,
                    oi_change_pct DOUBLE PRECISION, volume_change_pct DOUBLE PRECISION,
                    evidence JSONB NOT NULL DEFAULT '[]'::jsonb, label_status TEXT NOT NULL DEFAULT 'UNLABELED',
                    UNIQUE(symbol, timestamp_ms, source)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dataset_symbol_ts ON dataset_observations(symbol, timestamp_ms)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dataset_underlying_ts ON dataset_observations(underlying, timestamp_ms)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dataset_labels (
                    id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL, timestamp_ms BIGINT NOT NULL,
                    horizon_minutes INTEGER NOT NULL, entry_price DOUBLE PRECISION NOT NULL,
                    future_price DOUBLE PRECISION NOT NULL, return_pct DOUBLE PRECISION NOT NULL,
                    direction TEXT NOT NULL, future_timestamp_ms BIGINT NOT NULL,
                    features JSONB NOT NULL, label INTEGER NOT NULL,
                    UNIQUE(symbol, timestamp_ms, horizon_minutes)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_labels_ts ON dataset_labels(timestamp_ms)")
            conn.commit()

    @staticmethod
    def _observation_values(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row.get("timestamp_ms"), row.get("source", "unknown"), row.get("symbol", ""), row.get("underlying"),
            row.get("instrument_type"), row.get("expiry_date"), row.get("strike_price"), row.get("ltp"),
            row.get("change_pct_1m"), row.get("activity_score"), row.get("direction"), row.get("flow_event"),
            row.get("flow_score"), row.get("intelligence_score"), row.get("confidence"), row.get("bias"),
            row.get("price_change_pct"), row.get("depth_imbalance"), row.get("oi_change_pct"), row.get("volume_change_pct"),
            json.dumps(row.get("evidence", [])),
        )

    def insert_observation(self, row: dict[str, Any]) -> bool:
        return self.insert_observations([row]) > 0

    def insert_observations(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        with self.connect() as conn:
            inserted = 0
            for row in rows:
                cur = conn.execute("""
                    INSERT INTO dataset_observations
                    (timestamp_ms, source, symbol, underlying, instrument_type, expiry_date, strike_price, ltp,
                     change_pct_1m, activity_score, direction, flow_event, flow_score, intelligence_score,
                     confidence, bias, price_change_pct, depth_imbalance, oi_change_pct, volume_change_pct, evidence)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (symbol, timestamp_ms, source) DO NOTHING
                """, self._observation_values(row))
                inserted += max(0, cur.rowcount)
            conn.commit()
            return inserted

    @staticmethod
    def _label_values(label: dict[str, Any]) -> tuple[Any, ...]:
        return (
            label["symbol"], label["timestamp_ms"], label["horizon_minutes"], label["entry_price"], label["future_price"],
            label["return_pct"], label["direction"], label["future_timestamp_ms"], json.dumps(label.get("features", {})), label["label"],
        )

    def insert_label(self, label: dict[str, Any]) -> bool:
        return self.insert_labels([label]) > 0

    def insert_labels(self, labels: list[dict[str, Any]]) -> int:
        if not labels:
            return 0
        with self.connect() as conn:
            inserted = 0
            for label in labels:
                cur = conn.execute("""
                    INSERT INTO dataset_labels
                    (symbol,timestamp_ms,horizon_minutes,entry_price,future_price,return_pct,direction,future_timestamp_ms,features,label)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (symbol,timestamp_ms,horizon_minutes) DO NOTHING
                """, self._label_values(label))
                inserted += max(0, cur.rowcount)
            conn.commit()
            return inserted

    def labels(self, horizon: int = 5, limit: int = 1_000_000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT symbol,timestamp_ms,horizon_minutes,entry_price,future_price,return_pct,direction,
                       future_timestamp_ms,features,label
                FROM dataset_labels WHERE horizon_minutes=%s ORDER BY timestamp_ms ASC LIMIT %s
            """, (horizon, limit)).fetchall()
        keys = ("symbol","timestamp_ms","horizon_minutes","entry_price","future_price","return_pct","direction","future_timestamp_ms","features","label")
        return [dict(zip(keys, row)) for row in rows]

    def recent_observations(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM dataset_observations ORDER BY observed_at DESC LIMIT %s", (max(1, min(limit, 500)),)).fetchall()
            keys = [d.name for d in conn.execute("SELECT * FROM dataset_observations LIMIT 0").description]
        return [dict(zip(keys, row)) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            return {
                "observations": int(conn.execute("SELECT COUNT(*) FROM dataset_observations").fetchone()[0]),
                "labels": int(conn.execute("SELECT COUNT(*) FROM dataset_labels").fetchone()[0]),
            }


research_store = ResearchStore()
