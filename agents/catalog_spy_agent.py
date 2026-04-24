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
#  Products that were in the competitor catalog previously but
#  not scraped this cycle — possibly discontinued or sold out.
#
#  Strength layers:
#    1. Skip if last_seen is TODAY (name-variation false positive)
#    2. Require minimum absence gap (7 days not seen)
#    3. Require minimum historical sightings (5+) to be "established"
#    4. Fuzzy name matching — don't flag minor name variations
#    5. Category relevance — skip obviously irrelevant products
# ─────────────────────────────────────────────────────────────────

_IRRELEVANT_KEYWORDS = {
    "playstation", "ps5", "ps4", "xbox", "nintendo", "airpods",
    "earbuds", "headphone", "speaker", "camera", "router", "printer",
    "laptop", "tablet", "ipad", "macbook", "keyboard", "mouse",
}

MIN_ABSENCE_DAYS    = 7     # Must be missing for at least 7 days
MIN_TIMES_SEEN      = 5     # Must have been seen at least 5 times to be "established"
FUZZY_MATCH_THRESH  = 0.75  # If a DB name is 75%+ similar to any scraped name, it's NOT discontinued


def _fuzzy_similar(name: str, name_set: set, threshold: float = FUZZY_MATCH_THRESH) -> bool:
    """Check if `name` is fuzzy-similar to any name in the set."""
    from difflib import SequenceMatcher
    name_lower = name.lower()
    for scraped_name in name_set:
        ratio = SequenceMatcher(None, name_lower, scraped_name).ratio()
        if ratio >= threshold:
            return True
    return False


def _is_irrelevant(name: str) -> bool:
    """Filter out products clearly outside the retailer's category."""
    name_lower = name.lower()
    return any(kw in name_lower for kw in _IRRELEVANT_KEYWORDS)


def _discontinued_detector(payload: dict) -> dict:
    scraped      = payload["scraped_records"]
    retailer_id  = payload["retailer_id"]

    # Get full competitor catalog from DB (all historical products)
    all_catalog = db.get_competitor_catalog(retailer_id)

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Index what was scraped this cycle: {competitor_name: set of product names}
    this_cycle: dict[str, set] = defaultdict(set)
    for r in scraped:
        comp = r.get("competitor_name", "")
        name = r.get("product_name_raw", "")[:250]
        if comp and name:
            this_cycle[comp].add(name.lower())

    discontinued = []
    for row in all_catalog:
        comp      = row.get("competitor_name", "")
        name      = row.get("product_name", "")
        last_seen = row.get("last_seen_at", "")

        if not last_seen or not comp or not name:
            continue

        # ── Layer 1: Skip if last seen TODAY ──────────────────────
        # If upsert_competitor_catalog just updated this row today,
        # it's NOT discontinued — just a name-variation mismatch.
        if last_seen[:10] == today_str:
            continue

        # ── Layer 2: Skip if competitor wasn't scraped this cycle ─
        if comp not in this_cycle:
            continue

        # ── Layer 3: Skip if product WAS found this cycle (exact) ─
        if name.lower() in this_cycle[comp]:
            continue

        # ── Layer 4: Fuzzy match — skip if name closely matches ───
        # Catches "Samsung 108 cm ... UA43F5" vs "Samsung 108 cm ... UA43F50"
        if _fuzzy_similar(name, this_cycle[comp]):
            continue

        # ── Layer 5: Skip irrelevant products ─────────────────────
        if _is_irrelevant(name):
            continue

        # ── Layer 6: Must be "established" — seen enough times ────
        times_seen = row.get("times_seen", 0)
        if times_seen < MIN_TIMES_SEEN:
            continue

        # ── Layer 7: Must have been absent long enough ────────────
        try:
            last_dt = datetime.fromisoformat(last_seen[:19])
            days_absent = (datetime.now() - last_dt).days
        except (ValueError, TypeError):
            continue

        if days_absent < MIN_ABSENCE_DAYS:
            continue

        discontinued.append({
            "type":         "discontinued",
            "competitor":   comp,
            "product":      name,
            "last_seen":    last_seen[:10],
            "times_seen":   times_seen,
            "days_absent":  days_absent,
        })

    # Sort by days_absent descending — longest-missing first
    discontinued.sort(key=lambda x: x.get("days_absent", 0), reverse=True)
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
        days = a.get("days_absent", "?")
        catalog_alerts.append({
            "type":     "discontinued",
            "severity": "low",
            "message":  (f"🔻 Possibly discontinued at {a['competitor']}: "
                         f"{a['product'][:50]} (absent {days} days, "
                         f"seen {a['times_seen']}x before)"),
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