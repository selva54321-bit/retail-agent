"""
RetailAgent — Scout Agent (Regional Chain Lookup)
==================================================
Purpose:
  Supplement the planner's known competitors with well-known regional
  e-commerce/retail chains relevant to the retailer's state and category.
  No web search, no LLM calls, no DuckDuckGo — just a clean lookup table.

Flow:
  1. Detect the retailer's state from their location string
  2. Look up known chains for that state + category combination
  3. Filter out any that the planner already registered
  4. Add the remaining ones to the competitor registry with base URLs

Why not web search:
  DuckDuckGo discovery was finding Justdial listings, service centers,
  Instagram pages, and other non-scrapeable URLs. These wasted scraper
  cycles on sites with no product+price data.

Why a lookup table:
  Regional chains like Poorvika, Sathya, Darling, Girias are well-known
  and stable. Their URLs don't change. Hardcoding them is fast, reliable,
  and doesn't consume LLM quota.
"""

import re
from core.state import AgentState, ScrapeTarget, ExecutionPlan
from core       import database as db


# ─────────────────────────────────────────────────────────────────
#  REGIONAL CHAIN DATABASE
#  Structure: state_key → category_key → list of (name, base_url)
#
#  state_key:    lowercase, partial match (e.g. "tamil" matches "Tamil Nadu")
#  category_key: lowercase, partial match (e.g. "tv" matches "televisions")
#  "all":        applies to all categories in that state
# ─────────────────────────────────────────────────────────────────

REGIONAL_CHAINS: dict[str, dict[str, list[tuple[str, str]]]] = {

    # ── Tamil Nadu ───────────────────────────────────────────────
    "tamil": {
        "all": [
            ("Poorvika",          "https://www.poorvika.com"),
            ("Sangeetha",         "https://www.sangeetha.com"),
            ("Girias",            "https://www.girias.com"),
            ("Vasanth and Co",    "https://www.vasanthandco.com"),
            ("Sathya",            "https://www.sathya.in"),
            ("Darling",           "https://www.darling.in"),
            ("Pai International", "https://www.pai.in"),
            ("Lot Mobiles",       "https://www.lotmobiles.com"),
        ],
        "tv": [
            ("Viveks",            "https://www.viveks.com"),
        ],
        "electronics": [
            ("Viveks",            "https://www.viveks.com"),
        ],
    },

    # ── Karnataka ────────────────────────────────────────────────
    "karnataka": {
        "all": [
            ("Pai International", "https://www.pai.in"),
            ("Sangeetha",         "https://www.sangeetha.com"),
            ("Poorvika",          "https://www.poorvika.com"),
            ("Adishwar",          "https://www.adishwar.com"),
        ],
    },

    # ── Andhra Pradesh / Telangana ───────────────────────────────
    "andhra": {
        "all": [
            ("Lot Mobiles",       "https://www.lotmobiles.com"),
            ("Sangeetha",         "https://www.sangeetha.com"),
            ("Viveks",            "https://www.viveks.com"),
        ],
    },
    "telangana": {
        "all": [
            ("Lot Mobiles",       "https://www.lotmobiles.com"),
            ("Sangeetha",         "https://www.sangeetha.com"),
        ],
    },

    # ── Kerala ───────────────────────────────────────────────────
    "kerala": {
        "all": [
            ("Poorvika",          "https://www.poorvika.com"),
            ("Girias",            "https://www.girias.com"),
        ],
    },

    # ── Maharashtra ──────────────────────────────────────────────
    "maharashtra": {
        "all": [
            ("Vijay Sales",       "https://www.vijaysales.com"),
        ],
    },

    # ── Delhi / NCR ──────────────────────────────────────────────
    "delhi": {
        "all": [
            ("Vijay Sales",       "https://www.vijaysales.com"),
        ],
    },

    # ── National fallback ────────────────────────────────────────
    # Added for any state not listed above
    "national": {
        "all": [
            ("Vijay Sales",       "https://www.vijaysales.com"),
            ("Reliance Digital",  "https://www.reliancedigital.in"),
            ("Tata Cliq",         "https://www.tatacliq.com"),
        ],
    },
}


# ─────────────────────────────────────────────────────────────────
#  LOOKUP HELPERS
# ─────────────────────────────────────────────────────────────────

def _detect_state(location: str) -> str:
    """
    Extract state key from a location string like
    'Saibaba Colony, Coimbatore, Tamil Nadu'.
    Returns lowercase partial key that matches REGIONAL_CHAINS.
    """
    loc_lower = location.lower()
    for key in REGIONAL_CHAINS:
        if key == "national":
            continue
        if key in loc_lower:
            return key
    return "national"


