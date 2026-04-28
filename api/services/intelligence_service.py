from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from core import database as db


def _fuzzy_name_match(name_a: str, name_b: str, threshold: float = 0.65) -> bool:
    stop_words = {"inch", "inches", "cm", "the", "a", "an", "and", "or", "for", "in", "on", "of", "with", "smart", "led", "tv"}

    def tokens(value: str) -> set[str]:
        import re

        return {
            token.lower()
            for token in re.split(r"\W+", value)
            if token and len(token) > 1 and token.lower() not in stop_words
        }

    left = tokens(name_a)
    right = tokens(name_b)
    if not left or not right:
        return False
    return (len(left & right) / max(len(left), len(right))) >= threshold


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


def get_catalog_spy_snapshot(retailer_id: int) -> dict:
    profile = db.load_retailer_profile(retailer_id) or {}
    catalog = profile.get("catalog", []) if isinstance(profile, dict) else []
    catalog_skus = {item.get("sku", "") for item in catalog if item.get("sku")}

    rows = db.get_competitor_catalog(retailer_id)
    now = datetime.now()

    stock_alerts: list[dict] = []
    for row in rows:
        sku = row.get("catalog_sku", "")
        if sku in catalog_skus and not row.get("in_stock", True):
            stock_alerts.append(
                {
                    "type": "stock_out",
                    "competitor": row.get("competitor_name", ""),
                    "product": row.get("catalog_product_name") or row.get("product_name", ""),
                    "sku": sku,
                }
            )

    frequent_oos = db.get_frequent_stockouts(retailer_id, min_stockouts=2)
    fast_movers = [
        {
            "product": row.get("product_name", ""),
            "competitor": row.get("competitor_name", ""),
            "stockout_rate": round(float(row.get("stockout_rate", 0) or 0), 2),
            "times_out": row.get("times_out_of_stock", 0),
        }
        for row in frequent_oos[:10]
    ]

    new_arrivals: list[dict] = []
    seen_keys: set[str] = set()
    for row in db.get_new_competitor_products(retailer_id, since_hours=25):
        name = row.get("product_name", "")
        competitor = row.get("competitor_name", "")
        key = f"{competitor}:{name[:40].lower()}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if row.get("catalog_sku", "") in catalog_skus:
            continue
        new_arrivals.append(
            {
                "type": "new_arrival",
                "competitor": competitor,
                "product": name,
                "price": row.get("price", 0),
            }
        )

    discontinued: list[dict] = []
    this_cycle: dict[str, list[str]] = defaultdict(list)
    recent_cutoff = (datetime.now().timestamp() - 25 * 60 * 60)
    recent_rows = db.get_conn()["price_history"].find(
        {
            "retailer_id": retailer_id,
            "scraped_at": {"$gte": datetime.fromtimestamp(recent_cutoff).isoformat()},
        },
        {"_id": 0, "competitor_name": 1, "product_name_raw": 1},
    )
    for row in recent_rows:
        competitor = row.get("competitor_name", "")
        name = row.get("product_name_raw", "")
        if competitor and name:
            this_cycle[competitor].append(name)

    for row in rows:
        competitor = row.get("competitor_name", "")
        name = row.get("product_name", "")
        last_seen = row.get("last_seen_at", "")
        times_seen = int(row.get("times_seen", 0) or 0)
        if competitor not in this_cycle or times_seen < 5 or not last_seen:
            continue
        try:
            last_seen_dt = datetime.fromisoformat(str(last_seen)[:19])
            days_absent = (now - last_seen_dt).days
        except Exception:
            continue
        if days_absent < 7:
            continue
        cycle_names = this_cycle[competitor]
        if any(_fuzzy_name_match(name, candidate, threshold=0.65) for candidate in cycle_names):
            continue
        discontinued.append(
            {
                "type": "discontinued",
                "competitor": competitor,
                "product": name,
                "last_seen": str(last_seen)[:10],
                "days_absent": days_absent,
                "times_seen": times_seen,
            }
        )

    catalog_alerts: list[dict] = []
    for alert in stock_alerts:
        catalog_alerts.append(
            {
                "type": "stock_out",
                "severity": "medium",
                "message": f"Out of stock: {alert['product'][:50]} at {alert['competitor']}",
                "data": alert,
            }
        )
    for alert in new_arrivals:
        catalog_alerts.append(
            {
                "type": "new_arrival",
                "severity": "medium",
                "message": f"New at {alert['competitor']}: {alert['product'][:50]} (₹{alert['price']:,.0f}) — you don't carry this",
                "data": alert,
            }
        )
    for alert in discontinued:
        catalog_alerts.append(
            {
                "type": "discontinued",
                "severity": "low",
                "message": f"Possibly discontinued at {alert['competitor']}: {alert['product'][:50]} (absent {alert['days_absent']}d, last seen {alert['last_seen']})",
                "data": alert,
            }
        )

    return {
        "retailer_id": retailer_id,
        "catalog_alerts": catalog_alerts,
        "new_arrivals": new_arrivals,
        "stock_outs": stock_alerts,
        "discontinued": discontinued,
        "fast_movers": fast_movers,
    }
