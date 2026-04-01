"""
RetailAgent — Database Layer
==============================
SQLite-backed persistence for retailer profiles, competitor registry,
price history, product mappings, and recommendations.
All agents read/write through this module.
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Optional

DB_PATH = os.environ.get("RETAILAGENT_DB", "retailagent.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create all tables on first run."""
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS retailer_profiles (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        store_name  TEXT NOT NULL,
        profile_json TEXT NOT NULL,
        created_at  TEXT,
        updated_at  TEXT
    );

    CREATE TABLE IF NOT EXISTS competitor_registry (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer_id         INTEGER,
        competitor_name     TEXT,
        url                 TEXT,
        priority            TEXT DEFAULT 'medium',
        scan_interval_hours INTEGER DEFAULT 24,
        scrape_method       TEXT DEFAULT 'static',
        product_category    TEXT,
        selector_config     TEXT DEFAULT '{}',
        last_scraped        TEXT,
        consecutive_failures INTEGER DEFAULT 0,
        active              INTEGER DEFAULT 1,
        source              TEXT DEFAULT 'planner',
        notes               TEXT DEFAULT '',
        catalog_sku          TEXT DEFAULT '',
        catalog_product_name TEXT DEFAULT '',
        UNIQUE(retailer_id, url, catalog_sku)
    );

    CREATE TABLE IF NOT EXISTS price_history (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer_id         INTEGER,
        competitor_name     TEXT,
        competitor_url      TEXT,
        product_name_raw    TEXT,
        price               REAL,
        original_price      REAL,
        in_stock            INTEGER DEFAULT 1,
        scraped_at          TEXT,
        confidence          TEXT DEFAULT 'high',
        scrape_method_used  TEXT DEFAULT 'static',
        catalog_sku         TEXT DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_price_history_competitor
        ON price_history(retailer_id, competitor_name, product_name_raw, scraped_at);

    CREATE TABLE IF NOT EXISTS product_mappings (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer_id             INTEGER,
        retailer_sku            TEXT,
        retailer_product_name   TEXT,
        competitor_name         TEXT,
        competitor_product_name TEXT,
        competitor_price        REAL,
        similarity_score        REAL,
        match_method            TEXT,
        matched_at              TEXT,
        UNIQUE(retailer_id, retailer_sku, competitor_name, competitor_product_name)
    );

    CREATE TABLE IF NOT EXISTS analytics_results (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer_id             INTEGER,
        cycle_id                TEXT,
        retailer_sku            TEXT,
        product_name            TEXT,
        retailer_price          REAL,
        competitor_prices_json  TEXT,
        min_competitor_price    REAL,
        avg_competitor_price    REAL,
        max_competitor_price    REAL,
        price_rank              INTEGER,
        total_competitors       INTEGER,
        price_gap_to_min        REAL,
        price_gap_pct_to_min    REAL,
        trend                   TEXT,
        is_anomaly              INTEGER DEFAULT 0,
        anomaly_reason          TEXT,
        computed_at             TEXT
    );

    CREATE TABLE IF NOT EXISTS recommendations (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer_id         INTEGER,
        cycle_id            TEXT,
        retailer_sku        TEXT,
        product_name        TEXT,
        current_price       REAL,
        recommended_price   REAL,
        price_change        REAL,
        price_change_pct    REAL,
        action              TEXT,
        confidence          REAL,
        reasoning           TEXT,
        guardrail_applied   INTEGER DEFAULT 0,
        guardrail_note      TEXT,
        approved            INTEGER,
        created_at          TEXT
    );

    CREATE TABLE IF NOT EXISTS cycle_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        retailer_id     INTEGER,
        cycle_id        TEXT,
        started_at      TEXT,
        ended_at        TEXT,
        status          TEXT,
        records_scraped INTEGER DEFAULT 0,
        matches_found   INTEGER DEFAULT 0,
        recommendations_made INTEGER DEFAULT 0,
        briefing        TEXT,
        errors_json     TEXT DEFAULT '[]'
    );
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Initialized at {DB_PATH}")


