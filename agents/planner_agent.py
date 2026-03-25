"""
RetailAgent — Planner Agent (Product-Level Search URL Generation)
==================================================================
Key design: generates one scrape target per (competitor × catalog product).
Each URL uses the exact product name as the search query, so the scraper
fetches the right product page and the result is pre-tagged with the SKU.

Example for "LG 32-inch Smart HD TV":
  Amazon India → https://www.amazon.in/s?k=LG+32-inch+Smart+HD+TV
  Flipkart     → https://www.flipkart.com/search?q=LG+32-inch+Smart+HD+TV
  Croma        → https://www.croma.com/searchB?q=LG+32-inch+Smart+HD+TV
  Vasanth Co   → https://www.vasanthandco.com/search?q=LG+32-inch+Smart+HD+TV

LangChain patterns:
  - ChatPromptTemplate | get_llm | JsonOutputParser  → LCEL planning chain
  - Rule-based fallback                              → when LLM unavailable
"""

import re

from langchain_core.prompts        import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from core.state import AgentState, RetailerProfile, ExecutionPlan, ScrapeTarget
from core.llm   import get_llm
from core       import database as db


# ─────────────────────────────────────────────────────────────────
#  COMPETITOR → URL TEMPLATE MAPPING
#  Each entry: competitor domain fragment → (url_template, method)
#  {q} will be replaced with the URL-encoded product name.
# ─────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
#  COMPETITOR BASE URL MAP
#  Navigator will land here, then use the search box interactively.
#  No query string — navigator types the product name itself.
# ─────────────────────────────────────────────────────────────────

COMPETITOR_URL_MAP = {
    # ── National e-commerce ──────────────────────────────────────
    "amazon":            ("https://www.amazon.in",             "dynamic"),
    "flipkart":          ("https://www.flipkart.com",          "dynamic"),
    "croma":             ("https://www.croma.com",             "dynamic"),
    "reliance digital":  ("https://www.reliancedigital.in",    "dynamic"),
    "vijay sales":       ("https://www.vijaysales.com",        "dynamic"),
    "vijaysales":        ("https://www.vijaysales.com",        "dynamic"),
    "tata cliq":         ("https://www.tatacliq.com",          "dynamic"),
    "meesho":            ("https://www.meesho.com",            "dynamic"),
    "snapdeal":          ("https://www.snapdeal.com",          "dynamic"),

    # ── South India / Tamil Nadu chains ─────────────────────────
    "vasanth":           ("https://www.vasanthandco.in",       "dynamic"),
    "poorvika":          ("https://www.poorvika.com",          "dynamic"),
    "sangeetha":         ("https://www.sangeetha.com",         "dynamic"),
    "girias":            ("https://www.girias.com",            "dynamic"),
    "darling":           ("https://www.darlingretail.com",     "dynamic"),
    "sathya":            ("https://www.sathya.in",             "dynamic"),
    "pai international": ("https://www.pai.in",                "dynamic"),
    "pai":               ("https://www.pai.in",                "dynamic"),
    "lot mobiles":       ("https://www.lotmobiles.com",        "dynamic"),
    "lot":               ("https://www.lotmobiles.com",        "dynamic"),
    "bharath":           ("https://bharathelectronics.com",    "dynamic"),
    "viveks":            ("https://www.viveks.com",            "dynamic"),
    "adishwar":          ("https://www.adishwar.com",          "dynamic"),
}


def _make_search_url(competitor_name: str, product_name: str) -> tuple[str, str]:
    """
    Return the base site URL for a competitor.
    The actual search query is typed by the Navigator interactively.
    Returns (base_url, scrape_method).
    """
    comp_lower = competitor_name.lower().strip()

    for key, (base_url, method) in COMPETITOR_URL_MAP.items():
        if key in comp_lower:
            return base_url, method

    # Fallback: construct base domain from name
    domain = re.sub(r"[^a-z0-9]", "", comp_lower)
    return f"https://www.{domain}.com", "dynamic"


def _product_slug(product_name: str) -> str:
    """Short slug of a product name for logging."""
    return product_name[:35] + ("…" if len(product_name) > 35 else "")


# ─────────────────────────────────────────────────────────────────
#  VELOCITY + INTERVAL MAPPING
# ─────────────────────────────────────────────────────────────────

def _scan_interval(category: str, frequency_pref: str) -> tuple[int, str]:
    """Return (scan_interval_hours, priority) based on category."""
    HIGH = {"electronics", "tv", "television", "mobile", "phone", "grocery", "fuel"}
    LOW  = {"furniture", "jewellery", "specialty", "antique"}
    cat  = category.lower()

    if any(h in cat for h in HIGH):
        hours, pri = 6, "high"
    elif any(l in cat for l in LOW):
        hours, pri = 48, "low"
    else:
        hours, pri = 24, "medium"

    # Override from retailer's preference
    if frequency_pref == "hourly":
        hours = 2
    elif frequency_pref == "weekly":
        hours = 72

    return hours, pri


