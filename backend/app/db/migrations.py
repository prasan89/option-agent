from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


def _connect():
    import psycopg
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url)


def run_migrations() -> None:
    """Run idempotent, non-destructive schema upgrades on application startup."""
    with _connect() as conn:
        # Existing installations may have been created before these columns existed.
        conn.execute("""
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
        """)
        signal_columns = {
            "underlying": "TEXT",
            "instrument_type": "TEXT",
            "expiry_date": "TEXT",
            "strike_price": "DOUBLE PRECISION",
            "ltp": "DOUBLE PRECISION",
            "ml_probability_up": "DOUBLE PRECISION",
            "ml_probability_down": "DOUBLE PRECISION",
            "event": "TEXT",
            "evidence": "JSONB NOT NULL DEFAULT '[]'::jsonb",
            "payload": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        }
        for name, definition in signal_columns.items():
            conn.execute(f"ALTER TABLE signals ADD COLUMN IF NOT EXISTS {name} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_underlying ON signals(underlying)")

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
        observation_columns = {
            "underlying": "TEXT",
            "instrument_type": "TEXT",
            "expiry_date": "TEXT",
            "strike_price": "DOUBLE PRECISION",
            "ltp": "DOUBLE PRECISION",
            "change_pct_1m": "DOUBLE PRECISION",
            "activity_score": "DOUBLE PRECISION",
            "direction": "TEXT",
            "flow_event": "TEXT",
            "flow_score": "DOUBLE PRECISION",
            "intelligence_score": "DOUBLE PRECISION",
            "confidence": "TEXT",
            "bias": "TEXT",
            "price_change_pct": "DOUBLE PRECISION",
            "depth_imbalance": "DOUBLE PRECISION",
            "oi_change_pct": "DOUBLE PRECISION",
            "volume_change_pct": "DOUBLE PRECISION",
            "evidence": "JSONB NOT NULL DEFAULT '[]'::jsonb",
            "label_status": "TEXT NOT NULL DEFAULT 'UNLABELED'",
        }
        for name, definition in observation_columns.items():
            conn.execute(f"ALTER TABLE dataset_observations ADD COLUMN IF NOT EXISTS {name} {definition}")
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

        # Full signal/trade lifecycle journal. Existing data is preserved.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_journal (
                id BIGSERIAL PRIMARY KEY,
                signal_id TEXT NOT NULL UNIQUE,
                generated_at TIMESTAMPTZ NOT NULL,
                underlying TEXT, symbol TEXT, option_type TEXT,
                strike DOUBLE PRECISION, expiry TEXT, direction TEXT,
                score DOUBLE PRECISION, confidence DOUBLE PRECISION,
                entry_price DOUBLE PRECISION, stop_loss DOUBLE PRECISION,
                target_1 DOUBLE PRECISION, target_2 DOUBLE PRECISION,
                quantity INTEGER, risk_amount DOUBLE PRECISION,
                planned_reward DOUBLE PRECISION, rr DOUBLE PRECISION,
                status TEXT NOT NULL DEFAULT 'GENERATED',
                entry_at TIMESTAMPTZ, exit_at TIMESTAMPTZ,
                exit_price DOUBLE PRECISION, exit_reason TEXT,
                realized_pnl DOUBLE PRECISION, pnl_r DOUBLE PRECISION,
                max_favorable_excursion DOUBLE PRECISION,
                max_adverse_excursion DOUBLE PRECISION, bars_held INTEGER,
                data_quality TEXT, evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_generated ON signal_journal(generated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_status ON signal_journal(status)")
        conn.commit()
        logger.info("Database schema migrations completed successfully")