# ─── RETAILER PROFILE ────────────────────────

def save_retailer_profile(profile_dict: dict) -> int:
    conn = get_conn()
    now = datetime.now().isoformat()
    existing = conn.execute(
        "SELECT id FROM retailer_profiles WHERE store_name=?",
        (profile_dict["store_name"],)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE retailer_profiles SET profile_json=?, updated_at=? WHERE id=?",
            (json.dumps(profile_dict), now, existing["id"])
        )
        rid = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO retailer_profiles(store_name, profile_json, created_at, updated_at) VALUES(?,?,?,?)",
            (profile_dict["store_name"], json.dumps(profile_dict), now, now)
        )
        rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def load_retailer_profile(retailer_id: int) -> Optional[dict]:
    conn = get_conn()
    row = conn.execute(
        "SELECT profile_json FROM retailer_profiles WHERE id=?", (retailer_id,)
    ).fetchone()
    conn.close()
    return json.loads(row["profile_json"]) if row else None


def list_retailer_profiles() -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, store_name, updated_at FROM retailer_profiles ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── COMPETITOR REGISTRY ─────────────────────

def upsert_competitor(retailer_id: int, target: dict):
    conn = get_conn()
    conn.execute("""
        INSERT INTO competitor_registry
            (retailer_id, competitor_name, url, priority, scan_interval_hours,
             scrape_method, product_category, selector_config, source, notes,
             catalog_sku, catalog_product_name)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(retailer_id, url, catalog_sku) DO UPDATE SET
            priority=excluded.priority,
            scan_interval_hours=excluded.scan_interval_hours,
            scrape_method=excluded.scrape_method,
            product_category=excluded.product_category,
            selector_config=excluded.selector_config,
            source=excluded.source,
            notes=excluded.notes,
            catalog_sku=excluded.catalog_sku,
            catalog_product_name=excluded.catalog_product_name
    """, (
        retailer_id,
        target.get("competitor_name", ""),
        target.get("url", ""),
        target.get("priority", "medium"),
        target.get("scan_interval_hours", 24),
        target.get("scrape_method", "static"),
        target.get("product_category", ""),
        json.dumps(target.get("selector_config", {})),
        target.get("source", "planner"),
        target.get("notes", ""),
        target.get("catalog_sku", ""),
        target.get("catalog_product_name", ""),
    ))
    conn.commit()
    conn.close()


