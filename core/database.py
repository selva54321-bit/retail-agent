"""
RetailAgent - Database Layer (MongoDB)
=======================================
MongoDB-backed persistence for retailer profiles, competitor registry,
price history, product mappings, and recommendations.
All agents read/write through this module.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional

from pymongo import ASCENDING, DESCENDING, MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import PyMongoError
from pymongo.results import UpdateResult
from pymongo import ReturnDocument
import pymongo


MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "retailagent")
MONGODB_MAX_POOL_SIZE = int(os.environ.get("MONGODB_MAX_POOL_SIZE", "50"))
MONGODB_MIN_POOL_SIZE = int(os.environ.get("MONGODB_MIN_POOL_SIZE", "5"))

_client: Optional[MongoClient] = None


def _now_iso() -> str:
    return datetime.now().isoformat()

def _mask_uri(uri: str) -> str:
    """Hide credentials in MongoDB URI when printing logs/status."""
    if "://" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    if "@" not in rest:
        return uri
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


def _client_instance() -> MongoClient:
    """Create one reusable MongoClient for the process."""
    global _client
    if _client is None:
        _client = MongoClient(
            MONGODB_URI,
            appname="RetailAgent",
            maxPoolSize=MONGODB_MAX_POOL_SIZE,
            minPoolSize=MONGODB_MIN_POOL_SIZE,
            maxIdleTimeMS=300000,
            connectTimeoutMS=5000,
            socketTimeoutMS=30000,
            serverSelectionTimeoutMS=5000,
        )
    return _client


def _db() -> Database:
    return _client_instance()[MONGODB_DB]


def get_conn() -> Database:
    """
    Backward-compatible alias used by a few modules.
    Returns the active Mongo database object.
    """
    return _db()


def _col(name: str) -> Collection:
    return _db()[name]


def _strip_id(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


def _strip_ids(docs: list[dict]) -> list[dict]:
    return [_strip_id(d) for d in docs if d is not None]


def _next_sequence(counter_name: str) -> int:
    counters = _col("counters")
    doc = counters.find_one_and_update(
        {"_id": counter_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


def init_db():
    """Initialize MongoDB indexes used by the application."""
    db = _db()

    try:
        db.command("ping")
    except PyMongoError as e:
        raise RuntimeError(f"MongoDB connection failed: {e}") from e

    _col("retailer_profiles").create_index([("id", ASCENDING)], unique=True)
    _col("retailer_profiles").create_index([("store_name", ASCENDING)], unique=True)
    _col("retailer_profiles").create_index([("updated_at", DESCENDING)])

    _col("competitor_registry").create_index(
        [("retailer_id", ASCENDING), ("url", ASCENDING), ("catalog_sku", ASCENDING)],
        unique=True,
    )
    _col("competitor_registry").create_index([("retailer_id", ASCENDING), ("active", ASCENDING)])

    _col("price_history").create_index(
        [("retailer_id", ASCENDING), ("competitor_name", ASCENDING),
         ("product_name_raw", ASCENDING), ("scraped_at", ASCENDING)]
    )
    _col("price_history").create_index([("retailer_id", ASCENDING), ("scraped_at", DESCENDING)])

    _col("product_mappings").create_index(
        [("retailer_id", ASCENDING), ("retailer_sku", ASCENDING),
         ("competitor_name", ASCENDING), ("competitor_product_name", ASCENDING)],
        unique=True,
    )

    _col("analytics_results").create_index([("retailer_id", ASCENDING), ("cycle_id", ASCENDING)])

    _col("recommendations").create_index(
        [("retailer_id", ASCENDING), ("cycle_id", ASCENDING), ("retailer_sku", ASCENDING)]
    )
    _col("recommendations").create_index([("retailer_id", ASCENDING), ("created_at", DESCENDING)])

    _col("cycle_log").create_index([("retailer_id", ASCENDING), ("started_at", DESCENDING)])

    _col("competitor_catalog").create_index(
        [("retailer_id", ASCENDING), ("competitor_name", ASCENDING), ("product_name", ASCENDING)],
        unique=True,
    )
    _col("competitor_catalog").create_index([("retailer_id", ASCENDING), ("last_seen_at", DESCENDING)])

    _col("market_intelligence").create_index(
        [("retailer_id", ASCENDING), ("computed_at", DESCENDING)]
    )

    _col("price_drop_patterns").create_index(
        [("retailer_id", ASCENDING), ("competitor_name", ASCENDING), ("catalog_sku", ASCENDING)],
        unique=True,
    )
    _col("price_drop_patterns").create_index(
        [("retailer_id", ASCENDING), ("competitor_name", ASCENDING), ("consistency_score", DESCENDING)]
    )

    _col("demand_forecasts").create_index(
        [("retailer_id", ASCENDING), ("cycle_id", ASCENDING), ("computed_at", DESCENDING)]
    )

    _col("analytics_results").create_index(
        [("retailer_id", ASCENDING), ("retailer_sku", ASCENDING), ("computed_at", DESCENDING)]
    )

    print(f"[DB] Initialized MongoDB at {MONGODB_URI} (db: {MONGODB_DB})")

def check_mongodb_health() -> dict:
    """
    Lightweight startup check for MongoDB readiness.
    Returns a status dict consumed by CLI startup/status screens.
    """
    started = datetime.now()
    info = {
        "ok": False,
        "uri": _mask_uri(MONGODB_URI),
        "db": MONGODB_DB,
        "driver": getattr(pymongo, "version", "unknown"),
        "latency_ms": -1,
        "collections": 0,
        "error": "",
    }
    try:
        client = _client_instance()
        client.admin.command("ping")
        elapsed = int((datetime.now() - started).total_seconds() * 1000)
        info["ok"] = True
        info["latency_ms"] = max(elapsed, 0)
        info["collections"] = len(_db().list_collection_names())
        return info
    except Exception as e:
        info["error"] = str(e)
        return info

# --- RETAILER PROFILE --------------------------------------------------------

def save_retailer_profile(profile_dict: dict) -> int:
    profiles = _col("retailer_profiles")
    now = _now_iso()
    existing = profiles.find_one({"store_name": profile_dict["store_name"]}, {"id": 1})

    if existing:
        rid = int(existing["id"])
        profiles.update_one(
            {"id": rid},
            {"$set": {"profile_json": profile_dict, "updated_at": now}},
        )
    else:
        rid = _next_sequence("retailer_profiles_id")
        profiles.insert_one(
            {
                "id": rid,
                "store_name": profile_dict["store_name"],
                "profile_json": profile_dict,
                "created_at": now,
                "updated_at": now,
            }
        )
    return rid


def load_retailer_profile(retailer_id: int) -> Optional[dict]:
    row = _col("retailer_profiles").find_one({"id": retailer_id}, {"profile_json": 1})
    return row.get("profile_json") if row else None


def list_retailer_profiles() -> list:
    cur = _col("retailer_profiles").find(
        {},
        {"_id": 0, "id": 1, "store_name": 1, "updated_at": 1},
    ).sort("updated_at", DESCENDING)
    return list(cur)


# --- COMPETITOR REGISTRY -----------------------------------------------------

def upsert_competitor(retailer_id: int, target: dict):
    now = _now_iso()
    selector_config = target.get("selector_config", {})
    if isinstance(selector_config, str):
        try:
            selector_config = json.loads(selector_config)
        except Exception:
            selector_config = {}

    _col("competitor_registry").update_one(
        {
            "retailer_id": retailer_id,
            "url": target.get("url", ""),
            "catalog_sku": target.get("catalog_sku", ""),
        },
        {
            "$set": {
                "competitor_name": target.get("competitor_name", ""),
                "priority": target.get("priority", "medium"),
                "scan_interval_hours": target.get("scan_interval_hours", 24),
                "scrape_method": target.get("scrape_method", "static"),
                "product_category": target.get("product_category", ""),
                "selector_config": selector_config,
                "source": target.get("source", "planner"),
                "notes": target.get("notes", ""),
                "catalog_product_name": target.get("catalog_product_name", ""),
                "updated_at": now,
            },
            "$setOnInsert": {
                "active": 1,
                "consecutive_failures": 0,
                "last_scraped": "",
                "created_at": now,
            },
        },
        upsert=True,
    )


def get_competitors(retailer_id: int) -> list:
    cur = _col("competitor_registry").find(
        {"retailer_id": retailer_id, "active": {"$ne": 0}}
    )
    return _strip_ids(list(cur))


def mark_scrape_result(retailer_id: int, url: str, success: bool):
    if success:
        _col("competitor_registry").update_many(
            {"retailer_id": retailer_id, "url": url},
            {"$set": {"last_scraped": _now_iso(), "consecutive_failures": 0}},
        )
    else:
        _col("competitor_registry").update_many(
            {"retailer_id": retailer_id, "url": url},
            {"$inc": {"consecutive_failures": 1}},
        )


def update_selector_config_for_domain(retailer_id: int, domain: str, selector_config: dict) -> int:
    """Update selector_config for all competitor URLs containing the given domain."""
    pattern = re.compile(re.escape(domain), re.IGNORECASE)
    res: UpdateResult = _col("competitor_registry").update_many(
        {"retailer_id": retailer_id, "url": {"$regex": pattern}},
        {"$set": {"selector_config": selector_config, "updated_at": _now_iso()}},
    )
    return int(res.modified_count)


# --- PRICE HISTORY -----------------------------------------------------------

def save_price_records(retailer_id: int, records: list):
    if not records:
        return
    docs = []
    now = _now_iso()
    for r in records:
        docs.append(
            {
                "retailer_id": retailer_id,
                "competitor_name": r.get("competitor_name"),
                "competitor_url": r.get("competitor_url"),
                "product_name_raw": r.get("product_name_raw"),
                "price": r.get("price"),
                "original_price": r.get("original_price"),
                "in_stock": bool(r.get("in_stock", True)),
                "scraped_at": r.get("scraped_at") or now,
                "confidence": r.get("confidence", "high"),
                "scrape_method_used": r.get("scrape_method_used", "static"),
                "catalog_sku": r.get("catalog_sku", ""),
            }
        )
    _col("price_history").insert_many(docs)


def get_price_history(retailer_id: int, competitor_name: str,
                      product_name: str, days: int = 30) -> list:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    regex = re.compile(re.escape(product_name[:20]), re.IGNORECASE)
    cur = _col("price_history").find(
        {
            "retailer_id": retailer_id,
            "competitor_name": competitor_name,
            "product_name_raw": {"$regex": regex},
            "scraped_at": {"$gte": cutoff},
        },
        {"_id": 0, "price": 1, "scraped_at": 1},
    ).sort("scraped_at", ASCENDING)
    return list(cur)


def get_latest_prices(retailer_id: int) -> list:
    """Get the most recent price per competitor per product."""
    pipeline = [
        {"$match": {"retailer_id": retailer_id}},
        {"$sort": {"scraped_at": -1}},
        {
            "$group": {
                "_id": {
                    "competitor_name": "$competitor_name",
                    "product_name_raw": "$product_name_raw",
                },
                "doc": {"$first": "$$ROOT"},
            }
        },
        {"$replaceRoot": {"newRoot": "$doc"}},
        {
            "$project": {
                "_id": 0,
                "competitor_name": 1,
                "product_name_raw": 1,
                "price": 1,
                "original_price": 1,
                "in_stock": 1,
                "scraped_at": 1,
                "confidence": 1,
            }
        },
        {"$sort": {"competitor_name": 1, "product_name_raw": 1}},
    ]
    return list(_col("price_history").aggregate(pipeline))


# --- PRODUCT MAPPINGS --------------------------------------------------------

def save_product_mapping(retailer_id: int, mapping: dict):
    _col("product_mappings").update_one(
        {
            "retailer_id": retailer_id,
            "retailer_sku": mapping["retailer_sku"],
            "competitor_name": mapping["competitor_name"],
            "competitor_product_name": mapping["competitor_product_name"],
        },
        {
            "$set": {
                "retailer_product_name": mapping["retailer_product_name"],
                "competitor_price": mapping["competitor_price"],
                "similarity_score": mapping["similarity_score"],
                "match_method": mapping["match_method"],
                "matched_at": _now_iso(),
            }
        },
        upsert=True,
    )


def get_product_mappings(retailer_id: int) -> list:
    cur = _col("product_mappings").find({"retailer_id": retailer_id}, {"_id": 0})
    return list(cur)


# --- ANALYTICS RESULTS -------------------------------------------------------

def save_analytics_results(retailer_id: int, cycle_id: str, analytics_list: list[dict]):
    if not analytics_list:
        return
    now = _now_iso()
    docs = []
    for a in analytics_list:
        docs.append(
            {
                "retailer_id": retailer_id,
                "cycle_id": cycle_id,
                "retailer_sku": a["retailer_sku"],
                "product_name": a["product_name"],
                "retailer_price": a["retailer_price"],
                "competitor_prices_json": str(a.get("competitor_prices", {})),
                "min_competitor_price": a.get("min_competitor_price", 0),
                "avg_competitor_price": a.get("avg_competitor_price", 0),
                "max_competitor_price": a.get("max_competitor_price", 0),
                "price_rank": a.get("price_rank", 0),
                "total_competitors": a.get("total_competitors", 0),
                "price_gap_to_min": a.get("price_gap_to_min", 0),
                "price_gap_pct_to_min": a.get("price_gap_pct_to_min", 0),
                "trend": a.get("trend", "stable"),
                "is_anomaly": int(bool(a.get("is_anomaly", False))),
                "anomaly_reason": a.get("anomaly_reason", ""),
                "computed_at": now,
            }
        )
    _col("analytics_results").insert_many(docs)


# --- RECOMMENDATIONS ---------------------------------------------------------

def save_recommendations(retailer_id: int, cycle_id: str, recs: list):
    if not recs:
        return
    docs = []
    now = _now_iso()
    for r in recs:
        docs.append(
            {
                "retailer_id": retailer_id,
                "cycle_id": cycle_id,
                "retailer_sku": r.get("retailer_sku"),
                "product_name": r.get("product_name"),
                "current_price": r.get("current_price"),
                "recommended_price": r.get("recommended_price"),
                "price_change": r.get("price_change"),
                "price_change_pct": r.get("price_change_pct"),
                "action": r.get("action"),
                "confidence": r.get("confidence"),
                "reasoning": r.get("reasoning"),
                "guardrail_applied": int(bool(r.get("guardrail_applied", False))),
                "guardrail_note": r.get("guardrail_note", ""),
                "approved": r.get("approved"),
                "created_at": r.get("created_at", now),
            }
        )
    _col("recommendations").insert_many(docs)


def update_recommendation_approvals(retailer_id: int, cycle_id: str, decisions: list[dict]) -> int:
    """Bulk-update recommendation approval decisions for a cycle."""
    if not decisions:
        return 0

    ops = []
    for d in decisions:
        if d.get("approved") is None:
            continue
        ops.append(
            UpdateOne(
                {
                    "retailer_id": retailer_id,
                    "cycle_id": cycle_id,
                    "retailer_sku": d.get("retailer_sku"),
                },
                {"$set": {"approved": int(bool(d.get("approved")))}}
            )
        )

    if not ops:
        return 0

    result = _col("recommendations").bulk_write(ops, ordered=False)
    return int(result.modified_count)


def get_pending_recommendations(retailer_id: int) -> list:
    cur = _col("recommendations").find(
        {"retailer_id": retailer_id, "approved": None},
        {"_id": 0},
    ).sort("created_at", DESCENDING)
    return list(cur)


def get_all_recommendations(retailer_id: int, limit: int = 50) -> list:
    cur = _col("recommendations").find(
        {"retailer_id": retailer_id},
        {"_id": 0},
    ).sort("created_at", DESCENDING).limit(limit)
    return list(cur)


def get_recommendations_for_cycle(retailer_id: int, cycle_id: str) -> list:
    cur = _col("recommendations").find(
        {"retailer_id": retailer_id, "cycle_id": cycle_id},
        {"_id": 0},
    ).sort("created_at", DESCENDING)
    return list(cur)


# --- CYCLE LOG ---------------------------------------------------------------

def save_cycle_log(retailer_id: int, cycle: dict):
    _col("cycle_log").insert_one(
        {
            "retailer_id": retailer_id,
            "cycle_id": cycle.get("cycle_id"),
            "started_at": cycle.get("started_at"),
            "ended_at": cycle.get("ended_at"),
            "status": cycle.get("status", "completed"),
            "records_scraped": cycle.get("records_scraped", 0),
            "matches_found": cycle.get("matches_found", 0),
            "recommendations_made": cycle.get("recommendations_made", 0),
            "briefing": cycle.get("briefing", ""),
            "catalog_alerts": cycle.get("catalog_alerts", []),
            "fast_movers": cycle.get("fast_movers", []),
            "errors_json": cycle.get("errors", []),
        }
    )


def get_recent_cycles(retailer_id: int, limit: int = 10) -> list:
    cur = _col("cycle_log").find(
        {"retailer_id": retailer_id},
        {"_id": 0},
    ).sort("started_at", DESCENDING).limit(limit)
    return list(cur)


def get_cycle_log(retailer_id: int, cycle_id: str) -> dict | None:
    return _col("cycle_log").find_one(
        {"retailer_id": retailer_id, "cycle_id": cycle_id},
        {"_id": 0},
    )


def get_analytics_for_cycle(retailer_id: int, cycle_id: str) -> list:
    cur = _col("analytics_results").find(
        {"retailer_id": retailer_id, "cycle_id": cycle_id},
        {"_id": 0},
    ).sort("price_rank", ASCENDING)
    return list(cur)


def get_market_intelligence_for_cycle(retailer_id: int, cycle_id: str) -> list:
    cur = _col("market_intelligence").find(
        {"retailer_id": retailer_id, "cycle_id": cycle_id},
        {"_id": 0},
    ).sort("strategy_label", ASCENDING)
    return list(cur)


def get_price_drop_patterns_for_cycle(retailer_id: int, cycle_id: str | None = None) -> list:
    flt = {"retailer_id": retailer_id}
    cur = _col("price_drop_patterns").find(flt, {"_id": 0}).sort("consistency_score", DESCENDING)
    return list(cur)


def get_competitor_catalog_for_cycle(retailer_id: int, cycle_id: str | None = None) -> list:
    # competitor catalog is current-state data; cycle_id is accepted for API symmetry.
    cur = _col("competitor_catalog").find(
        {"retailer_id": retailer_id},
        {"_id": 0},
    ).sort("last_seen_at", DESCENDING)
    return list(cur)


# --- COMPETITOR CATALOG ------------------------------------------------------

def upsert_competitor_catalog(retailer_id: int, records: list[dict]):
    """
    Upsert all scraped products into the competitor_catalog collection.
    """
    if not records:
        return

    coll = _col("competitor_catalog")
    now = _now_iso()

    for r in records:
        name = r.get("product_name_raw", "")[:250]
        comp = r.get("competitor_name", "")
        price = r.get("price", 0)
        in_stock = bool(r.get("in_stock", True))
        catalog_sku = r.get("catalog_sku", "")

        flt = {
            "retailer_id": retailer_id,
            "competitor_name": comp,
            "product_name": name,
        }
        existing = coll.find_one(flt, {"times_seen": 1, "times_out_of_stock": 1})

        if existing:
            update_doc = {
                "$set": {
                    "price": price,
                    "in_stock": in_stock,
                    "last_seen_at": now,
                },
                "$inc": {
                    "times_seen": 1,
                    "times_out_of_stock": 0 if in_stock else 1,
                },
            }
            if catalog_sku:
                update_doc["$set"]["catalog_sku"] = catalog_sku
            coll.update_one(flt, update_doc)
        else:
            coll.insert_one(
                {
                    "retailer_id": retailer_id,
                    "competitor_name": comp,
                    "product_name": name,
                    "price": price,
                    "in_stock": in_stock,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "times_seen": 1,
                    "times_out_of_stock": 0 if in_stock else 1,
                    "catalog_sku": catalog_sku,
                }
            )


def get_competitor_catalog(retailer_id: int,
                           competitor_name: str = None) -> list[dict]:
    flt = {"retailer_id": retailer_id}
    sort_field = "last_seen_at"
    if competitor_name:
        flt["competitor_name"] = competitor_name
        sort_field = "times_seen"

    cur = _col("competitor_catalog").find(flt, {"_id": 0}).sort(sort_field, DESCENDING)
    return list(cur)


def get_new_competitor_products(retailer_id: int, since_hours: int = 25) -> list[dict]:
    """Products first seen in the last N hours (new arrivals)."""
    cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
    cur = _col("competitor_catalog").find(
        {
            "retailer_id": retailer_id,
            "catalog_sku": "",
            "first_seen_at": {"$gte": cutoff},
        },
        {"_id": 0},
    ).sort([("competitor_name", ASCENDING), ("first_seen_at", DESCENDING)])
    return list(cur)


def get_frequent_stockouts(retailer_id: int,
                           min_stockouts: int = 2) -> list[dict]:
    """Products that went out of stock frequently - proxy for high demand."""
    cur = _col("competitor_catalog").find(
        {
            "retailer_id": retailer_id,
            "times_seen": {"$gte": 3},
            "times_out_of_stock": {"$gte": min_stockouts},
        },
        {"_id": 0},
    ).sort("times_out_of_stock", DESCENDING)

    rows = list(cur)
    for r in rows:
        seen = max(int(r.get("times_seen", 0)), 1)
        r["stockout_rate"] = float(r.get("times_out_of_stock", 0)) / float(seen)
    return rows


# --- MARKET INTELLIGENCE -----------------------------------------------------

def save_market_intelligence(retailer_id: int, cycle_id: str,
                             intel_list: list[dict]):
    if not intel_list:
        return

    now = _now_iso()
    docs = []
    for r in intel_list:
        docs.append(
            {
                "retailer_id": retailer_id,
                "cycle_id": cycle_id,
                "competitor_name": r["competitor_name"],
                "strategy_label": r.get("strategy_label", "unknown"),
                "avg_price_gap_pct": r.get("avg_price_gap_pct", 0),
                "price_change_count": r.get("price_change_count", 0),
                "flash_sales_count": r.get("flash_sales_count", 0),
                "insights_json": r.get("insights", {}),
                "computed_at": now,
            }
        )
    _col("market_intelligence").insert_many(docs)


def get_market_intelligence(retailer_id: int,
                            limit_per_competitor: int = 10) -> list[dict]:
    cur = _col("market_intelligence").find(
        {"retailer_id": retailer_id},
        {"_id": 0},
    ).sort("computed_at", DESCENDING).limit(limit_per_competitor * 10)
    return list(cur)


def get_price_history_for_intel(retailer_id: int,
                                competitor_name: str,
                                days: int = 30) -> list[dict]:
    """Get price history for a competitor across all their products."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cur = _col("price_history").find(
        {
            "retailer_id": retailer_id,
            "competitor_name": competitor_name,
            "scraped_at": {"$gte": cutoff},
        },
        {
            "_id": 0,
            "competitor_name": 1,
            "product_name_raw": 1,
            "price": 1,
            "in_stock": 1,
            "scraped_at": 1,
        },
    ).sort("scraped_at", ASCENDING)
    return list(cur)


