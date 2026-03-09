
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import json


# ─────────────────────────────────────────────
#  RETAILER PROFILE  (set once at onboarding)
# ─────────────────────────────────────────────

@dataclass
class RetailerProfile:
    store_name: str = ""
    category: str = ""                  # electronics, grocery, apparel …
    subcategories: list = field(default_factory=list)
    location: str = ""                  # city, region
    brand_positioning: str = "mid-market"  # budget | mid-market | premium
    known_competitors: list = field(default_factory=list)
    pricing_strategy: str = "competitive_parity"  # cost_plus | value | competitive_parity | penetration | premium
    cost_margin_floor: float = 0.10     # minimum margin (10%)
    max_price_shift_pct: float = 0.15   # max single-cycle change (15%)
    auto_apply_prices: bool = False     # suggest-only vs auto-apply
    alert_threshold_pct: float = 0.05  # alert when competitor moves >5%
    scan_frequency: str = "daily"       # hourly | daily | weekly
    catalog: list = field(default_factory=list)  # [{name, sku, current_price, cost}]
    onboarding_complete: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self):
        return {
            "store_name": self.store_name,
            "category": self.category,
            "subcategories": self.subcategories,
            "location": self.location,
            "brand_positioning": self.brand_positioning,
            "known_competitors": self.known_competitors,
            "pricing_strategy": self.pricing_strategy,
            "cost_margin_floor": self.cost_margin_floor,
            "max_price_shift_pct": self.max_price_shift_pct,
            "auto_apply_prices": self.auto_apply_prices,
            "alert_threshold_pct": self.alert_threshold_pct,
            "scan_frequency": self.scan_frequency,
            "catalog": self.catalog,
            "onboarding_complete": self.onboarding_complete,
        }

    def summary(self):
        return (
            f"Store: {self.store_name} | Category: {self.category} | "
            f"Location: {self.location} | Positioning: {self.brand_positioning} | "
            f"Strategy: {self.pricing_strategy} | Competitors: {', '.join(self.known_competitors)}"
        )


# ─────────────────────────────────────────────
#  EXECUTION PLAN  (produced by Planner Agent)
# ─────────────────────────────────────────────

@dataclass
class ScrapeTarget:
    competitor_name: str
    url: str
    priority: str = "medium"           # high | medium | low
    scan_interval_hours: int = 24
    scrape_method: str = "static"       # static | dynamic | anti_bot
    product_category: str = ""
    last_scraped: Optional[str] = None
    selector_config: dict = field(default_factory=dict)
    consecutive_failures: int = 0

@dataclass
class ExecutionPlan:
    scrape_targets: list = field(default_factory=list)   # list[ScrapeTarget]
    priority_categories: list = field(default_factory=list)
    strategy_framework: str = "competitive_parity"
    replan_interval_days: int = 7
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ─────────────────────────────────────────────
#  SCRAPED PRICE RECORD
# ─────────────────────────────────────────────

@dataclass
class PriceRecord:
    competitor_name: str
    competitor_url: str
    product_name_raw: str               # exact string from competitor page
    price: float
    original_price: Optional[float]     # struck-through price if discount
    in_stock: bool = True
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())
    confidence: str = "high"            # high | medium | low
    scrape_method_used: str = "static"


# ─────────────────────────────────────────────
#  PRODUCT MATCH  (produced by Normalizer)
# ─────────────────────────────────────────────

@dataclass
class ProductMatch:
    retailer_sku: str
    retailer_product_name: str
    competitor_name: str
    competitor_product_name: str
    competitor_price: float
    similarity_score: float
    match_method: str = "embedding"     # embedding | llm | exact | cached
    matched_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ─────────────────────────────────────────────
#  ANALYTICS RESULT  (produced by Analyst)
# ─────────────────────────────────────────────

@dataclass
class ProductAnalytics:
    retailer_sku: str
    product_name: str
    retailer_price: float
    competitor_prices: dict = field(default_factory=dict)   # {competitor: price}
    min_competitor_price: float = 0.0
    avg_competitor_price: float = 0.0
    max_competitor_price: float = 0.0
    price_rank: int = 0                 # 1 = cheapest
    total_competitors: int = 0
    price_gap_to_min: float = 0.0       # absolute diff to cheapest
    price_gap_pct_to_min: float = 0.0   # % diff to cheapest
    trend: str = "stable"               # rising | falling | stable
    is_anomaly: bool = False
    anomaly_reason: str = ""


# ─────────────────────────────────────────────
#  PRICING RECOMMENDATION  (produced by Pricing Agent)
# ─────────────────────────────────────────────

@dataclass
class PricingRecommendation:
    retailer_sku: str
    product_name: str
    current_price: float
    recommended_price: float
    price_change: float
    price_change_pct: float
    action: str = "hold"                # hold | reduce | raise | aggressive_reduce
    confidence: float = 0.0
    reasoning: str = ""
    guardrail_applied: bool = False
    guardrail_note: str = ""
    approved: Optional[bool] = None     # None=pending, True=approved, False=rejected
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ─────────────────────────────────────────────
#  SHARED GRAPH STATE  (flows through all nodes)
# ─────────────────────────────────────────────

@dataclass
class AgentState:
    # Core
    retailer_profile: RetailerProfile = field(default_factory=RetailerProfile)
    execution_plan: Optional[ExecutionPlan] = None
    cycle_id: str = ""
    cycle_started_at: str = ""
    current_node: str = "start"
    errors: list = field(default_factory=list)

    # Data produced each cycle
    scraped_records: list = field(default_factory=list)       # list[PriceRecord]
    product_matches: list = field(default_factory=list)       # list[ProductMatch]
    analytics: list = field(default_factory=list)             # list[ProductAnalytics]
    recommendations: list = field(default_factory=list)       # list[PricingRecommendation]
    alerts: list = field(default_factory=list)
    morning_briefing: str = ""

    # Routing flags
    needs_onboarding: bool = True
    scraping_complete: bool = False
    analysis_complete: bool = False