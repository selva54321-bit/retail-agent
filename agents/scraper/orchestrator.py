"""
agents/scraper/orchestrator.py
================================
The run_scraper_node that the main RetailAgent graph calls.

This node:
  1. Reads all (competitor × product) targets from the DB
  2. Filters to only the active 2 competitors for now (Amazon + Flipkart)
  3. Runs the scraper sub-graph for each target in parallel (ThreadPoolExecutor)
  4. Saves all collected price records to the DB
  5. Returns the scraped_records list for the main state

Adding more competitors later:
  Remove or expand ACTIVE_COMPETITORS below.
  The sub-graph handles any competitor the planner registered.
"""

import concurrent.futures
import socket
from datetime import datetime

from agents.scraper.graph import run_scraper_subgraph
from core.state import AgentState
from core       import database as db


# ─────────────────────────────────────────────────────────────────
#  ACTIVE COMPETITORS
#  During initial testing, only scrape these two sites.
#  Expand this list once Amazon + Flipkart are confirmed working.
# ─────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
#  ACTIVE COMPETITORS
#  These are the ONLY sites the scraper will actually visit.
#  Names must match competitor_name values in the DB (case-insensitive).
#  Planner registers the user's known list → Scout adds local chains →
#  Orchestrator filters both down to this set before scraping.
# ─────────────────────────────────────────────────────────────────

ACTIVE_COMPETITORS = {
    "amazon india",
    "flipkart",
    "poorvika",
    "poorvika mobiles",
    "girias",
    "darling",
    "darling electronics",
    "reliance digital",
    "vasanth and co",
    "vasanthandco",
}


def _network_available() -> bool:
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
#  MAIN LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_scraper_node(state: AgentState) -> dict:
    """
    LangGraph node: Scraper (main graph).

    Reads scrape targets directly from the execution_plan built by Planner
    (and optionally extended by Scout this cycle). This ensures only
    explicitly known competitors are scraped — not stale DB entries.
    """
    retailer_id    = state["retailer_id"]
    execution_plan = state.get("execution_plan")

    # ── Build active_targets from this cycle's execution plan ────
    if execution_plan and execution_plan.scrape_targets:
        active_targets = [
            {
                "retailer_id":          retailer_id,
                "competitor_name":      t.competitor_name,
                "url":                  t.url,
                "scrape_method":        t.scrape_method,
                "catalog_sku":          t.catalog_sku,
                "catalog_product_name": t.catalog_product_name,
                "selector_config":      "{}",
            }
            for t in execution_plan.scrape_targets
        ]
    else:
        # Fallback: read only planner-registered targets from DB
        active_targets = [
            t for t in db.get_competitors(retailer_id)
            if t.get("source", "planner") == "planner"
        ]

    if not active_targets:
        print("\n[Scraper] No targets registered.")
        return {"scraped_records": [], "scraping_complete": True,
                "current_node": "scraper"}

    n_prod  = len({t.get("catalog_sku","") for t in active_targets if t.get("catalog_sku")})
    n_comp  = len({t.get("competitor_name","") for t in active_targets})

    print(f"\n[Scraper] Active: {len(active_targets)} targets "
          f"({n_prod} products × {n_comp} competitors)")

    if not active_targets:
        print("  No active competitor targets found in registry.")
        return {"scraped_records": [], "scraping_complete": True,
                "current_node": "scraper"}

    if not _network_available():
        print("  ✗ No network — cannot scrape")
        return {"scraped_records": [], "scraping_complete": True,
                "current_node": "scraper"}

    print(f"\n  Sub-graph per target: [navigator] → [fetcher] → [extractor]\n")

    # ── Run sub-graph per target in parallel ──────────────────────
    all_records: list[dict] = []
    failed:      list[str]  = []

    # max_workers=1 because headed (visible) browsers must run sequentially —
    # multiple browser windows simultaneously cause timing/focus conflicts.
    # Switch to max_workers=3 if you change navigator to headless=True.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        futures = {
            executor.submit(run_scraper_subgraph, t): t
            for t in active_targets
        }
        for future in concurrent.futures.as_completed(futures):
            t = futures[future]
            label = f"{t['competitor_name']} | {t.get('catalog_product_name','')[:30]}"
            try:
                records = future.result()
                all_records.extend(records)
                if records:
                    print(f"  ✓ {label} — {len(records)} record(s)")
                else:
                    print(f"  ✗ {label} — no data")
                    failed.append(label)
            except Exception as e:
                print(f"  ✗ {label} — unhandled: {e}")
                failed.append(label)

    # ── Persist ────────────────────────────────────────────────────
    db.save_price_records(retailer_id, all_records)

    ok       = len(active_targets) - len(failed)
    avg_conf = (sum(r.get("confidence_score", 0) for r in all_records) / len(all_records)
                if all_records else 0.0)

    print(f"\n[Scraper] Done — {len(all_records)} price records "
          f"from {ok}/{len(active_targets)} targets  |  avg conf: {avg_conf:.2f}")
    if failed:
        print(f"  No data for {len(failed)} target(s)")

    return {
        "scraped_records":   all_records,
        "scraping_complete": True,
        "current_node":      "scraper",
        "errors": ([f"Scraper: {len(failed)} targets no data"] if failed else []),
    }