# --- PRICE DROP PATTERNS -----------------------------------------------------

def upsert_price_drop_pattern(retailer_id: int, pattern: dict):
    """Save/update a detected price drop pattern for a competitor x product."""
    now = _now_iso()
    _col("price_drop_patterns").update_one(
        {
            "retailer_id": retailer_id,
            "competitor_name": pattern["competitor_name"],
            "catalog_sku": pattern["catalog_sku"],
        },
        {
            "$set": {
                "product_name": pattern["product_name"],
                "peak_day_of_week": pattern["peak_day_of_week"],
                "avg_drop_pct": pattern["avg_drop_pct"],
                "max_drop_pct": pattern["max_drop_pct"],
                "drop_count": pattern["drop_count"],
                "total_observations": pattern["total_observations"],
                "consistency_score": pattern["consistency_score"],
                "last_drop_date": pattern.get("last_drop_date", ""),
                "next_predicted_date": pattern.get("next_predicted_date", ""),
                "updated_at": now,
            }
        },
        upsert=True,
    )


def get_price_drop_patterns(retailer_id: int,
                            competitor_name: str = None) -> list[dict]:
    """Get stored drop patterns, optionally filtered by competitor."""
    flt = {"retailer_id": retailer_id}
    if competitor_name:
        flt["competitor_name"] = competitor_name

    sort_spec = [("consistency_score", DESCENDING)]
    if not competitor_name:
        sort_spec.append(("avg_drop_pct", DESCENDING))

    cur = _col("price_drop_patterns").find(flt, {"_id": 0}).sort(sort_spec)
    return list(cur)


