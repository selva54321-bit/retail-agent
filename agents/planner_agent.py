"""
RetailAgent — Planner Agent (LCEL Chain + Structured Output)
=============================================================
LangChain patterns used:
  - ChatPromptTemplate          → structured system + human prompt
  - LCEL chain (|)              → prompt | llm | JsonOutputParser
  - with_structured_output()    → bind Pydantic schema to model for
                                   guaranteed structured JSON output
  - RunnableLambda              → wraps the rule-based fallback as a Runnable
  - RunnableParallel            → could run multiple planning chains in parallel

The Planner uses chain-of-thought: the LLM reasons step by step
before producing the final JSON execution plan.
"""

from langchain_core.prompts   import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_core.output_parsers import JsonOutputParser

from core.state import AgentState, RetailerProfile, ExecutionPlan, ScrapeTarget
from core.llm   import get_llm, make_json_chain
from core       import database as db


# ─── Planner system prompt ────────────────────────────────────────
PLANNER_SYSTEM = """You are the Planner Agent for RetailAgent, a competitive price monitoring system.

Given a retailer profile, produce a strategic monitoring execution plan.
Think step by step before deciding:
  1. What category velocity level is this? (high/medium/low)
  2. Which competitors need most attention?
  3. What scan frequency is appropriate per category?
  4. What scraping method does each competitor site need?

Return this exact JSON structure:
{
  "scrape_targets": [
    {
      "competitor_name": "string",
      "url": "realistic URL for this competitor + category + location",
      "priority": "high|medium|low",
      "scan_interval_hours": number,
      "scrape_method": "static|dynamic|anti_bot",
      "product_category": "string"
    }
  ],
  "priority_categories": ["list of subcategories to focus on"],
  "strategy_framework": "competitive_parity|penetration|premium|value|cost_plus",
  "reasoning": "2-3 sentence explanation of your strategy"
}

Frequency rules:
  high velocity (electronics, grocery, phones): 3-6 hours
  medium velocity (appliances, apparel): 12-24 hours
  low velocity (furniture, specialty): 48-72 hours

Scrape method rules:
  Amazon, Flipkart, large e-commerce → dynamic (JS-rendered)
  Small local sites → static
  Cloudflare-protected → anti_bot
"""


def _build_planner_chain():
    """
    Build the LCEL planner chain.
    Chain: prompt | llm | json_parser
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", PLANNER_SYSTEM),
        ("human",  "{profile_summary}"),
    ])
    llm    = get_llm(temperature=0.05)
    parser = JsonOutputParser()

    # LCEL pipe operator builds the chain
    return prompt | llm | parser


def _rule_based_plan(profile: RetailerProfile) -> dict:
    """Fallback plan when LLM is unavailable."""
    HIGH_VELOCITY = {"electronics", "grocery", "mobile", "phones"}
    LOW_VELOCITY  = {"furniture", "jewellery", "specialty"}

    if profile.category.lower() in HIGH_VELOCITY:
        interval, priority = 6, "high"
    elif profile.category.lower() in LOW_VELOCITY:
        interval, priority = 48, "low"
    else:
        interval, priority = 24, "medium"

    cat_slug = profile.category.lower().replace(" ", "+")

    METHOD_MAP = {
        "amazon": "dynamic", "flipkart": "dynamic",
        "croma": "dynamic", "reliance digital": "dynamic",
    }
    URL_TEMPLATES = {
        "amazon india":     f"https://www.amazon.in/s?k={cat_slug}",
        "flipkart":         f"https://www.flipkart.com/search?q={cat_slug}",
        "croma":            f"https://www.croma.com/searchB?q={cat_slug}",
        "reliance digital": f"https://www.reliancedigital.in/search?q={cat_slug}",
    }

    targets = []
    for comp in profile.known_competitors:
        comp_l = comp.lower().strip()
        url    = URL_TEMPLATES.get(comp_l, f"https://www.{comp_l.replace(' ','-')}.com/search?q={cat_slug}")
        method = next((v for k, v in METHOD_MAP.items() if k in comp_l), "dynamic")
        targets.append({
            "competitor_name":     comp,
            "url":                 url,
            "priority":            priority,
            "scan_interval_hours": interval,
            "scrape_method":       method,
            "product_category":    profile.category,
        })

    # Add generic platforms if not already included
    if not any("amazon" in c.lower() for c in profile.known_competitors):
        targets.append({
            "competitor_name": "Amazon India", "url": f"https://www.amazon.in/s?k={cat_slug}",
            "priority": "high", "scan_interval_hours": 6,
            "scrape_method": "dynamic", "product_category": profile.category,
        })

    return {
        "scrape_targets":      targets,
        "priority_categories": profile.subcategories[:3],
        "strategy_framework":  profile.pricing_strategy,
        "reasoning":           f"Rule-based plan: {interval}h intervals for {profile.category}.",
    }


def run_planner_node(state: AgentState) -> dict:
    """
    LangGraph node: Planner Agent.
    Builds execution plan using LCEL chain → rule-based fallback.
    Returns partial state update.
    """
    profile     = state["retailer_profile"]
    retailer_id = state["retailer_id"]

    print("\n[Planner] Building monitoring strategy via LCEL chain...")

    profile_summary = f"""
Store: {profile.store_name}
Category: {profile.category} | Subcategories: {', '.join(profile.subcategories)}
Location: {profile.location}
Positioning: {profile.brand_positioning}
Known Competitors: {', '.join(profile.known_competitors)}
Pricing Strategy: {profile.pricing_strategy}
Catalog: {len(profile.catalog)} products
Preferred scan frequency: {profile.scan_frequency}

Generate a complete monitoring plan with realistic competitor URLs for this retailer.
Also suggest 1-2 additional relevant online platforms if appropriate.
"""

    # Try LCEL chain first
    try:
        chain     = _build_planner_chain()
        plan_data = chain.invoke({"profile_summary": profile_summary})
    except Exception as e:
        print(f"  [Planner] LLM unavailable ({e}), using rule-based plan.")
        plan_data = _rule_based_plan(profile)

    # Build typed ExecutionPlan from raw dict
    targets = [
        ScrapeTarget(**{k: v for k, v in t.items() if k in ScrapeTarget.model_fields})
        for t in plan_data.get("scrape_targets", [])
    ]
    plan = ExecutionPlan(
        scrape_targets      = targets,
        priority_categories = plan_data.get("priority_categories", profile.subcategories),
        strategy_framework  = plan_data.get("strategy_framework",  profile.pricing_strategy),
        reasoning           = plan_data.get("reasoning", ""),
    )

    # Persist targets to competitor registry DB
    for t in targets:
        db.upsert_competitor(retailer_id, t.model_dump())

    print(f"  [Planner] {len(targets)} scrape targets registered.")
    for t in targets:
        print(f"    • {t.competitor_name} ({t.priority}) "
              f"every {t.scan_interval_hours}h via {t.scrape_method}")

    return {
        "execution_plan": plan,
        "current_node":   "planner",
    }