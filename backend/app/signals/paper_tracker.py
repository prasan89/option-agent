from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg

from app.core.config import settings


class PaperSignalTracker:
    """Persistent paper P&L tracker for generated option signals.

    No broker orders are placed. Entry is the first observed option premium;
    subsequent observations update mark-to-market P&L and target/stop status.
    """

    def __init__(self) -> None:
        self.database_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect(self):
        return psycopg.connect(self.database_url)

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_signal_tracker (
                    id BIGSERIAL PRIMARY KEY,
                    signal_key TEXT NOT NULL UNIQUE,
                    generated_at TIMESTAMPTZ NOT NULL,
                    last_observed_at TIMESTAMPTZ NOT NULL,
                    underlying TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    option_type TEXT,
                    strike DOUBLE PRECISION,
                    expiry TEXT,
                    direction TEXT,
                    signal TEXT,
                    score DOUBLE PRECISION,
                    entry_price DOUBLE PRECISION NOT NULL,
                    current_price DOUBLE PRECISION NOT NULL,
                    stop_price DOUBLE PRECISION,
                    target_price DOUBLE PRECISION,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                    mark_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                    pnl_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    outcome_reason TEXT,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_tracker_generated ON paper_signal_tracker(generated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_paper_tracker_status ON paper_signal_tracker(status)")
            conn.commit()

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _quantity(item: dict[str, Any]) -> int:
        try:
            return max(1, int(item.get("quantity") or 1))
        except (TypeError, ValueError):
            return 1

    def record_candidates(self, candidates: list[dict[str, Any]], generated_at: Any = None) -> int:
        if not candidates:
            return 0
        now = datetime.now(timezone.utc)
        generated = generated_at or now
        if isinstance(generated, str):
            try:
                generated = datetime.fromisoformat(generated.replace("Z", "+00:00"))
            except ValueError:
                generated = now
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)

        written = 0
        with self.connect() as conn:
            for item in candidates:
                premium = self._num(item.get("premium"))
                if premium <= 0:
                    continue
                symbol = str(item.get("symbol") or "")
                underlying = str(item.get("underlying") or "")
                option_type = str(item.get("option_type") or "").upper()
                strike = item.get("strike")
                expiry = str(item.get("expiry") or "")
                signal = str(item.get("signal") or "")
                if not symbol or not underlying:
                    continue
                key = f"{generated.date()}:{symbol}:{option_type}:{strike}:{expiry}:{signal}"
                quantity = self._quantity(item)
                stop = self._num(item.get("stop_premium")) or None
                target = self._num(item.get("target_premium")) or None

                existing = conn.execute(
                    "SELECT entry_price, quantity, stop_price, target_price, status FROM paper_signal_tracker WHERE signal_key=%s",
                    (key,),
                ).fetchone()

                if existing:
                    entry, old_qty, old_stop, old_target, old_status = existing
                    entry = self._num(entry)
                    qty = int(old_qty or quantity)
                    current = premium
                    pnl = (current - entry) * qty
                    pnl_pct = ((current - entry) / entry * 100.0) if entry else 0.0
                    status = old_status
                    reason = None
                    if status not in {"TARGET_HIT", "STOP_HIT"}:
                        if target and current >= target:
                            status, reason = "TARGET_HIT", "Observed premium reached/exceeded the planned target."
                        elif stop and current <= stop:
                            status, reason = "STOP_HIT", "Observed premium reached/fell below the planned stop."
                        elif pnl > 0:
                            status = "PROFIT"
                        elif pnl < 0:
                            status = "LOSS"
                        else:
                            status = "OPEN"
                    conn.execute(
                        """UPDATE paper_signal_tracker
                           SET last_observed_at=%s,current_price=%s,mark_pnl=%s,pnl_pct=%s,status=%s,
                               outcome_reason=COALESCE(%s,outcome_reason),metadata=%s
                           WHERE signal_key=%s""",
                        (now, current, pnl, pnl_pct, status, reason, psycopg.types.json.Jsonb(item), key),
                    )
                    written += 1
                    continue

                pnl = 0.0
                conn.execute(
                    """INSERT INTO paper_signal_tracker
                       (signal_key,generated_at,last_observed_at,underlying,symbol,option_type,strike,expiry,
                        direction,signal,score,entry_price,current_price,stop_price,target_price,quantity,
                        mark_pnl,pnl_pct,status,outcome_reason,metadata)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,%s)
                    """,
                    (key, generated, now, underlying, symbol, option_type, strike, expiry,
                     item.get("direction"), signal, item.get("total_score", item.get("score")),
                     premium, premium, stop, target, quantity, pnl, 0.0,
                     "Signal recorded; waiting for the next observed option premium.",
                     psycopg.types.json.Jsonb(item)),
                )
                written += 1
            conn.commit()
        return written

    def rows(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 2000))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_signal_tracker ORDER BY generated_at DESC, id DESC LIMIT %s",
                (limit,),
            ).fetchall()
            cols = [d.name for d in conn.execute("SELECT * FROM paper_signal_tracker LIMIT 0").description]
        result = []
        for row in rows:
            item = dict(zip(cols, row))
            for key, value in list(item.items()):
                if isinstance(value, datetime):
                    item[key] = value.isoformat()
            result.append(item)
        return result

    def metrics(self) -> dict[str, Any]:
        with self.connect() as conn:
            total, profit, loss, open_count, target, stop, pnl = conn.execute("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE status='PROFIT'),
                       COUNT(*) FILTER (WHERE status='LOSS'),
                       COUNT(*) FILTER (WHERE status='OPEN'),
                       COUNT(*) FILTER (WHERE status='TARGET_HIT'),
                       COUNT(*) FILTER (WHERE status='STOP_HIT'),
                       COALESCE(SUM(mark_pnl),0)
                FROM paper_signal_tracker
            """).fetchone()
        closed = profit + loss + target + stop
        wins = profit + target
        return {
            "signals": total,
            "profit": profit,
            "loss": loss,
            "open": open_count,
            "target_hit": target,
            "stop_hit": stop,
            "closed": closed,
            "wins": wins,
            "win_rate_pct": round(wins / closed * 100, 2) if closed else 0.0,
            "mark_to_market_pnl": round(float(pnl), 2),
            "research_only": True,
            "trading": "DISABLED",
        }


paper_signal_tracker = PaperSignalTracker()
