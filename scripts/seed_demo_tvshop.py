from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.intake_agent import load_demo_profile
from core import database as db


RETAILER_ID = 1
CYCLE_ID = "demo-tv-seed-20260428"

DEMO_COMPETITORS = ["Amazon India", "Flipkart", "Poorvika", "Croma"]

PRICE_MAP = {
    "32LQ570BPSA": {
        "product_name": "LG 81.28 cm 32 inch Full HD LED Smart WebOS TV",
        "retailer_price": 17912,
        "competitor_prices": {
            "Amazon India": 18299,
            "Flipkart": 18450,
            "Poorvika": 18310,
            "Croma": 18520,
        },
    },
    "UA43UE86AFULXL": {
        "product_name": "Samsung 108 cm (43 inches) Crystal 4K Vista Pro Ultra HD Smart LED TV",
        "retailer_price": 33230,
        "competitor_prices": {
            "Amazon India": 33699,
            "Flipkart": 33950,
            "Poorvika": 34120,
            "Croma": 34010,
        },
    },
    "LG-32-LR595B6LA": {
        "product_name": "LG HD Ready AI Smart TV 32LR595B6LA 32 inch",
        "retailer_price": 17912,
        "competitor_prices": {
            "Amazon India": 18150,
            "Flipkart": 18290,
            "Poorvika": 18340,
            "Croma": 18400,
        },
    },
}


def _iso(days: int = 0, hours: int = 0) -> str:
    return (datetime.now() - timedelta(days=days, hours=hours)).isoformat()


def _reset_demo_data(retailer_id: int) -> None:
    collections = [
        "retailer_profiles",
        "competitor_registry",
        "competitor_catalog",
        "price_history",
        "product_mappings",
        "analytics_results",
        "market_intelligence",
        "demand_forecasts",
        "recommendations",
        "cycle_log",
    ]
    for name in collections:
        db._col(name).delete_many({"retailer_id": retailer_id})


def _seed_retailer_profile() -> None:
    profile = load_demo_profile()
    db.save_retailer_profile(profile.model_dump())


def _seed_competitor_registry(retailer_id: int) -> None:
    urls = {
        "Amazon India": "https://www.amazon.in/",
        "Flipkart": "https://www.flipkart.com/",
        "Poorvika": "https://www.poorvika.com/",
        "Croma": "https://www.croma.com/",
    }
    for competitor in DEMO_COMPETITORS:
        db.upsert_competitor(
            retailer_id,
            {
                "competitor_name": competitor,
                "url": urls.get(competitor, ""),
                "catalog_sku": "",
                "priority": "medium",
                "scan_interval_hours": 24,
                "scrape_method": "static",
                "product_category": "televisions",
                "selector_config": {},
                "source": "seed",
                "notes": "Seeded demo competitor",
                "catalog_product_name": "",
            },
        )


def _seed_competitor_catalog(retailer_id: int) -> None:
    docs = []
    for sku, spec in PRICE_MAP.items():
        for competitor, competitor_price in spec["competitor_prices"].items():
            first_seen = _iso(days=10)
            last_seen = _iso()
            times_seen = 3
            times_out_of_stock = 0
            in_stock = True

            # Mock Fast Mover / Stock-out
            if sku == "32LQ570BPSA" and competitor == "Flipkart":
                times_seen = 4
                times_out_of_stock = 2
                in_stock = False
            # Mock Discontinued
            elif sku == "UA43UE86AFULXL" and competitor == "Poorvika":
                times_seen = 6
                in_stock = False
                last_seen = _iso(days=8)

            docs.append(
                {
                    "retailer_id": retailer_id,
                    "competitor_name": competitor,
                    "product_name": spec["product_name"],
                    "price": competitor_price,
                    "in_stock": in_stock,
                    "first_seen_at": first_seen,
                    "last_seen_at": last_seen,
                    "times_seen": times_seen,
                    "times_out_of_stock": times_out_of_stock,
                    "catalog_sku": sku,
                }
            )

    # Mock New Arrival (competitor-only product first seen recently)
    docs.append(
        {
            "retailer_id": retailer_id,
            "competitor_name": "Amazon India",
            "product_name": "Samsung 65 inch OLED Very New Model",
            "price": 65000,
            "in_stock": True,
            "first_seen_at": _iso(hours=10),
            "last_seen_at": _iso(),
            "times_seen": 1,
            "times_out_of_stock": 0,
            "catalog_sku": "",
        }
    )

    coll = db._col("competitor_catalog")
    coll.insert_many(docs)


