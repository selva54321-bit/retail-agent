"""
RetailAgent — Catalog Spy Agent
================================
Runs after the Analyst. Looks at ALL scraped products (not just the ones
that matched your catalog) to answer three questions:

1. Stock Availability:  Which of your catalog products went out of stock
                        on which competitors? (Frequent stock-outs = high demand)

2. New Arrivals:        What products are competitors selling that you don't carry?
                        (New model numbers never seen before this cycle)

3. Discontinued:        What products did a competitor carry last cycle but not
                        this one? (Clearance opportunity — buy their leftover stock)

LangChain pattern: RunnableLambda pipeline (same as analyst)
  _stock_tracker | _new_arrival_detector | _discontinued_detector | _alert_builder

Data flow:
  scraped_records → upsert competitor_catalog DB
  → compare with previous cycle data
  → build catalog_alerts list
  → intel_insights["fast_movers"] populated
"""

from collections import defaultdict
from datetime    import datetime
import re

from langchain_core.runnables import RunnableLambda

from core.state import AgentState
from core       import database as db


# ─────────────────────────────────────────────────────────────────
#  MODULE 1 — Stock Tracker
#  Upserts all scraped records to competitor_catalog.
#  Identifies your catalog products that went out of stock.
# ─────────────────────────────────────────────────────────────────

def _stock_tracker(payload: dict) -> dict:
    scraped     = payload["scraped_records"]
    retailer_id = payload["retailer_id"]
    catalog_skus = payload["catalog_skus"]   # set of user's own SKUs

    # Upsert everything — catalog products AND competitor-only products
    db.upsert_competitor_catalog(retailer_id, scraped)

    # Find your catalog products that are showing as out-of-stock
    stock_alerts = []
    for r in scraped:
        sku      = r.get("catalog_sku", "")
        in_stock = r.get("in_stock", True)
        if sku in catalog_skus and not in_stock:
            stock_alerts.append({
                "type":       "stock_out",
                "competitor": r.get("competitor_name", ""),
                "product":    r.get("catalog_product_name") or r.get("product_name_raw", ""),
                "sku":        sku,
            })

    # Find frequently stocked-out products (across all cycles) — high demand signal
    frequent_oos = db.get_frequent_stockouts(retailer_id, min_stockouts=2)
    fast_movers  = [
        {"product": r["product_name"], "competitor": r["competitor_name"],
         "stockout_rate": round(r.get("stockout_rate", 0), 2),
         "times_out": r["times_out_of_stock"]}
        for r in frequent_oos[:10]
    ]

    payload["stock_alerts"]   = stock_alerts
    payload["fast_movers"]    = fast_movers
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 2 — New Arrival Detector
#  Products seen at a competitor for the first time this cycle
#  that are NOT in the user's catalog.
# ─────────────────────────────────────────────────────────────────

def _new_arrival_detector(payload: dict) -> dict:
    retailer_id = payload["retailer_id"]
    catalog_skus = payload["catalog_skus"]

    # Products first seen in last 25 hours (this cycle) with no catalog match
    new_products = db.get_new_competitor_products(retailer_id, since_hours=25)

    new_arrivals = []
    seen_names   = set()

    for r in new_products:
        name = r.get("product_name", "")
        comp = r.get("competitor_name", "")
        key  = f"{comp}:{name[:40].lower()}"

        if key in seen_names:
            continue
        seen_names.add(key)

        # Skip if it's actually one of the user's catalog products
        if r.get("catalog_sku", "") in catalog_skus:
            continue

        new_arrivals.append({
            "type":       "new_arrival",
            "competitor": comp,
            "product":    name,
            "price":      r.get("price", 0),
        })

    payload["new_arrivals"] = new_arrivals[:10]   # cap to 10 most recent
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 3 — Discontinued Detector
#  A product is "possibly discontinued" only when:
#    a) It was seen at least 5 times (established product, not a one-off)
#    b) It has NOT appeared in the last 7 days
#    c) The competitor WAS scraped this cycle (so absence is real)
#    d) No fuzzy-name match exists in this cycle's results either
#       (handles minor name changes like "Limited Edition" suffix)
# ─────────────────────────────────────────────────────────────────

def _fuzzy_name_match(name_a: str, name_b: str, threshold: float = 0.80) -> bool:
    """
    Simple token-overlap fuzzy match.
    Returns True if the two names share enough tokens to be the same product.
    e.g. "LG 32 inch HD TV" vs "LG 32 Inch HD Smart TV (2024)" → True
    """
    STOP = {"inch", "inches", "cm", "the", "a", "an", "and", "or",
            "for", "in", "on", "of", "with", "smart", "led", "tv"}

    def tokens(s: str) -> set:
        return {t.lower() for t in re.split(r'\W+', s)
                if t and len(t) > 1 and t.lower() not in STOP}

    ta, tb = tokens(name_a), tokens(name_b)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= threshold


