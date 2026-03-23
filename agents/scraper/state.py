"""
agents/scraper/state.py
========================
TypedDict that flows through the three scraper sub-agents.
Each sub-agent reads from it and writes only its own output keys back.
LangGraph merges the partial updates automatically.
"""
from __future__ import annotations
from typing import TypedDict


class ScraperSubState(TypedDict):
    """
    Shared state for one (competitor × product) scrape cycle.

    Flow:
      Navigator  → fills: page_html, nav_success
      Fetcher    → fills: dom_section  (focused slice of page_html)
      Extractor  → fills: products     (list of {name, price, original_price})
    """

    # ── Input — set once before sub-graph starts ─────────────────
    url:                  str
    competitor_name:      str
    catalog_sku:          str
    catalog_product_name: str
    scrape_method:        str       # "static" | "dynamic" | "anti_bot"

    # ── Navigator output ─────────────────────────────────────────
    page_html:   str                # full rendered HTML after JS execution
    nav_success: bool

    # ── Fetcher output ───────────────────────────────────────────
    dom_section: str                # focused HTML slice: product-list area only

    # ── Extractor output ─────────────────────────────────────────
    products:    list               # [{name, price, original_price}]

    # ── Error trace ──────────────────────────────────────────────
    errors:      list               # list of strings, one per failed step