def _seed_price_history(retailer_id: int) -> None:
    snapshots = [
        (2, 12),
        (1, 8),
        (0, 0),
    ]
    records = []
    for sku, spec in PRICE_MAP.items():
        for competitor, base_price in spec["competitor_prices"].items():
            for day_offset, hour_offset in snapshots:
                in_stock = True
                actual_day = day_offset

                # Reflect the out-of-stock trend in recent snapshots
                if sku == "32LQ570BPSA" and competitor == "Flipkart" and day_offset <= 1:
                    in_stock = False
                
                # Push the discontinued snapshots far into the past
                if sku == "UA43UE86AFULXL" and competitor == "Poorvika":
                    actual_day = day_offset + 8

                records.append(
                    {
                        "competitor_name": competitor,
                        "competitor_url": "",
                        "product_name_raw": spec["product_name"],
                        "price": base_price,
                        "original_price": base_price,
                        "in_stock": in_stock,
                        "scraped_at": _iso(days=actual_day, hours=hour_offset),
                        "confidence": "high",
                        "scrape_method_used": "seed",
                        "catalog_sku": sku,
                    }
                )
    db.save_price_records(retailer_id, records)


def _build_analytics_rows() -> list[dict]:
    analytics: list[dict] = []
    for sku, spec in PRICE_MAP.items():
        competitor_prices = spec["competitor_prices"]
        min_competitor_price = min(competitor_prices.values())
        avg_competitor_price = sum(competitor_prices.values()) / len(competitor_prices)
        max_competitor_price = max(competitor_prices.values())
        analytics.append(
            {
                "retailer_sku": sku,
                "product_name": spec["product_name"],
                "retailer_price": spec["retailer_price"],
                "competitor_prices": competitor_prices,
                "min_competitor_price": min_competitor_price,
                "avg_competitor_price": avg_competitor_price,
                "max_competitor_price": max_competitor_price,
                "price_rank": 1,
                "total_competitors": len(competitor_prices),
                "price_gap_to_min": round(spec["retailer_price"] - min_competitor_price, 0),
                "price_gap_pct_to_min": round(
                    ((spec["retailer_price"] - min_competitor_price) / min_competitor_price) * 100,
                    2,
                ),
                "trend": "stable",
                "is_anomaly": False,
                "anomaly_reason": "",
            }
        )
    return analytics


def _seed_analytics_and_intel(retailer_id: int) -> None:
    analytics = _build_analytics_rows()
    cycle_ids = ["demo-tv-seed-1", "demo-tv-seed-2", "demo-tv-seed-3"]

    for cycle_id in cycle_ids:
        db.save_analytics_results(retailer_id, cycle_id, analytics)

    strategies = []
    forecasts = []
    for sku, spec in PRICE_MAP.items():
        competitor_prices = spec["competitor_prices"]
        min_price = min(competitor_prices.values())
        avg_price = sum(competitor_prices.values()) / len(competitor_prices)
        gap_pct = ((spec["retailer_price"] - avg_price) / avg_price) * 100

        strategies.append(
            {
                "competitor_name": "Market Basket",
                "strategy_label": "competitive_parity",
                "avg_price_gap_pct": round(gap_pct, 2),
                "price_change_count": 0,
                "flash_sales_count": 0,
                "insights": {
                    "catalog_sku": sku,
                    "focus_product": spec["product_name"],
                    "note": "Seeded demo intelligence row",
                    "competitor_prices": competitor_prices,
                    "min_competitor_price": min_price,
                },
            }
        )

        forecasts.append(
            {
                "catalog_sku": sku,
                "product_name": spec["product_name"],
                "demand_signal": "stable",
                "confidence": "medium",
                "price_drop_velocity": 0.0,
                "stockout_rate": 0.0,
                "competitor_drop_count": 0,
                "seasonal_event": "",
                "days_to_event": 0,
                "recommendation": (
                    f"{spec['product_name'][:35]}: demand is STABLE — maintain current stock levels"
                ),
            }
        )

    db.save_market_intelligence(retailer_id, CYCLE_ID, strategies)
    db.save_demand_forecasts(retailer_id, CYCLE_ID, forecasts)