def _discontinued_detector(payload: dict) -> dict:
    scraped     = payload["scraped_records"]
    retailer_id = payload["retailer_id"]
    now         = datetime.now()

    # ── What was scraped this cycle per competitor ─────────────────
    # {competitor_name: [product_name_raw, ...]}
    this_cycle: dict[str, list[str]] = defaultdict(list)
    for r in scraped:
        comp = r.get("competitor_name", "")
        name = r.get("product_name_raw", "")[:250]
        if comp and name:
            this_cycle[comp].append(name)

    # ── Get established products from DB ──────────────────────────
    all_catalog = db.get_competitor_catalog(retailer_id)

    discontinued = []
    for row in all_catalog:
        comp      = row.get("competitor_name", "")
        name      = row.get("product_name", "")
        last_seen = row.get("last_seen_at", "")
        times_seen = row.get("times_seen", 0)

        # Guard 1: competitor must have been scraped this cycle
        if comp not in this_cycle:
            continue

        # Guard 2: must be an established product (seen 5+ times)
        if times_seen < 5:
            continue

        # Guard 3: must be absent for 7+ days
        if not last_seen:
            continue
        try:
            last_seen_dt = datetime.fromisoformat(last_seen[:19])
            days_absent  = (now - last_seen_dt).days
        except Exception:
            continue

        if days_absent < 7:
            continue

        # Guard 4: fuzzy check — maybe it's still there under a slightly different name
        cycle_names = this_cycle[comp]
        fuzzy_found = any(_fuzzy_name_match(name, cn, threshold=0.65) for cn in cycle_names)
        if fuzzy_found:
            continue

        discontinued.append({
            "type":       "discontinued",
            "competitor": comp,
            "product":    name,
            "last_seen":  last_seen[:10],
            "days_absent": days_absent,
            "times_seen": times_seen,
        })

    # Sort by days absent descending — longest missing first
    discontinued.sort(key=lambda x: x["days_absent"], reverse=True)
    payload["discontinued"] = discontinued[:10]
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 4 — Alert Builder
#  Converts all findings into catalog_alerts list
# ─────────────────────────────────────────────────────────────────

def _alert_builder(payload: dict) -> dict:
    now             = datetime.now().isoformat()
    stock_alerts    = payload.get("stock_alerts", [])
    new_arrivals    = payload.get("new_arrivals", [])
    discontinued    = payload.get("discontinued", [])
    fast_movers     = payload.get("fast_movers", [])

    catalog_alerts = []

    for a in stock_alerts:
        catalog_alerts.append({
            "type":     "stock_out",
            "severity": "medium",
            "message":  (f"📦 Out of stock: {a['product'][:50]} "
                         f"at {a['competitor']}"),
            "data":     a,
            "at":       now,
        })

    for a in new_arrivals:
        catalog_alerts.append({
            "type":     "new_arrival",
            "severity": "medium",
            "message":  (f"🆕 New at {a['competitor']}: {a['product'][:50]} "
                         f"(₹{a['price']:,.0f}) — you don't carry this"),
            "data":     a,
            "at":       now,
        })

    for a in discontinued:
        days = a.get("days_absent", 0)
        catalog_alerts.append({
            "type":     "discontinued",
            "severity": "low",
            "message":  (f"🔻 Possibly discontinued at {a['competitor']}: "
                         f"{a['product'][:50]} "
                         f"(absent {days}d, last seen {a['last_seen']})"),
            "data":     a,
            "at":       now,
        })

    payload["catalog_alerts"] = catalog_alerts
    payload["fast_movers"]    = fast_movers
    return payload


# ─── Compose RunnableLambda pipeline ─────────────────────────────

_catalog_spy_pipeline = (
    RunnableLambda(_stock_tracker)
    | RunnableLambda(_new_arrival_detector)
    | RunnableLambda(_discontinued_detector)
    | RunnableLambda(_alert_builder)
)


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_catalog_spy_node(state: AgentState) -> dict:
    """
    LangGraph node: Catalog Spy Agent.

    Tracks everything competitors are selling — not just your catalog.
    Outputs:
      catalog_alerts → stock-outs, new arrivals, discontinued products
      intel_insights["fast_movers"] → frequently out-of-stock products
    """
    scraped     = state["scraped_records"]
    retailer_id = state["retailer_id"]
    catalog     = state["retailer_profile"].catalog

    print(f"\n[CatalogSpy] Analyzing {len(scraped)} scraped records...")

    if not scraped:
        return {
            "catalog_alerts": [],
            "intel_insights": {},
            "current_node":   "catalog_spy",
        }

    catalog_skus = {p.get("sku", "") for p in catalog if p.get("sku")}

    result = _catalog_spy_pipeline.invoke({
        "scraped_records": scraped,
        "retailer_id":     retailer_id,
        "catalog_skus":    catalog_skus,
    })

    catalog_alerts = result.get("catalog_alerts", [])
    fast_movers    = result.get("fast_movers", [])
    new_arrivals   = [a for a in catalog_alerts if a["type"] == "new_arrival"]
    stock_outs     = [a for a in catalog_alerts if a["type"] == "stock_out"]
    discontinued   = [a for a in catalog_alerts if a["type"] == "discontinued"]

    print(f"[CatalogSpy] {len(new_arrivals)} new arrivals | "
          f"{len(stock_outs)} stock-outs | "
          f"{len(discontinued)} possibly discontinued | "
          f"{len(fast_movers)} fast movers")

    for a in catalog_alerts:
        print(f"  {a['message']}")

    return {
        "catalog_alerts": catalog_alerts,
        "intel_insights": {"fast_movers": fast_movers},
        "current_node":   "catalog_spy",
    }