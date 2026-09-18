from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

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
                    entry_at TIMESTAMPTZ,
                    exit_at TIMESTAMPTZ,
                    exit_price DOUBLE PRECISION,
                    bars_held INTEGER NOT NULL DEFAULT 0,
                    mfe_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
                    mae_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
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
            conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS entry_at TIMESTAMPTZ")
            conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS exit_at TIMESTAMPTZ")
            conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION")
            conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS bars_held INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS mfe_pct DOUBLE PRECISION NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE paper_signal_tracker ADD COLUMN IF NOT EXISTS mae_pct DOUBLE PRECISION NOT NULL DEFAULT 0")
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
        if isinstance(generated, (int, float)):
            epoch = float(generated) / (1000.0 if float(generated) > 10_000_000_000 else 1.0)
            generated = datetime.fromtimestamp(epoch, tz=timezone.utc)
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
                item_generated = item.get("signal_generated_at") or generated
                if isinstance(item_generated, (int, float)):
                    epoch = float(item_generated) / (1000.0 if float(item_generated) > 10_000_000_000 else 1.0)
                    item_generated = datetime.fromtimestamp(epoch, tz=timezone.utc)
                elif isinstance(item_generated, str):
                    try:
                        item_generated = datetime.fromisoformat(item_generated.replace("Z", "+00:00"))
                    except ValueError:
                        item_generated = generated
                if item_generated.tzinfo is None:
                    item_generated = item_generated.replace(tzinfo=timezone.utc)
                generated_key = item_generated.isoformat()
                key = f"{generated_key}:{symbol}:{option_type}:{strike}:{expiry}:{signal}"
                quantity = self._quantity(item)
                stop = self._num(item.get("stop_premium")) or None
                target = self._num(item.get("target_premium")) or None

                existing = conn.execute(
                    "SELECT entry_price, quantity, stop_price, target_price, status, entry_at, exit_at, exit_price, bars_held, mfe_pct, mae_pct FROM paper_signal_tracker WHERE signal_key=%s",
                    (key,),
                ).fetchone()

                if existing:
                    entry, old_qty, old_stop, old_target, old_status, old_entry_at, old_exit_at, old_exit_price, old_bars, old_mfe, old_mae = existing
                    entry = self._num(entry)
                    qty = int(old_qty or quantity)
                    current = premium
                    pnl = (current - entry) * qty
                    pnl_pct = ((current - entry) / entry * 100.0) if entry else 0.0
                    status = old_status
                    reason = None
                    exit_at = old_exit_at
                    exit_price = old_exit_price
                    bars_held = int(old_bars or 0)
                    mfe_pct = float(old_mfe or 0)
                    mae_pct = float(old_mae or 0)
                    if status not in {"TARGET_HIT", "STOP_HIT"}:
                        if target and current >= target:
                            status, reason = "TARGET_HIT", "Observed premium reached/exceeded the planned target."
                            exit_at, exit_price = now, target
                        elif stop and current <= stop:
                            status, reason = "STOP_HIT", "Observed premium reached/fell below the planned stop."
                            exit_at, exit_price = now, stop
                        elif pnl > 0:
                            status = "PROFIT"
                        elif pnl < 0:
                            status = "LOSS"
                        else:
                            status = "OPEN"
                    conn.execute(
                        """UPDATE paper_signal_tracker
                           SET last_observed_at=%s,current_price=%s,mark_pnl=%s,pnl_pct=%s,status=%s,
                               exit_at=%s,exit_price=%s,bars_held=%s,mfe_pct=%s,mae_pct=%s,
                               outcome_reason=COALESCE(%s,outcome_reason),metadata=%s
                           WHERE signal_key=%s""",
                        (now, current, pnl, pnl_pct, status, exit_at, exit_price, bars_held, mfe_pct, mae_pct, reason, Jsonb(item), key),
                    )
                    written += 1
                    continue

                pnl = 0.0
                conn.execute(
                    """INSERT INTO paper_signal_tracker
                       (signal_key,generated_at,last_observed_at,underlying,symbol,option_type,strike,expiry,
                        direction,signal,score,entry_price,current_price,stop_price,target_price,quantity,entry_at,
                        mark_pnl,pnl_pct,status,outcome_reason,metadata)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,%s)
                    """,
                    (key, item_generated, now, underlying, symbol, option_type, strike, expiry,
                     item.get("direction"), signal, item.get("total_score", item.get("score")),
                     premium, premium, stop, target, quantity, pnl, 0.0, item_generated,
                     "Signal recorded; waiting for subsequent option candles to determine target/stop outcome.",
                     Jsonb(item), item_generated),
                )
                written += 1
            conn.commit()
        return written

    def evaluate_open(self) -> int:
        """Evaluate open paper signals against subsequent 5-minute option candles.

        This is a research backtest/mark-to-market only. When target and stop are
        both inside the same candle, the stop is chosen conservatively because
        candle OHLC cannot establish the intrabar execution order.
        """
        from zoneinfo import ZoneInfo
        from app.services.groww_client import groww_client
        IST = ZoneInfo("Asia/Kolkata")
        now = datetime.now(timezone.utc)
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM paper_signal_tracker WHERE status='OPEN' ORDER BY generated_at ASC LIMIT 2000").fetchall()
            cols = [d.name for d in conn.execute("SELECT * FROM paper_signal_tracker LIMIT 0").description]
        updated = 0
        for raw in rows:
            item = dict(zip(cols, raw))
            generated = item.get("generated_at")
            if not isinstance(generated, datetime) or not item.get("symbol"):
                continue
            try:
                start = generated.astimezone(IST)
                end = now.astimezone(IST)
                if end <= start:
                    continue
                payload = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                groww_symbol = str(payload.get("groww_symbol") or f"NSE-{item['symbol']}")
                candles_payload = groww_client.historical_candles(
                    groww_symbol, start.strftime("%Y-%m-%d %H:%M:%S"),
                    end.strftime("%Y-%m-%d %H:%M:%S"), "FNO", "5minute"
                )
                candles = candles_payload.get("candles", []) if isinstance(candles_payload, dict) else []
                entry = self._num(item.get("entry_price"))
                target = self._num(item.get("target_price")) or None
                stop = self._num(item.get("stop_price")) or None
                qty = int(item.get("quantity") or 1)
                latest = None; hit = None; exit_price = None; exit_at = None; bars = 0
                mfe = 0.0; mae = 0.0
                for candle in candles:
                    if not isinstance(candle, (list, tuple)) or len(candle) < 5:
                        continue
                    try:
                        ts = str(candle[0]); high = float(candle[2]); low = float(candle[3]); close = float(candle[4])
                    except (TypeError, ValueError):
                        continue
                    dt = self._candle_datetime(ts, IST)
                    if dt is None or dt <= generated.astimezone(IST):
                        continue
                    bars += 1; latest = close
                    if entry > 0:
                        mfe = max(mfe, (high-entry)/entry*100.0)
                        mae = min(mae, (low-entry)/entry*100.0)
                    target_hit = target is not None and high >= target
                    stop_hit = stop is not None and low <= stop
                    if target_hit or stop_hit:
                        hit = "STOP_HIT" if stop_hit else "TARGET_HIT"
                        exit_price = stop if stop_hit else target
                        exit_at = dt
                        break
                if latest is None:
                    continue
                status = hit or ("OPEN_PROFIT" if latest > entry else "OPEN_LOSS" if latest < entry else "OPEN")
                mark_pnl = (latest-entry)*qty
                reason = "Target reached by subsequent 5M candle." if hit == "TARGET_HIT" else "Stop reached by subsequent 5M candle." if hit == "STOP_HIT" else "No target/stop hit yet; current mark is shown as paper profit/loss."
                if hit:
                    mark_pnl = (exit_price-entry)*qty
                with self.connect() as conn:
                    conn.execute("""UPDATE paper_signal_tracker SET last_observed_at=%s,current_price=%s,mark_pnl=%s,pnl_pct=%s,status=%s,exit_at=%s,exit_price=%s,bars_held=%s,mfe_pct=%s,mae_pct=%s,outcome_reason=%s WHERE signal_key=%s""",
                        (now, latest, mark_pnl, ((latest-entry)/entry*100.0) if entry else 0.0, status, exit_at, exit_price, bars, mfe, mae, reason, item["signal_key"]))
                    conn.commit()
                updated += 1
            except Exception:
                continue
        return updated

    @staticmethod
    def _candle_datetime(ts: str, ist) -> datetime | None:
        try:
            value = str(ts).strip()
            if value.isdigit():
                epoch = float(value) / (1000.0 if float(value) > 10_000_000_000 else 1.0)
                return datetime.fromtimestamp(epoch, tz=ist)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=ist)
            return parsed.astimezone(ist)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

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
                       COUNT(*) FILTER (WHERE status IN ('PROFIT','OPEN_PROFIT')),
                       COUNT(*) FILTER (WHERE status IN ('LOSS','OPEN_LOSS')),
                       COUNT(*) FILTER (WHERE status LIKE 'OPEN%'),
                       COUNT(*) FILTER (WHERE status='TARGET_HIT'),
                       COUNT(*) FILTER (WHERE status='STOP_HIT'),
                       COALESCE(SUM(mark_pnl),0)
                FROM paper_signal_tracker
            """).fetchone()
        closed = target + stop
        wins = target
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