def _seed_cycle_log(retailer_id: int) -> None:
    for i in range(1, 6):
        db._col("cycle_log").insert_one({
            "retailer_id": retailer_id,
            "cycle_id": f"demo-old-cycle-{i}",
            "status": "completed",
            "started_at": _iso(days=i, hours=2),
            "ended_at": _iso(days=i, hours=1),
            "records_scraped": 12,
            "matches_found": 12,
            "recommendations_made": 0,
            "catalog_alerts": 0,
            "notes": "Historical mock cycle",
        })

    doc = {
        "retailer_id": retailer_id,
        "cycle_id": CYCLE_ID,
        "status": "completed",
        "started_at": _iso(hours=1),
        "ended_at": _iso(),
        "records_scraped": 12,
        "matches_found": 12,
        "recommendations_made": 3,
        "catalog_alerts": 0,
        "notes": "Seeded demo cycle for televisions catalog",
    }
    db._col("cycle_log").insert_one(doc)


def _catalog_spy_cycle_thresholds() -> dict[str, str]:
    return {
        "new_arrivals": "cycle 1+ (when a competitor-only product is first seen in the last 25 hours)",
        "stock_outs": "cycle 1+ (when one of your mapped catalog SKUs is scraped as out of stock)",
        "fast_movers": "cycle 3+ (needs at least 3 sightings and 2 stock-out events)",
        "possibly_discontinued": "cycle 5+ (needs at least 5 sightings and 7 days of absence)",
    }


def _print_catalog_spy_report(retailer_id: int) -> None:
    thresholds = _catalog_spy_cycle_thresholds()
    cycle_count = db._col("cycle_log").count_documents({"retailer_id": retailer_id})
    print("[Check] CatalogSpy readiness")
    print(f"[Check] Existing cycles for retailer_id={retailer_id}: {cycle_count}")
    for label, detail in thresholds.items():
        print(f"[Check] {label.replace('_', ' ').title()}: {detail}")


def seed_demo_tvshop(retailer_id: int, reset: bool = True) -> None:
    if reset:
        _reset_demo_data(retailer_id)

    _seed_retailer_profile()
    _seed_competitor_registry(retailer_id)
    _seed_competitor_catalog(retailer_id)
    _seed_price_history(retailer_id)
    _seed_analytics_and_intel(retailer_id)
    _seed_cycle_log(retailer_id)

    for sku, spec in PRICE_MAP.items():
        for competitor, competitor_price in spec["competitor_prices"].items():
            db.save_product_mapping(
                retailer_id,
                {
                    "retailer_sku": sku,
                    "competitor_name": competitor,
                    "competitor_product_name": spec["product_name"],
                    "retailer_product_name": spec["product_name"],
                    "competitor_price": competitor_price,
                    "similarity_score": 0.98,
                    "match_method": "seed",
                },
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo television catalog data into MongoDB")
    parser.add_argument("--retailer-id", type=int, default=RETAILER_ID)
    parser.add_argument("--no-reset", action="store_true", help="Do not clear existing demo collections first")
    args = parser.parse_args()

    db.init_db()
    seed_demo_tvshop(args.retailer_id, reset=not args.no_reset)

    print(f"[Seed] Demo television data written for retailer_id={args.retailer_id}")
    print(f"[Seed] Cycle log id: {CYCLE_ID}")
    _print_catalog_spy_report(args.retailer_id)
    print("\n[Seed] SUCCESS: Mock historical data is in place! Run the agent API or dashboard to see CatalogSpy generate non-zero metrics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())