def get_price_history_by_sku(retailer_id: int,
                             competitor_name: str,
                             catalog_sku: str,
                             days: int = 60) -> list[dict]:
    """Get price history for a specific competitor+SKU combination."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cur = _col("price_history").find(
        {
            "retailer_id": retailer_id,
            "competitor_name": competitor_name,
            "catalog_sku": catalog_sku,
            "scraped_at": {"$gte": cutoff},
        },
        {
            "_id": 0,
            "competitor_name": 1,
            "catalog_sku": 1,
            "product_name_raw": 1,
            "price": 1,
            "in_stock": 1,
            "scraped_at": 1,
        },
    ).sort("scraped_at", ASCENDING)
    return list(cur)

def get_price_velocity(retailer_id: int, catalog_sku: str, days: int = 14) -> list[dict]:
    """Get recent per-competitor price timeline for a catalog SKU."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cur = _col("price_history").find(
        {
            "retailer_id": retailer_id,
            "catalog_sku": catalog_sku,
            "scraped_at": {"$gte": cutoff},
            "price": {"$ne": None},
        },
        {"_id": 0, "competitor_name": 1, "price": 1, "scraped_at": 1},
    ).sort([("competitor_name", ASCENDING), ("scraped_at", ASCENDING)])
    return list(cur)


