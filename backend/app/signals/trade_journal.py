from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import psycopg

from app.core.config import settings


class TradeJournal:
    """Durable signal lifecycle and paper-trade journal.

    Execution is intentionally excluded. The journal captures generated signals,
    planned entry/SL/targets, lifecycle timestamps and realized paper P&L.
    """

    def __init__(self) -> None:
        self.database_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect(self):
        return psycopg.connect(self.database_url)

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_journal (
                    id BIGSERIAL PRIMARY KEY,
                    signal_id TEXT NOT NULL UNIQUE,
                    generated_at TIMESTAMPTZ NOT NULL,
                    underlying TEXT,
                    symbol TEXT,
                    option_type TEXT,
                    strike DOUBLE PRECISION,
                    expiry TEXT,
                    direction TEXT,
                    score DOUBLE PRECISION,
                    confidence DOUBLE PRECISION,
                    entry_price DOUBLE PRECISION,
                    stop_loss DOUBLE PRECISION,
                    target_1 DOUBLE PRECISION,
                    target_2 DOUBLE PRECISION,
                    quantity INTEGER,
                    risk_amount DOUBLE PRECISION,
                    planned_reward DOUBLE PRECISION,
                    rr DOUBLE PRECISION,
                    status TEXT NOT NULL DEFAULT 'GENERATED',
                    entry_at TIMESTAMPTZ,
                    exit_at TIMESTAMPTZ,
                    exit_price DOUBLE PRECISION,
                    exit_reason TEXT,
                    realized_pnl DOUBLE PRECISION,
                    pnl_r DOUBLE PRECISION,
                    max_favorable_excursion DOUBLE PRECISION,
                    max_adverse_excursion DOUBLE PRECISION,
                    bars_held INTEGER,
                    data_quality TEXT,
                    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_generated ON signal_journal(generated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_status ON signal_journal(status)")
            conn.commit()

    def record_signal(self, signal: dict[str, Any]) -> bool:
        generated = signal.get("generated_at") or signal.get("created_at") or datetime.now(timezone.utc)
        entry = float(signal.get("entry_price", signal.get("premium", 0)) or 0)
        stop = float(signal.get("stop_loss", signal.get("stop_premium", 0)) or 0)
        target1 = float(signal.get("target_1", signal.get("target_premium", 0)) or 0)
        target2 = float(signal.get("target_2", target1) or target1)
        risk = abs(entry - stop) * int(signal.get("quantity", 0) or 0)
        reward = max(0, target2 - entry) * int(signal.get("quantity", 0) or 0)
        rr = reward / risk if risk else 0
        with self.connect() as conn:
            cur = conn.execute("""
                INSERT INTO signal_journal
                (signal_id, generated_at, underlying, symbol, option_type, strike, expiry,
                 direction, score, confidence, entry_price, stop_loss, target_1, target_2,
                 quantity, risk_amount, planned_reward, rr, data_quality, evidence, metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (signal_id) DO NOTHING
            """, (
                str(signal.get("signal_id") or signal.get("signal_key") or f"{signal.get('symbol')}:{generated}"),
                generated, signal.get("underlying"), signal.get("symbol"), signal.get("option_type"),
                signal.get("strike"), signal.get("expiry"), signal.get("direction"), signal.get("score", signal.get("total_score")),
                signal.get("confidence"), entry, stop, target1, target2, signal.get("quantity", 0), risk, reward, rr,
                signal.get("data_quality"), json.dumps(signal.get("evidence", [])), json.dumps(signal),
            ))
            conn.commit()
            return cur.rowcount == 1

    def update_trade(self, signal_id: str, **fields: Any) -> bool:
        allowed = {"status","entry_at","entry_price","exit_at","exit_price","exit_reason","realized_pnl","pnl_r","max_favorable_excursion","max_adverse_excursion","bars_held"}
        updates = [(k, fields[k]) for k in fields if k in allowed]
        if not updates:
            return False
        set_sql = ", ".join(f"{k}=%s" for k, _ in updates)
        with self.connect() as conn:
            cur = conn.execute(f"UPDATE signal_journal SET {set_sql} WHERE signal_id=%s", [v for _, v in updates] + [signal_id])
            conn.commit()
            return cur.rowcount == 1

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM signal_journal ORDER BY generated_at DESC LIMIT %s", (max(1, min(limit, 500)),)).fetchall()
            cols = [d.name for d in conn.execute("SELECT * FROM signal_journal LIMIT 0").description]
        result=[]
        for row in rows:
            item=dict(zip(cols,row))
            for k,v in list(item.items()):
                if isinstance(v, datetime): item[k]=v.isoformat()
            result.append(item)
        return result

    def metrics(self) -> dict[str, Any]:
        with self.connect() as conn:
            total, wins, losses, open_count, pnl, avg_win, avg_loss = conn.execute("""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE realized_pnl > 0), COUNT(*) FILTER (WHERE realized_pnl < 0),
                       COUNT(*) FILTER (WHERE status IN ('GENERATED','OPEN','TARGET1_HIT')), COALESCE(SUM(realized_pnl),0),
                       COALESCE(AVG(realized_pnl) FILTER (WHERE realized_pnl > 0),0),
                       COALESCE(AVG(realized_pnl) FILTER (WHERE realized_pnl < 0),0)
                FROM signal_journal
            """).fetchone()
            closed = wins + losses
            gross_win = conn.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM signal_journal WHERE realized_pnl > 0").fetchone()[0]
            gross_loss = abs(conn.execute("SELECT COALESCE(SUM(realized_pnl),0) FROM signal_journal WHERE realized_pnl < 0").fetchone()[0])
            best = conn.execute("SELECT COALESCE(MAX(realized_pnl),0) FROM signal_journal").fetchone()[0]
            worst = conn.execute("SELECT COALESCE(MIN(realized_pnl),0) FROM signal_journal").fetchone()[0]
            return {
                "signals_total": total, "closed": closed, "open": open_count, "wins": wins, "losses": losses,
                "win_rate_pct": round(wins / closed * 100, 2) if closed else 0,
                "realized_pnl": round(float(pnl),2), "avg_win": round(float(avg_win),2), "avg_loss": round(float(avg_loss),2),
                "profit_factor": round(float(gross_win) / float(gross_loss), 3) if gross_loss else None,
                "best_trade": float(best), "worst_trade": float(worst),
            }


trade_journal = TradeJournal()
