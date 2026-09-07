from typing import Any

from fastapi import APIRouter

from app.intelligence.fno_scanner import fno_scanner
from app.strategy.slo_engine import build_results

router = APIRouter(prefix="/strategy", tags=["strategy"])


@router.get("/status")
def status() -> dict[str, Any]:
    scan = fno_scanner.stats
    data = build_results(scan.get("latest_rankings", []), scan.get("top_underlyings", []))
    return {
        "status": "READY" if scan.get("ranking_ready") else "WARMING_UP",
        "scanner_running": scan.get("running", False),
        "ranking_ready": scan.get("ranking_ready", False),
        "generated_candidates": data["count"],
        "method": data["method"],
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
    data["results"] = data["results"][:limit]
    data["scanner"] = {
        "running": scan.get("running", False),
        "ranking_ready": scan.get("ranking_ready", False),
        "feed_events": scan.get("feed_events", 0),
        "enrichment_requests": scan.get("enrichment_requests", 0),
        "last_scan_timestamp": scan.get("last_scan_timestamp"),
    }
    return data
