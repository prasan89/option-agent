from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from math import floor
from typing import Any


@dataclass(frozen=True)
class RiskConfig:
    capital: float = 1_000_000.0
    active_capital: float = 700_000.0
    reserve_capital: float = 300_000.0
    risk_per_trade: float = 5_000.0
    max_daily_loss: float = 15_000.0
    max_open_positions: int = 7
    max_sector_positions: int = 2
    target_profit_per_position: float = 10_000.0
    min_score: float = 75.0
    min_rr: float = 1.8


@dataclass
class Candidate:
    underlying: str
    direction: str
    score: float
    signal: str
    premium: float
    stop_premium: float
    target_premium: float
    quantity: int
    max_loss: float
    target_profit: float
    rr: float
    sector: str = "UNKNOWN"
    evidence: list[str] | None = None

    def json(self) -> dict[str, Any]:
        return asdict(self)


class FNOOpportunityEngine:
    """Market-wide candidate ranking and portfolio-risk guard.

    This module deliberately does not place orders. It converts scanner rows into
    ranked research candidates and applies portfolio-level constraints.
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()
        self.daily_pnl = 0.0
        self.open_positions: list[dict[str, Any]] = []
        self.last_candidates: list[dict[str, Any]] = []
        self.last_run: str | None = None

    @staticmethod
    def _num(row: dict[str, Any], key: str, default: float = 0.0) -> float:
        try:
            return float(row.get(key) or default)
        except (TypeError, ValueError):
            return default

    def score_row(self, row: dict[str, Any]) -> float:
        """Normalize existing intelligence into a 0-100 opportunity score.

        If richer price-action fields are present they receive additional weight;
        absent fields are neutral rather than fabricated.
        """
        base = abs(self._num(row, "intelligence_score", self._num(row, "activity_score")))
        flow = min(100.0, base)
        momentum = min(100.0, abs(self._num(row, "momentum_score", 50.0)))
        volume = min(100.0, abs(self._num(row, "volume_score", 50.0)))
        liquidity = min(100.0, max(0.0, self._num(row, "liquidity_score", 50.0)))
        structure = min(100.0, max(0.0, self._num(row, "structure_score", 50.0)))
        return round(0.30 * flow + 0.20 * momentum + 0.15 * volume + 0.15 * liquidity + 0.20 * structure, 2)

    def candidate_from_row(self, row: dict[str, Any]) -> Candidate | None:
        direction = str(row.get("direction") or row.get("bias") or "").upper()
        if direction not in {"BULLISH", "BEARISH", "UP", "DOWN"}:
            return None
        direction = "BULLISH" if direction in {"BULLISH", "UP"} else "BEARISH"
        option_type = str(row.get("option_type") or row.get("instrument_type") or "").upper()
        if option_type not in {"CE", "PE"}:
            return None
        if (direction == "BULLISH") != (option_type == "CE"):
            return None
        score = self.score_row(row)
        premium = self._num(row, "premium", self._num(row, "mid", self._num(row, "ltp")))
        if premium <= 0 or score < self.config.min_score:
            return None
        stop = self._num(row, "stop_premium", premium * 0.65)
        target = self._num(row, "target_premium", premium * 1.50)
        risk_per_unit = max(0.01, premium - stop)
        reward_per_unit = max(0.0, target - premium)
        rr = reward_per_unit / risk_per_unit
        if rr < self.config.min_rr:
            return None
        # Prefer the configured rupee risk budget; option lot size is supplied by
        # the live instrument row and is never guessed from a hard-coded lot size.
        lot_size = max(1, int(self._num(row, "lot_size", 1)))
        units = floor(self.config.risk_per_trade / risk_per_unit)
        quantity = (units // lot_size) * lot_size
        if quantity < lot_size:
            return None
        max_loss = quantity * risk_per_unit
        target_profit = quantity * reward_per_unit
        evidence = row.get("evidence") or row.get("data_sources") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        return Candidate(
            underlying=str(row.get("underlying") or "UNKNOWN"),
            direction=direction,
            score=score,
            signal="BUY_CALL" if option_type == "CE" else "BUY_PUT",
            premium=round(premium, 4),
            stop_premium=round(stop, 4),
            target_premium=round(target, 4),
            quantity=quantity,
            max_loss=round(max_loss, 2),
            target_profit=round(target_profit, 2),
            rr=round(rr, 2),
            sector=str(row.get("sector") or "UNKNOWN"),
            evidence=[str(x) for x in evidence],
        )

    def rank(self, rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
        candidates = [c for row in rows if (c := self.candidate_from_row(row)) is not None]
        candidates.sort(key=lambda c: (c.score, c.rr, c.target_profit), reverse=True)
        selected: list[Candidate] = []
        sectors: dict[str, int] = {}
        underlyings: set[str] = set()
        total_risk = 0.0
        for candidate in candidates:
            if candidate.underlying in underlyings:
                continue
            if len(selected) >= min(limit, self.config.max_open_positions):
                break
            if sectors.get(candidate.sector, 0) >= self.config.max_sector_positions:
                continue
            if total_risk + candidate.max_loss > self.config.max_daily_loss:
                continue
            selected.append(candidate)
            underlyings.add(candidate.underlying)
            sectors[candidate.sector] = sectors.get(candidate.sector, 0) + 1
            total_risk += candidate.max_loss
        self.last_candidates = [c.json() for c in selected]
        self.last_run = datetime.now(timezone.utc).isoformat()
        return self.last_candidates

    def can_trade(self) -> tuple[bool, str]:
        if self.daily_pnl <= -self.config.max_daily_loss:
            return False, "DAILY_LOSS_LIMIT"
        if len(self.open_positions) >= self.config.max_open_positions:
            return False, "MAX_OPEN_POSITIONS"
        return True, "OK"

    def register_paper_fill(self, trade: dict[str, Any]) -> dict[str, Any]:
        self.open_positions.append(dict(trade))
        return {"accepted": True, "open_positions": len(self.open_positions)}

    def close_paper_fill(self, pnl: float) -> dict[str, Any]:
        self.daily_pnl += float(pnl)
        if self.open_positions:
            self.open_positions.pop(0)
        return {"daily_pnl": round(self.daily_pnl, 2), "can_trade": self.can_trade()[0]}

    def reset_day(self) -> None:
        self.daily_pnl = 0.0
        self.open_positions.clear()

    def status(self) -> dict[str, Any]:
        allowed, reason = self.can_trade()
        return {
            "config": asdict(self.config),
            "daily_pnl": round(self.daily_pnl, 2),
            "open_positions": len(self.open_positions),
            "trading_allowed_by_risk": allowed,
            "risk_block_reason": reason,
            "last_run": self.last_run,
            "last_candidates": self.last_candidates,
            "execution": "DISABLED",
            "research_only": True,
        }


fno_opportunity_engine = FNOOpportunityEngine()