def get_competitors(retailer_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM competitor_registry WHERE retailer_id=? AND active=1",
        (retailer_id,)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["selector_config"] = json.loads(d.get("selector_config") or "{}")
        result.append(d)
    return result


def mark_scrape_result(retailer_id: int, url: str, success: bool):
    conn = get_conn()
    if success:
        conn.execute(
            "UPDATE competitor_registry SET last_scraped=?, consecutive_failures=0 WHERE retailer_id=? AND url=?",
            (datetime.now().isoformat(), retailer_id, url)
        )
    else:
        conn.execute(
            "UPDATE competitor_registry SET consecutive_failures=consecutive_failures+1 WHERE retailer_id=? AND url=?",
            (retailer_id, url)
        )
    conn.commit()
    conn.close()


# ─── PRICE HISTORY ───────────────────────────

def save_price_records(retailer_id: int, records: list):
    if not records:
        return
    conn = get_conn()
    conn.executemany("""
        INSERT INTO price_history
            (retailer_id, competitor_name, competitor_url, product_name_raw,
             price, original_price, in_stock, scraped_at, confidence,
             scrape_method_used, catalog_sku)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, [
        (retailer_id,
         r.get("competitor_name"), r.get("competitor_url"),
         r.get("product_name_raw"), r.get("price"), r.get("original_price"),
         int(r.get("in_stock", True)), r.get("scraped_at"),
         r.get("confidence", "high"), r.get("scrape_method_used", "static"),
         r.get("catalog_sku", ""))
        for r in records
    ])
    conn.commit()
    conn.close()


def get_price_history(retailer_id: int, competitor_name: str,
                      product_name: str, days: int = 30) -> list:
    conn = get_conn()
    rows = conn.execute("""
        SELECT price, scraped_at FROM price_history
        WHERE retailer_id=? AND competitor_name=? AND product_name_raw LIKE ?
          AND scraped_at >= datetime('now', ?)
        ORDER BY scraped_at ASC
    """, (retailer_id, competitor_name, f"%{product_name[:20]}%", f"-{days} days")).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_prices(retailer_id: int) -> list:
    """Get the most recent price per competitor per product."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT competitor_name, product_name_raw, price, original_price,
               in_stock, scraped_at, confidence
        FROM price_history p1
        WHERE retailer_id=?
          AND scraped_at = (
              SELECT MAX(p2.scraped_at)
              FROM price_history p2
              WHERE p2.retailer_id=p1.retailer_id
                AND p2.competitor_name=p1.competitor_name
                AND p2.product_name_raw=p1.product_name_raw
          )
        ORDER BY competitor_name, product_name_raw
    """, (retailer_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── PRODUCT MAPPINGS ────────────────────────

def save_product_mapping(retailer_id: int, mapping: dict):
    conn = get_conn()
    conn.execute("""
        INSERT INTO product_mappings
            (retailer_id, retailer_sku, retailer_product_name,
             competitor_name, competitor_product_name, competitor_price,
             similarity_score, match_method, matched_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(retailer_id, retailer_sku, competitor_name, competitor_product_name)
        DO UPDATE SET competitor_price=excluded.competitor_price,
                      similarity_score=excluded.similarity_score,
                      matched_at=excluded.matched_at
    """, (
        retailer_id,
        mapping["retailer_sku"], mapping["retailer_product_name"],
        mapping["competitor_name"], mapping["competitor_product_name"],
        mapping["competitor_price"], mapping["similarity_score"],
        mapping["match_method"], datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


def get_product_mappings(retailer_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM product_mappings WHERE retailer_id=?", (retailer_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── RECOMMENDATIONS ─────────────────────────

def save_recommendations(retailer_id: int, cycle_id: str, recs: list):
    if not recs:
        return
    conn = get_conn()
    conn.executemany("""
        INSERT INTO recommendations
            (retailer_id, cycle_id, retailer_sku, product_name,
             current_price, recommended_price, price_change, price_change_pct,
             action, confidence, reasoning, guardrail_applied, guardrail_note,
             approved, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [
        (retailer_id, cycle_id,
         r.get("retailer_sku"), r.get("product_name"),
         r.get("current_price"), r.get("recommended_price"),
         r.get("price_change"), r.get("price_change_pct"),
         r.get("action"), r.get("confidence"),
         r.get("reasoning"), int(r.get("guardrail_applied", False)),
         r.get("guardrail_note", ""), r.get("approved"),
         r.get("created_at", datetime.now().isoformat()))
        for r in recs
    ])
    conn.commit()
    conn.close()


def get_pending_recommendations(retailer_id: int) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE retailer_id=? AND approved IS NULL ORDER BY created_at DESC",
        (retailer_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_recommendations(retailer_id: int, limit: int = 50) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE retailer_id=? ORDER BY created_at DESC LIMIT ?",
        (retailer_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── CYCLE LOG ───────────────────────────────

def save_cycle_log(retailer_id: int, cycle: dict):
    conn = get_conn()
    conn.execute("""
        INSERT INTO cycle_log
            (retailer_id, cycle_id, started_at, ended_at, status,
             records_scraped, matches_found, recommendations_made,
             briefing, errors_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        retailer_id,
        cycle.get("cycle_id"), cycle.get("started_at"), cycle.get("ended_at"),
        cycle.get("status", "completed"),
        cycle.get("records_scraped", 0), cycle.get("matches_found", 0),
        cycle.get("recommendations_made", 0), cycle.get("briefing", ""),
        json.dumps(cycle.get("errors", []))
    ))
    conn.commit()
    conn.close()


def get_recent_cycles(retailer_id: int, limit: int = 10) -> list:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM cycle_log WHERE retailer_id=? ORDER BY started_at DESC LIMIT ?",
        (retailer_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]