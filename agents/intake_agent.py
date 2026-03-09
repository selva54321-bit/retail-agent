"""
RetailAgent — Intake Agent
============================
Runs a guided multi-turn conversation to collect the retailer profile.
Uses Ollama (Llama 3.1 8B) to understand free-form answers and extract
structured data. Falls back to direct CLI prompts if LLM is unavailable.
"""

import json
import sys
from core.state import RetailerProfile, AgentState
from core.llm import chat_json, is_available


SYSTEM_PROMPT = """You are RetailAgent's onboarding assistant helping a retailer set up 
automated competitor price monitoring.

Your job is to extract structured information from the retailer's responses.
Always return a JSON object with the fields you can extract from the conversation so far.
Be friendly, concise, and ask one thing at a time.

The fields you need to collect:
- store_name: string
- category: string (electronics, grocery, apparel, pharmacy, hardware, furniture, etc.)
- subcategories: list of strings (specific product types)
- location: string (city or region)
- brand_positioning: "budget" | "mid-market" | "premium"
- known_competitors: list of competitor names or websites
- pricing_strategy: "cost_plus" | "competitive_parity" | "penetration" | "premium" | "value"
- cost_margin_floor: float (minimum margin as decimal, e.g. 0.10 for 10%)
- max_price_shift_pct: float (max single change, e.g. 0.15 for 15%)
- auto_apply_prices: boolean (true=auto-apply, false=suggest only)
- alert_threshold_pct: float (e.g. 0.05 for 5%)
- scan_frequency: "hourly" | "daily" | "weekly"
- catalog: list of objects with {name, sku, current_price, cost}
"""


QUESTIONS = [
    ("store_name",         "What is your store name?"),
    ("category",           "What category of products do you sell? (e.g. electronics, grocery, apparel, pharmacy)"),
    ("subcategories",      "What are your main product sub-categories? (e.g. smartphones, laptops, accessories)"),
    ("location",           "What city or region is your store in?"),
    ("brand_positioning",  "How would you describe your brand positioning? (budget / mid-market / premium)"),
    ("known_competitors",  "Who are your main competitors? (names or websites, comma-separated)"),
    ("pricing_strategy",   "What is your pricing strategy?\n  1. competitive_parity (match competitors)\n  2. penetration (undercut to gain market share)\n  3. premium (price above market)\n  4. value (best value proposition)\n  5. cost_plus (cost + fixed margin)\nEnter number or name:"),
    ("scan_frequency",     "How often should we scan competitor prices? (hourly / daily / weekly)"),
    ("auto_apply_prices",  "Should we auto-apply price changes, or just suggest them? (auto / suggest)"),
    ("alert_threshold_pct","Alert you when a competitor changes price by more than what %? (e.g. 5)"),
    ("cost_margin_floor",  "What is your minimum acceptable profit margin %? (e.g. 10)"),
    ("catalog",            "Add your products (or press Enter to use demo catalog).\nFormat: ProductName|SKU|CurrentPrice|CostPrice  (one per line, blank line to finish)"),
]

STRATEGY_MAP = {
    "1": "competitive_parity", "2": "penetration",
    "3": "premium", "4": "value", "5": "cost_plus",
}

DEMO_CATALOG = [
    {"name": "Samsung 55-inch 4K Smart TV",   "sku": "TV-001", "current_price": 45000, "cost": 32000},
    {"name": "Sony WH-1000XM5 Headphones",    "sku": "HP-001", "current_price": 28000, "cost": 19000},
    {"name": "Apple iPhone 15 128GB",          "sku": "PH-001", "current_price": 79000, "cost": 62000},
    {"name": "LG 8kg Front Load Washing Machine","sku":"WM-001","current_price": 35000, "cost": 24000},
    {"name": "Bosch 500W Mixer Grinder",       "sku": "KA-001", "current_price": 4500,  "cost": 2800},
]


def _parse_bool(answer: str) -> bool:
    return answer.strip().lower() in ("auto", "yes", "y", "true", "1", "auto-apply")

def _parse_float_pct(answer: str) -> float:
    try:
        v = float(answer.strip().replace("%", ""))
        return v / 100.0 if v > 1 else v
    except Exception:
        return 0.10

def _parse_list(answer: str) -> list:
    return [x.strip() for x in answer.replace(";", ",").split(",") if x.strip()]

def _parse_catalog(lines: list) -> list:
    catalog = []
    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            try:
                catalog.append({
                    "name": parts[0],
                    "sku":  parts[1] if len(parts) > 1 else f"SKU-{len(catalog)+1:03d}",
                    "current_price": float(parts[2]) if len(parts) > 2 else 0.0,
                    "cost": float(parts[3]) if len(parts) > 3 else 0.0,
                })
            except ValueError:
                continue
    return catalog


def _llm_extract_field(field: str, answer: str, context: dict) -> any:
    """Use LLM to extract a specific field from a free-form answer."""
    if not is_available():
        return None
    try:
        result = chat_json(
            "intake",
            SYSTEM_PROMPT,
            f"The user was asked about '{field}'. Their answer was: '{answer}'\n"
            f"Current collected context: {json.dumps(context)}\n"
            f"Extract and return ONLY the value for '{field}' as JSON: {{'{field}': <value>}}"
        )
        return result.get(field)
    except Exception:
        return None


