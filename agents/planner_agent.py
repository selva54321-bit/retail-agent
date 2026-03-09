"""
RetailAgent — Planner Agent
=============================
Powered by Llama 3.1 (via Ollama).
Reads the retailer profile and produces a structured JSON execution plan:
  - Which competitors to monitor and at what frequency
  - Which product categories are high/medium/low velocity
  - Which pricing strategy framework to apply
  - Scrape method per competitor

Falls back to a rule-based plan if LLM is unavailable.
"""

import json
from core.state import AgentState, ExecutionPlan, ScrapeTarget
from core.llm import chat_json, is_available
from core import database as db


SYSTEM_PROMPT = """You are the Planner Agent for RetailAgent, an automated competitor 
price monitoring system.

Given a retailer's profile, you must create a strategic monitoring execution plan.

Return a JSON object with this exact structure:
{
  "scrape_targets": [
    {
      "competitor_name": "string",
      "url": "string (realistic URL for this competitor in the retailer's category and location)",
      "priority": "high|medium|low",
      "scan_interval_hours": number,
      "scrape_method": "static|dynamic|anti_bot",
      "product_category": "string"
    }
  ],
  "priority_categories": ["list of product categories to prioritize"],
  "strategy_framework": "competitive_parity|penetration|premium|value|cost_plus",
  "reasoning": "brief explanation of your strategy choices"
}

Rules for scan_interval_hours:
- high priority (electronics, phones, groceries): 3-6 hours
- medium priority (appliances, apparel): 12-24 hours  
- low priority (furniture, specialty): 48-72 hours

Rules for scrape_method:
- Large e-commerce platforms (Amazon, Flipkart): dynamic (JS-rendered)
- Retail chains with web presence: dynamic
- Small local sites: static
- Anti-bot protected sites: anti_bot
"""


def run_planner(state: AgentState, retailer_id: int) -> AgentState:
    """
    Decomposes the retailer profile into an execution plan.
    Writes scrape targets to the competitor registry.
    """
    profile = state.retailer_profile
    print("\n[Planner] Analyzing retailer profile and building execution plan...")

    if is_available():
        plan_data = _llm_plan(profile)
    else:
        plan_data = _rule_based_plan(profile)

    # Build ExecutionPlan object
    targets = []
    for t in plan_data.get("scrape_targets", []):
        targets.append(ScrapeTarget(
            competitor_name=t.get("competitor_name", ""),
            url=t.get("url", ""),
            priority=t.get("priority", "medium"),
            scan_interval_hours=int(t.get("scan_interval_hours", 24)),
            scrape_method=t.get("scrape_method", "static"),
            product_category=t.get("product_category", profile.category),
        ))

    plan = ExecutionPlan(
        scrape_targets=targets,
        priority_categories=plan_data.get("priority_categories", profile.subcategories),
        strategy_framework=plan_data.get("strategy_framework", profile.pricing_strategy),
    )

    # Persist targets to competitor registry
    for t in targets:
        db.upsert_competitor(retailer_id, {
            "competitor_name": t.competitor_name,
            "url": t.url,
            "priority": t.priority,
            "scan_interval_hours": t.scan_interval_hours,
            "scrape_method": t.scrape_method,
            "product_category": t.product_category,
        })

    state.execution_plan = plan

    print(f"[Planner] Plan created: {len(targets)} scrape targets")
    for t in targets:
        print(f"  • {t.competitor_name} ({t.priority}) — every {t.scan_interval_hours}h via {t.scrape_method}")

    if "reasoning" in plan_data:
        print(f"[Planner] Reasoning: {plan_data['reasoning'][:200]}")

    return state


