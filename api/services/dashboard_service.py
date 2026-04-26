from __future__ import annotations

from api.errors import ApiError
from core import database as db


def _derive_alerts(analytics: list[dict], recommendations: list[dict]) -> list[dict]:
    alerts: list[dict] = []

    for row in analytics:
        gap_pct = float(row.get("price_gap_pct_to_min", 0) or 0) * 100
        min_price = row.get("min_competitor_price", 0)
        if gap_pct > 0 and min_price:
            severity = "high" if gap_pct >= 20 else "medium" if gap_pct >= 10 else "low"
            cheapest = row.get("competitor_prices", {})
            cheapest_comp = "competitor"
            if isinstance(cheapest, dict) and cheapest:
                cheapest_comp = min(cheapest, key=cheapest.get)
            alerts.append(
                {
                    "severity": severity,
                    "message": (
                        f"{row.get('product_name', '')[:60]} is {gap_pct:.1f}% above cheapest "
                        f"({cheapest_comp})"
                    ),
                    "source": "analytics",
                }
            )

    for rec in recommendations:
        if rec.get("action") == "hold":
            continue
        alerts.append(
            {
                "severity": "medium" if rec.get("action") == "reduce" else "high",
                "message": (
                    f"Recommendation: {rec.get('product_name', '')[:60]} → "
                    f"₹{rec.get('recommended_price', 0):,.0f}"
                ),
                "source": "pricing",
            }
        )

    return alerts


def get_cycle_dashboard(retailer_id: int, cycle_id: str) -> dict:
    cycle_log = db.get_cycle_log(retailer_id, cycle_id)
    if not cycle_log:
        raise ApiError(f"Cycle {cycle_id} not found for retailer {retailer_id}", status_code=404)

    analytics = db.get_analytics_for_cycle(retailer_id, cycle_id)
    recommendations = db.get_recommendations_for_cycle(retailer_id, cycle_id)
    market_intelligence = db.get_market_intelligence_for_cycle(retailer_id, cycle_id)
    drop_patterns = db.get_price_drop_patterns_for_cycle(retailer_id)
    competitor_catalog = db.get_competitor_catalog_for_cycle(retailer_id, cycle_id)
    alerts = _derive_alerts(analytics, recommendations)

    return {
        "retailer_id": retailer_id,
        "cycle_id": cycle_id,
        "cycle_log": cycle_log,
        "analytics": analytics,
        "recommendations": recommendations,
        "market_intelligence": market_intelligence,
        "drop_patterns": drop_patterns,
        "competitor_catalog": competitor_catalog,
        "alerts": alerts,
        "briefing": cycle_log.get("briefing", ""),
    }


def get_latest_cycle_dashboard(retailer_id: int) -> dict:
    cycles = db.get_recent_cycles(retailer_id, limit=1)
    if not cycles:
        raise ApiError(f"No cycles found for retailer {retailer_id}", status_code=404)
    latest = cycles[0]
    return get_cycle_dashboard(retailer_id, latest["cycle_id"])
