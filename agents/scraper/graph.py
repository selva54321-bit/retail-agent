"""
agents/scraper/graph.py
========================
LangGraph sub-graph for scraping one (competitor × product) target.

Nodes:   navigator → fetcher → extractor
State:   ScraperSubState (defined in state.py)

The sub-graph is compiled once and invoked once per scrape target.
Each invocation is independent — failures in one target never affect others.

Routing:
  - After navigator: if nav_success=False → skip to END (no data to fetch)
  - After fetcher:   always proceed to extractor (extractor handles empty DOM)
  - After extractor: always END
"""

from typing import Literal

from langgraph.graph import StateGraph, START, END

from agents.scraper.state     import ScraperSubState
from agents.scraper.navigator import run_navigator
from agents.scraper.fetcher   import run_fetcher
from agents.scraper.extractor import run_extractor


# ─────────────────────────────────────────────────────────────────
#  ROUTING FUNCTIONS
# ─────────────────────────────────────────────────────────────────

def route_after_navigator(state: ScraperSubState) -> Literal["fetcher", "__end__"]:
    """
    If the navigator failed to load the page, skip directly to END.
    There is no HTML to fetch or extract from.
    """
    if state.get("nav_success"):
        return "fetcher"
    return "__end__"


# ─────────────────────────────────────────────────────────────────
#  BUILD THE SUB-GRAPH
# ─────────────────────────────────────────────────────────────────

def build_scraper_subgraph():
    """
    Construct and compile the scraper sub-graph.
    Called once at module load — the compiled graph is reused for every target.

    Graph:
      START → navigator → [conditional] → fetcher → extractor → END
                                 ↓ (failed)
                                END
    """
    graph = StateGraph(ScraperSubState)

    # Register nodes
    graph.add_node("navigator", run_navigator)
    graph.add_node("fetcher",   run_fetcher)
    graph.add_node("extractor", run_extractor)

    # Edges
    graph.add_edge(START, "navigator")

    graph.add_conditional_edges(
        "navigator",
        route_after_navigator,
        {
            "fetcher":   "fetcher",
            "__end__":   END,
        }
    )

    graph.add_edge("fetcher",   "extractor")
    graph.add_edge("extractor", END)

    return graph.compile()


# Compile once at import time — reused for every invocation
SCRAPER_SUBGRAPH = build_scraper_subgraph()


# ─────────────────────────────────────────────────────────────────
#  PUBLIC INTERFACE
# ─────────────────────────────────────────────────────────────────

def run_scraper_subgraph(target: dict) -> list[dict]:
    """
    Run the scraper sub-graph for one (competitor × product) target.

    Args:
        target: row from competitor_registry DB with these keys:
                competitor_name, url, scrape_method,
                catalog_sku, catalog_product_name

    Returns:
        list of price record dicts ready to save to price_history DB.
        Empty list on failure.
    """
    initial_state: ScraperSubState = {
        # Input
        "url":                  target["url"],
        "competitor_name":      target["competitor_name"],
        "catalog_sku":          target.get("catalog_sku", ""),
        "catalog_product_name": target.get("catalog_product_name", ""),
        "scrape_method":        target.get("scrape_method", "dynamic"),

        # Filled by sub-agents
        "page_html":   "",
        "nav_success": False,
        "dom_section": "",
        "products":    [],
        "errors":      [],
    }

    final_state = SCRAPER_SUBGRAPH.invoke(initial_state)

    # Convert extracted products → price record dicts
    from datetime import datetime
    ts       = datetime.now().isoformat()
    products = final_state.get("products", [])
    records  = []

    for p in products:
        records.append({
            "competitor_name":      target["competitor_name"],
            "competitor_url":       target["url"],
            "product_name_raw":     p["name"],
            "price":                p["price"],
            "original_price":       p.get("original_price"),
            "in_stock":             True,
            "scraped_at":           ts,
            "confidence":           "high" if p.get("confidence", 0) >= 0.65 else "medium",
            "confidence_score":     p.get("confidence", 0),
            "scrape_method_used":   p.get("method", "unknown"),
            "catalog_sku":          target.get("catalog_sku", ""),
            "catalog_product_name": target.get("catalog_product_name", ""),
        })

    return records