# ─────────────────────────────────────────────────────────────────
#  LCEL PLANNER CHAIN  (for LLM-generated competitor suggestions)
# ─────────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are the Planner Agent for RetailAgent, a competitive price monitoring system.

Given a retailer profile, suggest additional REAL online competitors to monitor.
Only suggest actual business names with working websites — no descriptions, 
no conditionals, no generic phrases like "local stores".

Return this exact JSON structure:
{{
  "additional_competitors": ["ExactStoreName1", "ExactStoreName2"],
  "strategy_framework": "competitive_parity|penetration|premium|value|cost_plus",
  "reasoning": "2-3 sentence explanation"
}}

Rules for additional_competitors:
- Only real Indian retailer names with actual websites (e.g. "Pai International", "Girias")
- Maximum 3 suggestions
- Each name must be under 30 characters
- Do NOT include conditional phrases, brackets, or descriptions
- Do NOT suggest names already in the known list

For TV/electronics in Tamil Nadu, valid examples: Pai International, Girias, Sangeetha
"""


def _build_planner_chain():
    prompt = ChatPromptTemplate.from_messages([
        ("system", PLANNER_SYSTEM),
        ("human", "{profile_summary}"),
    ])
    return prompt | get_llm(temperature=0.05) | JsonOutputParser()


# ─────────────────────────────────────────────────────────────────
#  CORE: BUILD TARGETS — one URL per (competitor × product)
# ─────────────────────────────────────────────────────────────────

def _build_targets(competitors: list[str], catalog: list[dict],
                   category: str, frequency_pref: str) -> list[dict]:
    """
    Generate one scrape target per (competitor × catalog product).
    Each target's URL uses the exact product name as the search query.
    """
    interval, priority = _scan_interval(category, frequency_pref)
    targets = []

    for competitor in competitors:
        for product in catalog:
            pname = product.get("name", "")
            sku   = product.get("sku", "")
            if not pname:
                continue

            url, method = _make_search_url(competitor, pname)

            targets.append({
                "competitor_name":     competitor,
                "url":                 url,
                "priority":            priority,
                "scan_interval_hours": interval,
                "scrape_method":       method,
                "product_category":    category,
                "selector_config":     {},
                "source":              "planner",
                "catalog_sku":         sku,
                "catalog_product_name": pname,
            })

    return targets


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_planner_node(state: AgentState) -> dict:
    """
    LangGraph node: Planner Agent.

    Strategy:
      1. Use the known_competitors from intake profile as the base list
      2. Ask LLM to suggest 2-3 additional relevant competitors
      3. For EVERY competitor × EVERY catalog product → generate a search URL
         using the exact product name as the query parameter
      4. Register all targets in the competitor_registry DB
    """
    profile     = state["retailer_profile"]
    retailer_id = state["retailer_id"]

    print("\n[Planner] Building product-level monitoring plan...")
    print(f"  Catalog: {len(profile.catalog)} products × "
          f"{len(profile.known_competitors)} known competitors")

    # ── Use only known competitors from intake ───────────────
    all_competitors = list(profile.known_competitors)
    strategy  = profile.pricing_strategy
    reasoning = f"Rule-based plan for {profile.category} in {profile.location} using known competitors."

    # ── Generate one URL per (competitor × product) ───────────
    targets = _build_targets(
        competitors    = all_competitors,
        catalog        = profile.catalog,
        category       = profile.category,
        frequency_pref = profile.scan_frequency,
    )

    # ── Persist to DB ─────────────────────────────────────────
    for t in targets:
        db.upsert_competitor(retailer_id, t)

    # ── Build ExecutionPlan ───────────────────────────────────
    scrape_target_objs = [
        ScrapeTarget(**{k: v for k, v in t.items() if k in ScrapeTarget.model_fields})
        for t in targets
    ]
    plan = ExecutionPlan(
        scrape_targets      = scrape_target_objs,
        priority_categories = profile.subcategories or [profile.category],
        strategy_framework  = strategy,
        reasoning           = reasoning,
    )

    total_products = len(profile.catalog)
    total_comps    = len(all_competitors)
    print(f"  [Planner] {len(targets)} search targets registered "
          f"({total_comps} competitors × {total_products} products)")
    for c in all_competitors:
        print(f"    • {c}")

    return {
        "execution_plan": plan,
        "current_node":   "planner",
    }