def _detect_category(category: str) -> str:
    """Normalize category to a key used in REGIONAL_CHAINS."""
    cat = category.lower()
    for key in ["tv", "television", "mobile", "phone", "electronics",
                "laptop", "appliance", "furniture"]:
        if key in cat:
            # Normalize synonyms
            if key in ("television",):
                return "tv"
            return key
    return "all"


def _get_regional_chains(location: str, category: str) -> list[tuple[str, str]]:
    """
    Return all (name, base_url) pairs relevant to this location + category.
    Merges 'all' chains with category-specific chains, deduplicated by name.
    """
    state_key = _detect_state(location)
    cat_key   = _detect_category(category)

    state_map = REGIONAL_CHAINS.get(state_key, REGIONAL_CHAINS["national"])

    # Always include 'all' chains
    chains: list[tuple[str, str]] = list(state_map.get("all", []))

    # Add category-specific extras
    if cat_key != "all":
        cat_extras = state_map.get(cat_key, [])
        existing_names = {c[0].lower() for c in chains}
        for chain in cat_extras:
            if chain[0].lower() not in existing_names:
                chains.append(chain)

    return chains


def _get_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_scout_node(state: AgentState) -> dict:
    """
    LangGraph node: Scout Agent.

    Looks up well-known regional chains for the retailer's location and
    category, then adds any that the planner hasn't already registered.
    No web search, no LLM calls.

    The scraper's ACTIVE_COMPETITORS set controls which of these are
    actually scraped — Scout just ensures they exist in the registry.
    """
    profile     = state["retailer_profile"]
    retailer_id = state["retailer_id"]
    plan        = state.get("execution_plan")

    print(f"\n[Scout] Looking up regional chains for "
          f"{profile.category} in {profile.location}...")

    # ── Get existing registered URLs to avoid duplicates ─────────
    existing   = db.get_competitors(retailer_id)
    known_urls = {_get_domain(t["url"]) for t in existing}
    known_names = {t["competitor_name"].lower() for t in existing}

    # ── Look up regional chains ───────────────────────────────────
    chains = _get_regional_chains(profile.location, profile.category)

    new_targets = []
    for chain_name, base_url in chains:
        domain = _get_domain(base_url)

        # Skip if already registered (by name or domain)
        if (chain_name.lower() in known_names or domain in known_urls):
            print(f"  ✓ Already registered: {chain_name}")
            continue

        # One target per catalog product — same structure as planner
        for product in profile.catalog:
            pname = product.get("name", "")
            sku   = product.get("sku", "")
            if not pname:
                continue

            target = {
                "competitor_name":      chain_name,
                "url":                  base_url,
                "priority":             "medium",
                "scan_interval_hours":  24,
                "scrape_method":        "dynamic",
                "product_category":     profile.category,
                "selector_config":      {},
                "source":               "scout_regional",
                "notes":                f"Regional chain — {profile.location}",
                "catalog_sku":          sku,
                "catalog_product_name": pname,
            }
            new_targets.append(target)
            # Only print once per chain
            if sku == profile.catalog[0].get("sku", ""):
                print(f"  + Adding: {chain_name} → {base_url}")

        # Track so we don't duplicate across products
        known_names.add(chain_name.lower())
        known_urls.add(domain)

    if not new_targets:
        print("  [Scout] All regional chains already registered.")
        return {"current_node": "scout"}

    # ── Save to competitor registry ───────────────────────────────
    for t in new_targets:
        db.upsert_competitor(retailer_id, t)

    # ── Update execution plan ─────────────────────────────────────
    new_scrape_targets = list(plan.scrape_targets) if plan else []
    for t in new_targets:
        new_scrape_targets.append(ScrapeTarget(
            **{k: v for k, v in t.items() if k in ScrapeTarget.model_fields}
        ))

    updated_plan = ExecutionPlan(
        scrape_targets      = new_scrape_targets,
        priority_categories = plan.priority_categories if plan else profile.subcategories,
        strategy_framework  = plan.strategy_framework  if plan else profile.pricing_strategy,
        reasoning           = (plan.reasoning if plan else "") +
                              f" | Scout added regional chains for {profile.location}.",
    )

    # Count unique chains added
    unique_chains = {t["competitor_name"] for t in new_targets}
    print(f"[Scout] Done — {len(unique_chains)} regional chain(s) added "
          f"({len(new_targets)} targets total):")
    for c in sorted(unique_chains):
        print(f"  + {c}")

    return {
        "execution_plan": updated_plan,
        "current_node":   "scout",
    }