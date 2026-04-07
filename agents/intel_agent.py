"""
RetailAgent — Intel Agent (Competitive Intelligence)
======================================================
Runs after Catalog Spy. Analyses the 30-cycle price history to derive
deeper strategic insights about each competitor's behaviour.

Four intelligence modules (RunnableLambda pipeline):

1. Strategy Classifier
   Compares each competitor's prices to the market average over 30 cycles.
   Labels each as one of:
     price_leader       — consistently cheapest (>50% of time)
     discount_aggressor — frequent steep drops (flash sales >10%)
     price_follower     — tracks average, rarely leads
     premium_anchor     — consistently above average

2. Flash Sale Detector
   If a competitor dropped a product price by >12% vs their own last-cycle
   price, flag it as a potential flash sale (short-term, don't match it).

3. Price Pattern Analyser
   Detects day-of-week patterns:
   "Poorvika drops prices on Fridays" — uses price_history timestamps.

4. Growth Opportunity Finder
   Combines fast_movers (from catalog_spy) + new_arrivals to suggest
   which products the user should consider stocking.

LangChain pattern: RunnableLambda pipeline composed with |
Persists results to market_intelligence DB table.
"""

import json
from collections import defaultdict
from datetime    import datetime

from langchain_core.runnables import RunnableLambda

from core.state import AgentState
from core       import database as db


# ─────────────────────────────────────────────────────────────────
#  MODULE 1 — Competitor Strategy Classifier
# ─────────────────────────────────────────────────────────────────

def _strategy_classifier(payload: dict) -> dict:
    """
    Classify each competitor's pricing strategy based on 30-cycle history.
    Compares their prices against the market average for each product.
    """
    retailer_id = payload["retailer_id"]
    analytics   = payload["analytics"]   # current cycle analytics per SKU

    strategies: dict[str, dict] = {}

    # Build market averages from current cycle analytics
    market_avgs: dict[str, float] = {}
    for a in analytics:
        market_avgs[a["product_name"]] = a.get("avg_competitor_price", 0)

    # Get all active competitors from this cycle's scraped data
    competitors = {r.get("competitor_name", "")
                   for r in payload["scraped_records"] if r.get("competitor_name")}

    for comp in competitors:
        if not comp:
            continue

        history = db.get_price_history_for_intel(retailer_id, comp, days=30)
        if len(history) < 3:
            strategies[comp] = {
                "competitor_name":   comp,
                "strategy_label":    "unknown",
                "avg_price_gap_pct": 0.0,
                "price_change_count": 0,
                "flash_sales_count": 0,
                "insights":          {"note": "Insufficient history"},
            }
            continue

        # Group history by product
        by_product: dict[str, list] = defaultdict(list)
        for row in history:
            by_product[row["product_name_raw"]].append(row)

        gap_pcts      = []
        change_count  = 0
        below_market  = 0
        above_market  = 0
        total_products = 0

        for prod_name, rows in by_product.items():
            market_avg = market_avgs.get(prod_name)
            if not market_avg:
                # Try partial name match
                for key, avg in market_avgs.items():
                    if key[:20].lower() in prod_name.lower():
                        market_avg = avg
                        break

            if not market_avg:
                continue

            prices = [float(r["price"]) for r in rows if r.get("price")]
            if not prices:
                continue

            avg_price = sum(prices) / len(prices)
            gap_pct   = (avg_price - market_avg) / market_avg if market_avg else 0
            gap_pcts.append(gap_pct)

            # Count price changes
            for i in range(1, len(prices)):
                if abs(prices[i] - prices[i-1]) / max(prices[i-1], 1) > 0.01:
                    change_count += 1

            if avg_price < market_avg * 0.97:
                below_market += 1
            elif avg_price > market_avg * 1.03:
                above_market += 1

            total_products += 1

        avg_gap = sum(gap_pcts) / len(gap_pcts) if gap_pcts else 0.0

        # Classify strategy
        if total_products == 0:
            label = "unknown"
        elif below_market / max(total_products, 1) >= 0.5:
            label = "price_leader"
        elif above_market / max(total_products, 1) >= 0.5:
            label = "premium_anchor"
        elif change_count / max(total_products, 1) >= 3:
            label = "discount_aggressor"
        else:
            label = "price_follower"

        strategies[comp] = {
            "competitor_name":    comp,
            "strategy_label":     label,
            "avg_price_gap_pct":  round(avg_gap * 100, 2),
            "price_change_count": change_count,
            "flash_sales_count":  0,   # filled by next module
            "insights": {
                "below_market_products": below_market,
                "above_market_products": above_market,
                "total_products_analysed": total_products,
            },
        }

    payload["strategies"] = strategies
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 2 — Flash Sale Detector
# ─────────────────────────────────────────────────────────────────

