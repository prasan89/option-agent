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
            "signal_key": "TEXT",
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
        # Repair legacy databases created before signal_key was introduced.
        # Existing rows receive deterministic keys so newer history/persistence
        # queries work without destructive table recreation.
        conn.execute(
            "UPDATE signals SET signal_key = 'LEGACY:' || id::text "
            "WHERE signal_key IS NULL OR btrim(signal_key) = ''"
        )
        conn.execute("ALTER TABLE signals ALTER COLUMN signal_key SET NOT NULL")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_signal_key_unique ON signals(signal_key)")
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

        # A candidate that qualifies once must remain auditable for the whole
        # trading day even if the live score later drops below the threshold.
        conn.execute("""
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
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_history_day ON opportunity_history(trading_day, last_seen_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_opp_history_underlying ON opportunity_history(underlying, trading_day)")

        conn.execute("CREATE TABLE IF NOT EXISTS paper_signal_tracker (id BIGSERIAL PRIMARY KEY, signal_key TEXT NOT NULL UNIQUE, generated_at TIMESTAMPTZ NOT NULL, last_observed_at TIMESTAMPTZ NOT NULL, underlying TEXT NOT NULL, symbol TEXT NOT NULL, option_type TEXT, strike DOUBLE PRECISION, expiry TEXT, direction TEXT, signal TEXT, score DOUBLE PRECISION, entry_price DOUBLE PRECISION NOT NULL, current_price DOUBLE PRECISION NOT NULL, stop_price DOUBLE PRECISION, target_price DOUBLE PRECISION, quantity INTEGER NOT NULL DEFAULT 0, realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0, mark_pnl DOUBLE PRECISION NOT NULL DEFAULT 0, pnl_pct DOUBLE PRECISION NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'OPEN', outcome_reason TEXT, metadata JSONB NOT NULL DEFAULT '{}'::jsonb)")

        conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS entry_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS exit_at TIMESTAMPTZ")
        conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION")
        conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS bars_held INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS mfe_pct DOUBLE PRECISION NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS mae_pct DOUBLE PRECISION NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_tracker_generated ON paper_signal_tracker(generated_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_tracker_status ON paper_signal_tracker(status)")

        conn.commit()
        logger.info("Database schema migrations completed successfully")