def _llm_plan(profile) -> dict:
    """Use LLM to create an intelligent execution plan."""
    user_msg = f"""
Retailer Profile:
- Store: {profile.store_name}
- Category: {profile.category}
- Subcategories: {', '.join(profile.subcategories)}
- Location: {profile.location}
- Positioning: {profile.brand_positioning}
- Known Competitors: {', '.join(profile.known_competitors)}
- Pricing Strategy: {profile.pricing_strategy}
- Catalog Size: {len(profile.catalog)} products
- Scan Frequency Preference: {profile.scan_frequency}

Create a monitoring execution plan for this retailer. 
For each known competitor, generate a realistic URL for their product listings 
in the retailer's category and location.
Also suggest 2-3 additional relevant online competitors if applicable.
"""
    try:
        result = chat_json("planner", SYSTEM_PROMPT, user_msg)
        return result
    except Exception as e:
        print(f"[Planner] LLM failed, using rule-based fallback: {e}")
        return _rule_based_plan(profile)


def _rule_based_plan(profile) -> dict:
    """
    Rule-based fallback plan when LLM is unavailable.
    Uses category + competitor names to build sensible defaults.
    """
    # Frequency by category
    HIGH_VELOCITY = {"electronics", "grocery", "mobile", "phones", "fuel", "food"}
    LOW_VELOCITY  = {"furniture", "jewellery", "specialty", "antiques"}

    if profile.category.lower() in HIGH_VELOCITY:
        default_interval = 6
        default_priority  = "high"
    elif profile.category.lower() in LOW_VELOCITY:
        default_interval = 48
        default_priority  = "low"
    else:
        default_interval = 24
        default_priority  = "medium"

    # URL templates per well-known competitor
    URL_TEMPLATES = {
        "amazon":           "https://www.amazon.in/s?k={category}",
        "amazon india":     "https://www.amazon.in/s?k={category}",
        "flipkart":         "https://www.flipkart.com/search?q={category}",
        "croma":            "https://www.croma.com/searchB?q={category}",
        "reliance digital": "https://www.reliancedigital.in/search?q={category}",
        "vijay sales":      "https://www.vijaysales.com/search/{category}",
        "meesho":           "https://www.meesho.com/search?q={category}",
        "snapdeal":         "https://www.snapdeal.com/search?keyword={category}",
        "myntra":           "https://www.myntra.com/{category}",
        "nykaa":            "https://www.nykaa.com/search/result/?q={category}",
    }

    SCRAPE_METHOD = {
        "amazon": "dynamic", "flipkart": "dynamic", "croma": "dynamic",
        "reliance digital": "dynamic", "myntra": "dynamic",
    }

    cat_slug = profile.category.lower().replace(" ", "+")
    targets = []

    for comp in profile.known_competitors:
        comp_lower = comp.lower().strip()
        url_template = URL_TEMPLATES.get(comp_lower, f"https://www.{comp_lower.replace(' ','-')}.com/search?q={{category}}")
        url = url_template.format(category=cat_slug)
        method = next((v for k, v in SCRAPE_METHOD.items() if k in comp_lower), "dynamic")

        targets.append({
            "competitor_name": comp,
            "url": url,
            "priority": default_priority,
            "scan_interval_hours": default_interval,
            "scrape_method": method,
            "product_category": profile.category,
        })

    # Supplement with generic e-commerce if not already present
    generic = []
    if not any("amazon" in c.lower() for c in profile.known_competitors):
        generic.append({
            "competitor_name": "Amazon India",
            "url": f"https://www.amazon.in/s?k={cat_slug}",
            "priority": "high",
            "scan_interval_hours": 6,
            "scrape_method": "dynamic",
            "product_category": profile.category,
        })
    if not any("flipkart" in c.lower() for c in profile.known_competitors):
        generic.append({
            "competitor_name": "Flipkart",
            "url": f"https://www.flipkart.com/search?q={cat_slug}",
            "priority": "high",
            "scan_interval_hours": 6,
            "scrape_method": "dynamic",
            "product_category": profile.category,
        })

    return {
        "scrape_targets": targets + generic[:2],
        "priority_categories": profile.subcategories[:3] if profile.subcategories else [profile.category],
        "strategy_framework": profile.pricing_strategy,
        "reasoning": f"Rule-based plan for {profile.category} retailer in {profile.location}. "
                     f"Default {default_interval}h scan intervals for {default_priority} velocity category.",
    }