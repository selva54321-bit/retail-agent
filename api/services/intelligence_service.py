from __future__ import annotations

from core import database as db


def get_market_intelligence(retailer_id: int, limit_per_competitor: int = 10) -> list[dict]:
    return db.get_market_intelligence(retailer_id, limit_per_competitor=limit_per_competitor)


def get_drop_patterns(retailer_id: int, competitor_name: str | None = None) -> list[dict]:
    return db.get_price_drop_patterns(retailer_id, competitor_name=competitor_name)


def get_competitor_catalog(retailer_id: int, competitor_name: str | None = None) -> list[dict]:
    return db.get_competitor_catalog(retailer_id, competitor_name=competitor_name)


def get_latest_demand_forecasts(retailer_id: int, limit: int = 50) -> list[dict]:
    cur = db.get_conn()["demand_forecasts"].find(
        {"retailer_id": retailer_id},
        {"_id": 0},
    ).sort("computed_at", -1).limit(limit)
    return list(cur)