def _prompt(question: str) -> str:
    print(f"\n  ❯  {question}")
    return input("     → ").strip()


def run_intake(state: AgentState) -> AgentState:
    """
    Runs the interactive onboarding conversation.
    Populates state.retailer_profile and sets needs_onboarding=False.
    """
    profile = state.retailer_profile
    collected = {}

    print("\n" + "═" * 60)
    print("  RETAILAGENT — Retailer Onboarding")
    print("═" * 60)
    print("  Let's set up your competitive pricing monitor.")
    print("  Answer each question to configure your RetailAgent.\n")

    for field, question in QUESTIONS:

        # ── CATALOG special handling ──
        if field == "catalog":
            print(f"\n  ❯  {question}")
            lines = []
            while True:
                line = input("     → ").strip()
                if not line:
                    break
                lines.append(line)
            if lines:
                catalog = _parse_catalog(lines)
                if catalog:
                    profile.catalog = catalog
                    collected["catalog"] = catalog
                    print(f"     ✓  {len(catalog)} products added.")
                else:
                    print("     ℹ  Could not parse catalog. Using demo catalog.")
                    profile.catalog = DEMO_CATALOG
                    collected["catalog"] = DEMO_CATALOG
            else:
                print("     ℹ  Using demo catalog.")
                profile.catalog = DEMO_CATALOG
                collected["catalog"] = DEMO_CATALOG
            continue

        answer = _prompt(question)
        if not answer:
            continue

        # Try LLM extraction first, fall back to direct parsing
        llm_val = _llm_extract_field(field, answer, collected)

        # ── Field-specific parsing ──
        if field == "store_name":
            profile.store_name = llm_val or answer

        elif field == "category":
            profile.category = (llm_val or answer).lower().strip()

        elif field == "subcategories":
            if isinstance(llm_val, list):
                profile.subcategories = llm_val
            else:
                profile.subcategories = _parse_list(answer)

        elif field == "location":
            profile.location = llm_val or answer

        elif field == "brand_positioning":
            val = (llm_val or answer).lower()
            if "premium" in val:
                profile.brand_positioning = "premium"
            elif "budget" in val:
                profile.brand_positioning = "budget"
            else:
                profile.brand_positioning = "mid-market"

        elif field == "known_competitors":
            if isinstance(llm_val, list):
                profile.known_competitors = llm_val
            else:
                profile.known_competitors = _parse_list(answer)

        elif field == "pricing_strategy":
            mapped = STRATEGY_MAP.get(answer.strip())
            if mapped:
                profile.pricing_strategy = mapped
            elif llm_val:
                profile.pricing_strategy = str(llm_val)
            else:
                ans_lower = answer.lower()
                for strat in ["competitive_parity", "penetration", "premium", "value", "cost_plus"]:
                    if strat.replace("_", "") in ans_lower.replace(" ", ""):
                        profile.pricing_strategy = strat
                        break

        elif field == "scan_frequency":
            ans_lower = (llm_val or answer).lower()
            if "hour" in ans_lower:
                profile.scan_frequency = "hourly"
            elif "week" in ans_lower:
                profile.scan_frequency = "weekly"
            else:
                profile.scan_frequency = "daily"

        elif field == "auto_apply_prices":
            if llm_val is not None:
                profile.auto_apply_prices = bool(llm_val)
            else:
                profile.auto_apply_prices = _parse_bool(answer)

        elif field == "alert_threshold_pct":
            profile.alert_threshold_pct = (
                float(llm_val) if llm_val else _parse_float_pct(answer)
            )

        elif field == "cost_margin_floor":
            profile.cost_margin_floor = (
                float(llm_val) if llm_val else _parse_float_pct(answer)
            )

        collected[field] = getattr(profile, field, answer)
        print(f"     ✓  Saved.")

    profile.onboarding_complete = True
    state.retailer_profile = profile
    state.needs_onboarding = False

    print("\n" + "═" * 60)
    print("  ✅  Onboarding complete!")
    print(f"  {profile.summary()}")
    print("═" * 60 + "\n")

    return state


def load_demo_profile() -> RetailerProfile:
    """Returns a fully populated demo profile for testing without interaction."""
    p = RetailerProfile()
    p.store_name = "TechZone Electronics"
    p.category = "electronics"
    p.subcategories = ["smartphones", "televisions", "headphones", "appliances"]
    p.location = "Coimbatore, Tamil Nadu"
    p.brand_positioning = "mid-market"
    p.known_competitors = ["Reliance Digital", "Croma", "Amazon India", "Flipkart"]
    p.pricing_strategy = "competitive_parity"
    p.cost_margin_floor = 0.10
    p.max_price_shift_pct = 0.15
    p.auto_apply_prices = False
    p.alert_threshold_pct = 0.05
    p.scan_frequency = "daily"
    p.catalog = DEMO_CATALOG
    p.onboarding_complete = True
    return p