def save_demand_forecasts(retailer_id: int, cycle_id: str, forecasts: list[dict]):
    """Persist demand forecasts generated by Intel Agent."""
    if not forecasts:
        return

    now = _now_iso()
    docs = []
    for f in forecasts:
        docs.append(
            {
                "retailer_id": retailer_id,
                "cycle_id": cycle_id,
                "catalog_sku": f.get("catalog_sku", ""),
                "product_name": f.get("product_name", ""),
                "demand_signal": f.get("demand_signal", "unknown"),
                "confidence": f.get("confidence", "low"),
                "price_drop_velocity": float(f.get("price_drop_velocity", 0) or 0),
                "stockout_rate": float(f.get("stockout_rate", 0) or 0),
                "competitor_drop_count": int(f.get("competitor_drop_count", 0) or 0),
                "seasonal_event": f.get("seasonal_event", ""),
                "days_to_event": int(f.get("days_to_event", 0) or 0),
                "recommendation": f.get("recommendation", ""),
                "computed_at": now,
            }
        )
    _col("demand_forecasts").insert_many(docs)


def get_recent_price_rank_history(retailer_id: int, retailer_sku: str, limit: int = 10) -> list[dict]:
    """Get recent rank history for a retailer SKU ordered newest first."""
    cur = _col("analytics_results").find(
        {"retailer_id": retailer_id, "retailer_sku": retailer_sku},
        {"_id": 0, "price_rank": 1, "computed_at": 1},
    ).sort("computed_at", DESCENDING).limit(limit)
    return list(cur)

