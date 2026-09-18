from typing import Any

from fastapi import APIRouter

from app.intelligence.fno_scanner import fno_scanner
from app.strategy.fno_engine import fno_opportunity_engine
from app.strategy.slo_engine import build_results
from app.signals.paper_tracker import paper_signal_tracker

router = APIRouter(prefix="/strategy", tags=["strategy"])


def _track_calls(data: dict[str, Any], scan: dict[str, Any]) -> None:
    """Record every strict SLO option call for paper P&L tracking."""
    try:
        generated_at = scan.get("last_scan_timestamp")
        paper_signal_tracker.record_candidates(data.get("live_results", []), generated_at=generated_at)
    except Exception:
        # Tracking must never break the research strategy endpoint.
        pass


@router.get("/status")
def status() -> dict[str, Any]:
    scan = fno_scanner.stats
    data = build_results(scan.get("latest_rankings", []), scan.get("top_underlyings", []))
    _track_calls(data, scan)
    ranked = fno_opportunity_engine.rank(data["results"], limit=10)
    return {
        "status": "READY" if scan.get("ranking_ready") else "WARMING_UP",
        "scanner_running": scan.get("running", False),
        "ranking_ready": scan.get("ranking_ready", False),
        "generated_candidates": len(ranked),
        "method": "MARKET_WIDE_FNO_OPPORTUNITY_ENGINE",
        "risk": fno_opportunity_engine.status(),
        "research_only": True,
        "trading": "DISABLED",
        "last_scan_timestamp": scan.get("last_scan_timestamp"),
    }


@router.get("/results")
def results(limit: int = 50, min_score: float = 65.0) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    min_score = max(0.0, min(min_score, 100.0))
    scan = fno_scanner.stats
    data = build_results(scan.get("latest_rankings", []), scan.get("top_underlyings", []), min_score=min_score)
    _track_calls(data, scan)

    # Convert the existing option-agent candidates into the new portfolio-aware
    # ranked list. The engine applies score, R:R, sector concentration and total
    # risk constraints without placing any orders.
    ranked = fno_opportunity_engine.rank(data["results"], limit=min(10, limit))
    data["results"] = ranked
    data["count"] = len(ranked)
    data["method"] = "MARKET_WIDE_FNO_OPPORTUNITY_ENGINE"
    data["risk"] = fno_opportunity_engine.status()
    data["results_note"] = "Top candidates are selected across the available F&O scanner universe; selection is risk-capped and research-only. Paper P&L tracking records every strict SLO call before portfolio ranking."
    data["scanner"] = {
        "running": scan.get("running", False),
        "ranking_ready": scan.get("ranking_ready", False),
        "feed_events": scan.get("feed_events", 0),
        "enrichment_requests": scan.get("enrichment_requests", 0),
        "last_scan_timestamp": scan.get("last_scan_timestamp"),
    }
    return data