def _flash_sale_detector(payload: dict) -> dict:
    """
    Detect flash sales: competitor dropped a product >12% vs their own
    previous cycle price. These are temporary — don't recommend matching.
    """
    retailer_id  = payload["retailer_id"]
    scraped      = payload["scraped_records"]
    strategies   = payload["strategies"]
    flash_events = []

    # Group current scraped prices by competitor+product
    current: dict[str, dict[str, float]] = defaultdict(dict)
    for r in scraped:
        comp  = r.get("competitor_name", "")
        pname = r.get("product_name_raw", "")[:100]
        price = r.get("price", 0)
        if comp and pname and price:
            current[comp][pname] = float(price)

    for comp, products in current.items():
        history = db.get_price_history_for_intel(retailer_id, comp, days=3)

        # Build previous cycle prices (exclude today)
        prev: dict[str, float] = {}
        for row in history:
            pname = row.get("product_name_raw", "")[:100]
            price = float(row.get("price", 0))
            # Keep the most recent previous price (not today's)
            scraped_at = row.get("scraped_at", "")
            today = datetime.now().strftime("%Y-%m-%d")
            if scraped_at[:10] < today and pname and price:
                prev[pname] = price

        for pname, cur_price in products.items():
            if pname not in prev:
                continue
            prev_price = prev[pname]
            if prev_price <= 0:
                continue
            drop_pct = (prev_price - cur_price) / prev_price

            if drop_pct >= 0.12:   # 12% or more drop
                event = {
                    "competitor": comp,
                    "product":    pname,
                    "prev_price": prev_price,
                    "cur_price":  cur_price,
                    "drop_pct":   round(drop_pct * 100, 1),
                }
                flash_events.append(event)
                if comp in strategies:
                    strategies[comp]["flash_sales_count"] += 1
                    strategies[comp]["insights"]["latest_flash_sale"] = event

    payload["flash_events"] = flash_events
    payload["strategies"]   = strategies
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 3 — Price Pattern Analyser
# ─────────────────────────────────────────────────────────────────

DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

def _price_pattern_analyser(payload: dict) -> dict:
    """
    Detect day-of-week price drop patterns.
    e.g. "Poorvika drops prices most often on Fridays"
    """
    retailer_id = payload["retailer_id"]
    strategies  = payload["strategies"]

    for comp in list(strategies.keys()):
        history = db.get_price_history_for_intel(retailer_id, comp, days=30)
        if len(history) < 7:
            continue

        # Count price drops by day of week
        drops_by_day: dict[int, int] = defaultdict(int)
        rows_by_product: dict[str, list] = defaultdict(list)

        for row in history:
            pname = row.get("product_name_raw", "")[:60]
            rows_by_product[pname].append(row)

        for pname, rows in rows_by_product.items():
            rows_sorted = sorted(rows, key=lambda x: x.get("scraped_at",""))
            for i in range(1, len(rows_sorted)):
                try:
                    prev_p = float(rows_sorted[i-1].get("price", 0))
                    cur_p  = float(rows_sorted[i].get("price", 0))
                    if prev_p > 0 and (prev_p - cur_p) / prev_p > 0.02:
                        dt_str = rows_sorted[i].get("scraped_at", "")[:10]
                        day_num = datetime.strptime(dt_str, "%Y-%m-%d").weekday()
                        drops_by_day[day_num] += 1
                except Exception:
                    continue

        if drops_by_day:
            peak_day = max(drops_by_day, key=drops_by_day.get)
            peak_count = drops_by_day[peak_day]
            total_drops = sum(drops_by_day.values())

            if total_drops >= 3 and peak_count / total_drops >= 0.35:
                pattern = f"Drops prices most often on {DAY_NAMES[peak_day]}s"
                strategies[comp]["insights"]["price_pattern"] = pattern

    payload["strategies"] = strategies
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 4 — Growth Opportunity Finder
# ─────────────────────────────────────────────────────────────────

