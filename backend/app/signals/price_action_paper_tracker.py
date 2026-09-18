from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from app.core.config import settings

IST = ZoneInfo("Asia/Kolkata")


class PriceActionPaperTracker:
    """Paper P&L tracker for underlying price-action signals. No orders."""

    def __init__(self) -> None:
        self.database_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect(self):
        return psycopg.connect(self.database_url)

    def init(self) -> None:
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_action_paper_tracker (
                    id BIGSERIAL PRIMARY KEY,
                    signal_key TEXT NOT NULL UNIQUE,
                    generated_at TIMESTAMPTZ NOT NULL,
                    last_observed_at TIMESTAMPTZ NOT NULL,
                    entry_at TIMESTAMPTZ NOT NULL,
                    exit_at TIMESTAMPTZ,
                    exit_price DOUBLE PRECISION,
                    underlying TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score DOUBLE PRECISION,
                    entry_price DOUBLE PRECISION NOT NULL,
                    current_price DOUBLE PRECISION NOT NULL,
                    stop_price DOUBLE PRECISION,
                    target_price DOUBLE PRECISION,
                    realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                    mark_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
                    pnl_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    outcome_reason TEXT,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pa_paper_generated ON price_action_paper_tracker(generated_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pa_paper_status ON price_action_paper_tracker(status)")
            conn.commit()

    @staticmethod
    def _num(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _dt(v: Any) -> datetime | None:
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if isinstance(v, (int, float)):
            x = float(v)
            return datetime.fromtimestamp(x / (1000 if x > 10_000_000_000 else 1), tz=timezone.utc)
        if v:
            try:
                d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    def record_signal(self, item: dict[str, Any]) -> None:
        generated = self._dt(item.get("created_at") or item.get("time")) or datetime.now(timezone.utc)
        symbol = str(item.get("underlying") or item.get("symbol") or "").upper()
        pattern = str(item.get("pattern") or "")
        supported = {"BULLISH ENGULFING","BEARISH ENGULFING","BULLISH HARAMI","BEARISH HARAMI","PIERCING","DARK CLOUD COVER","MORNING STAR","EVENING STAR","THREE WHITE SOLDIERS","THREE BLACK CROWS"}
        if pattern not in supported:
            return
        direction = str(item.get("signal") or "").upper()
        entry = self._num(item.get("price") or item.get("close_15min"))
        if not symbol or not pattern or direction not in {"BUY", "SELL"} or entry <= 0:
            return
        key = f"{generated.isoformat()}:{symbol}:{pattern}:{direction}"
        stop = self._num(item.get("stop_loss")) or None
        target = self._num(item.get("target")) or None
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO price_action_paper_tracker
                (signal_key,generated_at,last_observed_at,entry_at,underlying,pattern,direction,score,
                 entry_price,current_price,stop_price,target_price,status,outcome_reason,metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',
                        'Signal recorded; tracking subsequent 15M underlying candles.',%s)
                ON CONFLICT (signal_key) DO NOTHING
            """, (key, generated, generated, generated, symbol, pattern, direction,
                  item.get("score"), entry, entry, stop, target, Jsonb(item)))
            conn.commit()

    def evaluate_open(self) -> int:
        from app.services.groww_client import groww_client
        now = datetime.now(timezone.utc)
        updated = 0
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM price_action_paper_tracker WHERE status='OPEN' ORDER BY generated_at ASC LIMIT 2000").fetchall()
            cols = [d.name for d in conn.execute("SELECT * FROM price_action_paper_tracker LIMIT 0").description]

        for raw in rows:
            item = dict(zip(cols, raw))
            generated = item.get("generated_at")
            if not isinstance(generated, datetime):
                continue
            try:
                start = generated.astimezone(IST)
                end = now.astimezone(IST)
                payload = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                symbol = str(item["underlying"])
                candles_payload = groww_client.historical_candles(
                    str(payload.get("groww_symbol") or f"NSE-{symbol}"),
                    start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"),
                    "CASH", "15minute")
                candles = candles_payload.get("candles", []) if isinstance(candles_payload, dict) else []
                entry = self._num(item.get("entry_price"))
                stop = self._num(item.get("stop_price")) or None
                target = self._num(item.get("target_price")) or None
                direction = str(item["direction"]).upper()
                latest = None
                hit = None
                exit_price = None
                exit_at = None

                for candle in candles:
                    if not isinstance(candle, (list, tuple)) or len(candle) < 5:
                        continue
                    try:
                        ts = str(candle[0]); high = float(candle[2]); low = float(candle[3]); close = float(candle[4])
                    except (TypeError, ValueError):
                        continue
                    dt = self._dt(ts)
                    if dt is None or dt <= generated:
                        continue
                    latest = close
                    target_hit = target is not None and (high >= target if direction == "BUY" else low <= target)
                    stop_hit = stop is not None and (low <= stop if direction == "BUY" else high >= stop)
                    if target_hit or stop_hit:
                        hit = "STOP_HIT" if stop_hit else "TARGET_HIT"
                        exit_price = stop if stop_hit else target
                        exit_at = dt
                        break

                if latest is None:
                    continue
                favorable = (latest-entry) if direction == "BUY" else (entry-latest)
                pnl = favorable
                pnl_pct = favorable / entry * 100 if entry else 0
                market_close = end.time().replace(tzinfo=None) >= __import__("datetime").time(15, 40)
                if hit:
                    status = hit
                    realized = pnl = ((float(exit_price)-entry) if direction == "BUY" else (entry-float(exit_price)))
                    reason = "Target reached by subsequent 15M candle." if hit == "TARGET_HIT" else "Stop reached by subsequent 15M candle."
                    exit_at = exit_at
                    exit_value = float(exit_price)
                    pnl_pct = ((exit_value-entry) if direction == "BUY" else (entry-exit_value)) / entry * 100 if entry else 0
                elif market_close:
                    status = "PROFIT" if favorable > 0 else "LOSS" if favorable < 0 else "FLAT"
                    realized = pnl
                    exit_price = latest
                    exit_at = dt
                    reason = "End-of-session paper exit at the last available 15M close."
                else:
                    status = "OPEN_PROFIT" if favorable > 0 else "OPEN_LOSS" if favorable < 0 else "OPEN"
                    realized = 0.0
                    reason = "No target/stop hit yet; current mark is shown as paper profit/loss."

                with self.connect() as conn:
                    conn.execute("""
                        UPDATE price_action_paper_tracker
                        SET last_observed_at=%s,current_price=%s,exit_at=%s,exit_price=%s,
                            realized_pnl=%s,mark_pnl=%s,pnl_pct=%s,status=%s,outcome_reason=%s
                        WHERE signal_key=%s
                    """, (now, latest, exit_at, exit_price, realized, pnl, pnl_pct, status, reason, item["signal_key"]))
                    conn.commit()
                updated += 1
            except Exception:
                continue
        return updated

    def rows(self, limit: int = 500) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 2000))
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM price_action_paper_tracker ORDER BY generated_at DESC, id DESC LIMIT %s", (limit,)).fetchall()
            cols = [d.name for d in conn.execute("SELECT * FROM price_action_paper_tracker LIMIT 0").description]
        result = []
        for row in rows:
            item = dict(zip(cols, row))
            for k, v in list(item.items()):
                if isinstance(v, datetime):
                    item[k] = v.isoformat()
            result.append(item)
        return result

    def metrics(self) -> dict[str, Any]:
        with self.connect() as conn:
            total, profit, loss, open_count, target, stop, pnl = conn.execute("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE status IN ('PROFIT','OPEN_PROFIT')),
                       COUNT(*) FILTER (WHERE status IN ('LOSS','OPEN_LOSS')),
                       COUNT(*) FILTER (WHERE status LIKE 'OPEN%'),
                       COUNT(*) FILTER (WHERE status='TARGET_HIT'),
                       COUNT(*) FILTER (WHERE status='STOP_HIT'),
                       COALESCE(SUM(mark_pnl),0)
                FROM price_action_paper_tracker
            """).fetchone()
        closed = profit + loss + target + stop
        wins = profit + target
        return {"signals":total,"profit":profit,"loss":loss,"open":open_count,"target_hit":target,
                "stop_hit":stop,"closed":closed,"wins":wins,
                "win_rate_pct":round(wins/closed*100,2) if closed else 0.0,
                "mark_to_market_pnl":round(float(pnl),2),"research_only":True,"trading":"DISABLED"}


price_action_paper_tracker = PriceActionPaperTracker()
