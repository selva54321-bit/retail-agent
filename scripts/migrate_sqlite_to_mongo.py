"""
One-time migration tool: SQLite -> MongoDB for RetailAgent.

Usage examples:
  python scripts/migrate_sqlite_to_mongo.py
  python scripts/migrate_sqlite_to_mongo.py --sqlite-path retailagent.db --drop-first

Notes:
- This script is intended to be run once.
- By default, it stops if target Mongo collections already contain data.
- Use --drop-first to clear target collections before copying.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import database as mongo_db  # noqa: E402


TABLES = [
    "retailer_profiles",
    "competitor_registry",
    "price_history",
    "product_mappings",
    "analytics_results",
    "recommendations",
    "cycle_log",
    "competitor_catalog",
    "market_intelligence",
    "price_drop_patterns",
]

JSON_FIELDS = {
    "retailer_profiles": ["profile_json"],
    "competitor_registry": ["selector_config"],
    "analytics_results": ["competitor_prices_json"],
    "cycle_log": ["errors_json"],
    "market_intelligence": ["insights_json"],
}

BOOL_FIELDS = {
    "price_history": ["in_stock"],
    "competitor_catalog": ["in_stock"],
}


def _sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r[0] for r in cur.fetchall()}


def _coerce_json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            pass
    return default


def _normalize_row(table: str, row: dict) -> dict:
    doc = dict(row)

    # Preserve retailer_profiles.id because app relies on this integer id.
    # For other tables, keep sqlite id as metadata and let Mongo _id be native.
    if table != "retailer_profiles" and "id" in doc:
        doc["sqlite_id"] = doc.pop("id")

    for field in JSON_FIELDS.get(table, []):
        default = [] if field.endswith("errors_json") else {}
        doc[field] = _coerce_json(doc.get(field), default)

    for field in BOOL_FIELDS.get(table, []):
        if field in doc and doc[field] is not None:
            doc[field] = bool(doc[field])

    return doc


def _fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    cur = conn.execute(f"SELECT * FROM {table}")
    rows = [dict(r) for r in cur.fetchall()]
    return rows


def _sync_retailer_counter(db) -> None:
    top = db["retailer_profiles"].find_one(sort=[("id", -1)], projection={"id": 1})
    if top and "id" in top:
        db["counters"].update_one(
            {"_id": "retailer_profiles_id"},
            {"$set": {"seq": int(top["id"])}},
            upsert=True,
        )


def run(sqlite_path: str, drop_first: bool) -> int:
    sqlite_file = Path(sqlite_path)
    if not sqlite_file.exists():
        print(f"[Migration] SQLite file not found: {sqlite_file}")
        return 1

    print(f"[Migration] Source SQLite: {sqlite_file}")
    print(f"[Migration] Target MongoDB: {mongo_db.MONGODB_URI} (db: {mongo_db.MONGODB_DB})")

    mongo_db.init_db()
    mongo = mongo_db.get_conn()

    if not drop_first:
        non_empty = [
            name for name in TABLES
            if mongo[name].estimated_document_count() > 0
        ]
        if non_empty:
            print("[Migration] Aborted: target Mongo collections already contain data.")
            print("[Migration] Non-empty collections:")
            for name in non_empty:
                print(f"  - {name}")
            print("[Migration] Re-run with --drop-first to replace target data.")
            return 2

    conn = sqlite3.connect(str(sqlite_file))
    conn.row_factory = sqlite3.Row

    existing_tables = _sqlite_tables(conn)
    summary: list[tuple[str, int]] = []

    try:
        for table in TABLES:
            if table not in existing_tables:
                print(f"[Migration] Skip {table}: not present in SQLite")
                summary.append((table, 0))
                continue

            rows = _fetch_rows(conn, table)
            if not rows:
                print(f"[Migration] {table}: 0 rows")
                summary.append((table, 0))
                continue

            if drop_first:
                mongo[table].delete_many({})

            docs = [_normalize_row(table, r) for r in rows]
            mongo[table].insert_many(docs, ordered=False)
            print(f"[Migration] {table}: copied {len(docs)} rows")
            summary.append((table, len(docs)))

        _sync_retailer_counter(mongo)

    finally:
        conn.close()

    total = sum(n for _, n in summary)
    print("\n[Migration] Complete")
    print(f"[Migration] Total rows copied: {total}")
    for name, count in summary:
        print(f"  - {name}: {count}")

    return 0


def main() -> int:
    default_sqlite = os.environ.get("RETAILAGENT_SQLITE_PATH", os.environ.get("RETAILAGENT_DB", "retailagent.db"))

    parser = argparse.ArgumentParser(description="One-time SQLite to MongoDB migration")
    parser.add_argument("--sqlite-path", default=default_sqlite, help="Path to old SQLite DB file")
    parser.add_argument("--drop-first", action="store_true", help="Clear target Mongo collections before insert")
    args = parser.parse_args()

    return run(sqlite_path=args.sqlite_path, drop_first=args.drop_first)


if __name__ == "__main__":
    raise SystemExit(main())