def _growth_opportunity_finder(payload: dict) -> dict:
    """
    Combines fast_movers + new_arrivals to suggest growth opportunities.
    Fast movers (frequent stock-outs) = products in high demand you should stock more of.
    New arrivals at competitors = products you should consider adding to your catalog.
    """
    fast_movers    = payload.get("fast_movers", [])
    catalog_alerts = payload.get("catalog_alerts", [])
    new_arrivals   = [a["data"] for a in catalog_alerts
                      if a.get("type") == "new_arrival"]

    opportunities = []

    # Fast movers across competitors = high demand
    seen_products = set()
    for fm in fast_movers[:5]:
        key = fm["product"][:40].lower()
        if key not in seen_products:
            seen_products.add(key)
            opportunities.append({
                "type":       "stock_up",
                "product":    fm["product"],
                "competitor": fm["competitor"],
                "reason":     f"Out of stock {fm['times_out']}x — high demand signal",
                "priority":   "high" if fm["stockout_rate"] > 0.4 else "medium",
            })

    # New arrivals competitors have = products to consider stocking
    for na in new_arrivals[:5]:
        key = na["product"][:40].lower()
        if key not in seen_products:
            seen_products.add(key)
            opportunities.append({
                "type":       "add_to_catalog",
                "product":    na["product"],
                "competitor": na["competitor"],
                "price":      na.get("price", 0),
                "reason":     f"Competitor {na['competitor']} is selling this — you don't carry it",
                "priority":   "medium",
            })

    payload["opportunities"] = opportunities
    return payload


# ─── Compose RunnableLambda pipeline ─────────────────────────────

_intel_pipeline = (
    RunnableLambda(_strategy_classifier)
    | RunnableLambda(_flash_sale_detector)
    | RunnableLambda(_price_pattern_analyser)
    | RunnableLambda(_growth_opportunity_finder)
)


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_intel_node(state: AgentState) -> dict:
    """
    LangGraph node: Intel Agent.

    Runs 4-module competitive intelligence pipeline.
    Outputs intel_insights dict merged into state.
    Persists strategy labels to market_intelligence DB.
    """
    scraped      = state["scraped_records"]
    analytics    = state["analytics"]
    retailer_id  = state["retailer_id"]
    cycle_id     = state["cycle_id"]
    catalog_alerts = state.get("catalog_alerts", [])

    print(f"\n[Intel] Running competitive intelligence analysis...")

    if not scraped:
        return {"intel_insights": {}, "current_node": "intel"}

    result = _intel_pipeline.invoke({
        "scraped_records": scraped,
        "analytics":       analytics,
        "retailer_id":     retailer_id,
        "fast_movers":     state.get("intel_insights", {}).get("fast_movers", []),
        "catalog_alerts":  catalog_alerts,
    })

    strategies    = result.get("strategies", {})
    flash_events  = result.get("flash_events", [])
    opportunities = result.get("opportunities", [])

    # Persist strategy labels to DB
    intel_list = list(strategies.values())
    db.save_market_intelligence(retailer_id, cycle_id, intel_list)

    # Print summary
    print(f"[Intel] Competitor strategies:")
    for comp, data in strategies.items():
        pattern = data["insights"].get("price_pattern", "")
        label   = data["strategy_label"]
        gap     = data["avg_price_gap_pct"]
        sign    = "+" if gap >= 0 else ""
        print(f"  {comp:25} → {label:22} (avg {sign}{gap:.1f}% vs market)"
              + (f" | {pattern}" if pattern else ""))

    if flash_events:
        print(f"[Intel] ⚡ {len(flash_events)} flash sale(s) detected:")
        for fe in flash_events[:3]:
            print(f"  {fe['competitor']}: {fe['product'][:40]} "
                  f"dropped {fe['drop_pct']}% (₹{fe['prev_price']:,.0f} → ₹{fe['cur_price']:,.0f})")

    if opportunities:
        print(f"[Intel] 💡 {len(opportunities)} growth opportunity(ies):")
        for op in opportunities[:3]:
            print(f"  [{op['priority'].upper()}] {op['type']}: {op['product'][:45]}")

    # Merge with existing intel_insights from catalog_spy
    existing_insights = state.get("intel_insights", {})
    merged_insights   = {
        **existing_insights,
        "competitor_strategies": {
            comp: data["strategy_label"]
            for comp, data in strategies.items()
        },
        "strategy_details":    strategies,
        "flash_sales":         flash_events,
        "opportunities":       opportunities,
    }

    return {
        "intel_insights": merged_insights,
        "current_node":   "intel",
    }