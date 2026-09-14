from app.strategy.fno_engine import FNOOpportunityEngine, RiskConfig


def row(symbol: str, score: float, sector: str = "BANKING") -> dict:
    return {
        "underlying": symbol,
        "direction": "BULLISH",
        "option_type": "CE",
        "symbol": f"{symbol}-CE",
        "total_score": score,
        "premium": 100.0,
        "stop_premium": 65.0,
        "target_premium": 180.0,
        "lot_size": 10,
        "sector": sector,
    }


def test_candidate_risk_and_target():
    engine = FNOOpportunityEngine(RiskConfig(risk_per_trade=5000, max_daily_loss=15000))
    candidate = engine.candidate_from_row(row("TEST", 90))
    assert candidate is not None
    assert candidate.signal == "BUY_CALL"
    assert candidate.max_loss <= 5000
    assert candidate.target_profit >= 10000
    assert candidate.rr > 1.8


def test_rank_respects_total_risk_and_sector_cap():
    engine = FNOOpportunityEngine(RiskConfig(risk_per_trade=5000, max_daily_loss=15000, max_sector_positions=2))
    results = engine.rank([
        row("A", 95), row("B", 94), row("C", 93), row("D", 92, "IT"),
    ], limit=10)
    assert len(results) <= 3
    assert sum(x["max_loss"] for x in results) <= 15000
    assert sum(1 for x in results if x["sector"] == "BANKING") <= 2


def test_daily_loss_lock():
    engine = FNOOpportunityEngine()
    engine.daily_pnl = -15000
    allowed, reason = engine.can_trade()
    assert not allowed
    assert reason == "DAILY_LOSS_LIMIT"
