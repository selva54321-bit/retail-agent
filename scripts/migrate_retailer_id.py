from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import database as db


DIRECT_UPDATE_COLLECTIONS = [
    "price_history",
    "analytics_results",
    "recommendations",
    "cycle_log",
    "market_intelligence",
    "demand_forecasts",
]


MERGE_COLLECTIONS = {
    "competitor_registry": ["url", "catalog_sku"],
    "product_mappings": ["retailer_sku", "competitor_name", "competitor_product_name"],
    "competitor_catalog": ["competitor_name", "product_name"],
    "price_drop_patterns": ["competitor_name", "catalog_sku"],
}


SUM_FIELDS = {
    "times_seen",
    "times_out_of_stock",
    "drop_count",
    "total_observations",
    "flash_sales_count",
    "price_change_count",
}

MAX_TS_FIELDS = {
    "updated_at",
    "last_seen_at",
    "last_scraped",
    "computed_at",
    "ended_at",
    "started_at",
    "matched_at",
    "last_drop_date",
    "next_predicted_date",
}

MIN_TS_FIELDS = {
    "created_at",
    "first_seen_at",
}


def _max_str(a, b):
    if not a:
        return b
    if not b:
        return a
    return a if str(a) >= str(b) else b


def _min_str(a, b):
    if not a:
        return b
    if not b:
        return a
    return a if str(a) <= str(b) else b


def _merge_docs(collection: str, target: dict, source: dict) -> dict:
    merged = dict(target)

    for k, sv in source.items():
        if k in {"_id", "retailer_id"}:
            continue

        tv = merged.get(k)

        if k in SUM_FIELDS and isinstance(tv, (int, float)) and isinstance(sv, (int, float)):
            merged[k] = tv + sv
            continue

        if k in MAX_TS_FIELDS:
            merged[k] = _max_str(tv, sv)
            continue

        if k in MIN_TS_FIELDS:
            merged[k] = _min_str(tv, sv)
            continue

        if isinstance(tv, dict) and isinstance(sv, dict):
            merged[k] = {**tv, **sv}
            continue

        if tv in (None, "", [], {}):
            merged[k] = sv

    # Collection-specific merge refinements.
    if collection == "competitor_catalog":
        # Prefer the most recent price/in_stock snapshot.
        tgt_last = target.get("last_seen_at", "")
        src_last = source.get("last_seen_at", "")
        if str(src_last) > str(tgt_last):
            if "price" in source:
                merged["price"] = source.get("price")
            if "in_stock" in source:
                merged["in_stock"] = source.get("in_stock")
            if source.get("catalog_sku"):
                merged["catalog_sku"] = source.get("catalog_sku")

    if collection == "product_mappings":
        # Keep mapping with better similarity or latest match timestamp.
        tgt_score = float(target.get("similarity_score", 0) or 0)
        src_score = float(source.get("similarity_score", 0) or 0)
        if src_score >= tgt_score:
            merged["similarity_score"] = source.get("similarity_score")
            merged["competitor_price"] = source.get("competitor_price")
            merged["match_method"] = source.get("match_method") or target.get("match_method")

    merged.pop("_id", None)
    merged["retailer_id"] = target.get("retailer_id", 1)
    return merged


def migrate_retailer_id(src_id: int, dst_id: int, apply: bool = False) -> dict:
    database = db.get_conn()
    report = {
        "source": src_id,
        "target": dst_id,
        "apply": apply,
        "started_at": datetime.now().isoformat(),
        "direct_updates": {},
        "merged_updates": {},
    }

    # 1) Direct update collections (no unique conflicts on retailer_id).
    for name in DIRECT_UPDATE_COLLECTIONS:
        coll = database[name]
        src_count = coll.count_documents({"retailer_id": src_id})
        if apply:
            result = coll.update_many({"retailer_id": src_id}, {"$set": {"retailer_id": dst_id}})
            modified = int(result.modified_count)
        else:
            modified = int(src_count)
        report["direct_updates"][name] = {
            "source_docs": int(src_count),
            "modified": modified,
        }

    # 2) Merge collections where unique indexes include retailer_id.
    for name, key_fields in MERGE_COLLECTIONS.items():
        coll = database[name]
        src_docs = list(coll.find({"retailer_id": src_id}))
        inserted = 0
        merged = 0

        for sdoc in src_docs:
            key_filter = {"retailer_id": dst_id}
            for k in key_fields:
                key_filter[k] = sdoc.get(k, "")

            tdoc = coll.find_one(key_filter)
            if tdoc is None:
                if apply:
                    ndoc = dict(sdoc)
                    ndoc.pop("_id", None)
                    ndoc["retailer_id"] = dst_id
                    coll.insert_one(ndoc)
                    coll.delete_one({"_id": sdoc["_id"]})
                inserted += 1
            else:
                if apply:
                    merged_doc = _merge_docs(name, tdoc, sdoc)
                    coll.update_one({"_id": tdoc["_id"]}, {"$set": merged_doc})
                    coll.delete_one({"_id": sdoc["_id"]})
                merged += 1

        report["merged_updates"][name] = {
            "source_docs": len(src_docs),
            "inserted": inserted,
            "merged": merged,
        }

    report["finished_at"] = datetime.now().isoformat()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Move records from one retailer_id to another in MongoDB")
    parser.add_argument("--from-id", type=int, default=0, dest="from_id")
    parser.add_argument("--to-id", type=int, required=True, dest="to_id")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    args = parser.parse_args()

    db.init_db()
    report = migrate_retailer_id(args.from_id, args.to_id, apply=args.apply)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n[MIGRATION] Mode={mode} from retailer_id={args.from_id} to retailer_id={args.to_id}")

    print("\nDirect updates:")
    for name, item in report["direct_updates"].items():
        print(
            f"  - {name:22} source={item['source_docs']:4d} modified={item['modified']:4d}"
        )

    print("\nMerge updates:")
    for name, item in report["merged_updates"].items():
        print(
            f"  - {name:22} source={item['source_docs']:4d} inserted={item['inserted']:4d} merged={item['merged']:4d}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
