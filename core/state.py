"""
RetailAgent — State Schema (LangGraph-native)
===============================================
Uses TypedDict for LangGraph state — the exact format LangGraph's
StateGraph expects. Every agent node receives this dict and returns
a partial update that LangGraph merges into shared state.

Key LangGraph pattern:
  - State is a TypedDict annotated with Annotated[list, operator.add]
    for fields that should be appended (not overwritten) across nodes.
  - Every node returns a dict with only the keys it wants to update.
  - LangGraph merges updates automatically.
"""

from __future__ import annotations
from typing import TypedDict, Annotated, Optional
import operator
from datetime import datetime
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────
#  PYDANTIC MODELS — used for LLM structured output parsing
# ─────────────────────────────────────────────────────────────────

class RetailerProfile(BaseModel):
    """Complete retailer profile collected during onboarding."""
    store_name:          str  = ""
    category:            str  = ""
    subcategories:       list[str] = Field(default_factory=list)
    location:            str  = ""
    brand_positioning:   str  = "mid-market"   # budget | mid-market | premium
    known_competitors:   list[str] = Field(default_factory=list)
    pricing_strategy:    str  = "competitive_parity"
    cost_margin_floor:   float = 0.10
    max_price_shift_pct: float = 0.15
    auto_apply_prices:   bool  = False
    alert_threshold_pct: float = 0.05
    scan_frequency:      str  = "daily"
    catalog: list[dict] = Field(default_factory=list)
    onboarding_complete: bool = False


class ScrapeTarget(BaseModel):
    """A single competitor URL to monitor — one per (competitor × catalog product)."""
    competitor_name:      str
    url:                  str
    priority:             str  = "medium"
    scan_interval_hours:  int  = 24
    scrape_method:        str  = "static"   # static | dynamic | anti_bot
    product_category:     str  = ""
    selector_config:      dict = Field(default_factory=dict)
    # ── Product binding — set by planner, used by scraper ──────
    catalog_sku:          str  = ""   # retailer's own SKU for this product
    catalog_product_name: str  = ""   # exact name to search for on competitor


class ExecutionPlan(BaseModel):
    """Output of the Planner Agent — defines the monitoring strategy."""
    scrape_targets:       list[ScrapeTarget] = Field(default_factory=list)
    priority_categories:  list[str]          = Field(default_factory=list)
    strategy_framework:   str                = "competitive_parity"
    reasoning:            str                = ""


class PriceRecord(BaseModel):
    """A single scraped price entry."""
    competitor_name:    str
    competitor_url:     str
    product_name_raw:   str
    price:              float
    original_price:     Optional[float] = None
    in_stock:           bool  = True
    scraped_at:         str   = Field(default_factory=lambda: datetime.now().isoformat())
    confidence:         str   = "high"
    scrape_method_used: str   = "static"


class ProductMatch(BaseModel):
    """A matched competitor product → retailer catalog item."""
    retailer_sku:             str
    retailer_product_name:    str
    competitor_name:          str
    competitor_product_name:  str
    competitor_price:         float
    similarity_score:         float
    match_method:             str = "embedding"


class ProductAnalytics(BaseModel):
    """Per-product analytics computed by the Analyst Agent."""
    retailer_sku:           str
    product_name:           str
    retailer_price:         float
    competitor_prices:      dict[str, float] = Field(default_factory=dict)
    min_competitor_price:   float = 0.0
    avg_competitor_price:   float = 0.0
    max_competitor_price:   float = 0.0
    price_rank:             int   = 0
    total_competitors:      int   = 0
    price_gap_to_min:       float = 0.0
    price_gap_pct_to_min:   float = 0.0
    trend:                  str   = "stable"
    is_anomaly:             bool  = False
    anomaly_reason:         str   = ""


class PricingRecommendation(BaseModel):
    """Price change recommendation from the Pricing Agent."""
    retailer_sku:       str
    product_name:       str
    current_price:      float
    recommended_price:  float
    price_change:       float
    price_change_pct:   float
    action:             str   = "hold"   # hold | reduce | raise | aggressive_reduce
    confidence:         float = 0.5
    reasoning:          str   = ""
    guardrail_applied:  bool  = False
    guardrail_note:     str   = ""
    approved:           Optional[bool] = None


class Alert(BaseModel):
    """A competitive intelligence alert."""
    type:     str   # anomaly | price_gap | trend
    sku:      str
    product:  str
    message:  str
    severity: str   = "medium"   # high | medium | low
    at:       str   = Field(default_factory=lambda: datetime.now().isoformat())


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH STATE — TypedDict with merge annotations
# ─────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """
    The shared state flowing through the LangGraph graph.

    LangGraph merge rules:
      - Annotated[list, operator.add] → new items are APPENDED each cycle
      - Plain fields             → new value OVERWRITES previous
    """

    # ── Setup (written once) ────────────────────────────────────
    retailer_id:          int
    retailer_profile:     RetailerProfile
    execution_plan:       Optional[ExecutionPlan]
    cycle_id:             str
    cycle_started_at:     str

    # ── Routing flags ───────────────────────────────────────────
    needs_onboarding:     bool
    scraping_complete:    bool
    analysis_complete:    bool

    # ── Per-cycle data (appended across parallel branches) ──────
    scraped_records:      Annotated[list[dict], operator.add]
    product_matches:      Annotated[list[dict], operator.add]
    analytics:            Annotated[list[dict], operator.add]
    recommendations:      Annotated[list[dict], operator.add]
    alerts:               Annotated[list[dict], operator.add]
    errors:               Annotated[list[str],  operator.add]

    # ── Final outputs ────────────────────────────────────────────
    morning_briefing:     str
    current_node:         str