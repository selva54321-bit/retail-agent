"""
RetailAgent — Intake Agent (LangChain LCEL + Chat History)
===========================================================
LangChain patterns used:
  - ChatPromptTemplate + MessagesPlaceholder → conversational prompt
  - Manual chat history (list of HumanMessage/AIMessage) → replaces deprecated ConversationChain
  - LCEL chain: prompt | llm | StrOutputParser → dialogue turn
  - make_json_chain (LCEL) → extract structured RetailerProfile from transcript

Note: EXTRACT_SYSTEM uses {{}} to escape JSON braces so LangChain does
not treat them as template variables. Only {transcript} is a real variable.
"""

from langchain_core.prompts        import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages       import HumanMessage, AIMessage
from langchain_core.output_parsers import StrOutputParser

from core.state import AgentState, RetailerProfile
from core.llm   import get_llm, make_json_chain


# ─────────────────────────────────────────────────────────────────
#  ONBOARDING DIALOGUE PROMPT
# ─────────────────────────────────────────────────────────────────

ONBOARDING_SYSTEM = """You are RetailAgent's onboarding assistant.
Your job is to collect information from a retailer to set up automated
competitor price monitoring.

Be conversational, friendly, and concise. Ask ONE thing at a time.
After the retailer answers, acknowledge it briefly and ask the next question.

Collect in this order:
1. Store name and what they sell (product category)
2. Their city / neighbourhood / region
3. Their brand positioning (budget / mid-market / premium / specialist)
4. Their main competitors (names or websites — local stores AND online platforms)
5. Their pricing strategy (competitive parity / penetration / premium / value / cost-plus)
6. How often to scan prices (hourly / daily / weekly)
7. Whether to auto-apply price changes or just suggest them
8. Their minimum profit margin % (e.g. 12%)
9. Their product catalog — ask them to enter each product as:
   ProductName | SKU | CurrentSellingPrice | YourCost
   (one per line, blank line when done)

When all information is collected, say exactly:
"Perfect! I have everything I need. Setting up your RetailAgent now."
"""


# ─────────────────────────────────────────────────────────────────
#  EXTRACTION PROMPT  —  dialogue transcript → structured JSON
#  IMPORTANT: all JSON braces are doubled ({{ }}) so LangChain
#  does not confuse them with template variables.
# ─────────────────────────────────────────────────────────────────

EXTRACT_SYSTEM = """You are a data extraction assistant.
Given a conversation transcript between a retailer and an onboarding assistant,
extract the retailer's information into a structured JSON object.

Return ONLY this JSON (no markdown, no explanation, no extra keys):
{{
  "store_name":        "string",
  "category":          "string",
  "subcategories":     ["list", "of", "strings"],
  "location":          "string (city or neighbourhood)",
  "brand_positioning": "budget|mid-market|premium|specialist_retailer",
  "known_competitors": ["list of competitor names or websites"],
  "pricing_strategy":  "competitive_parity|penetration|premium|value|cost_plus",
  "cost_margin_floor": 0.12,
  "max_price_shift_pct": 0.15,
  "auto_apply_prices": false,
  "alert_threshold_pct": 0.05,
  "scan_frequency":    "hourly|daily|weekly",
  "catalog": [
    {{"name": "string", "sku": "string", "current_price": 0.0, "cost": 0.0}}
  ]
}}
"""


# ─────────────────────────────────────────────────────────────────
#  CATEGORY → COMPETITORS MAPPING
#  Maps product categories to the competitors we can reliably scrape.
#  Each competitor name must match a key in COMPETITOR_URL_MAP
#  (planner_agent.py) so URLs get generated correctly.
#  Expand this dict to support more categories later.
# ─────────────────────────────────────────────────────────────────

CATEGORY_COMPETITORS = {
    "electronics":   ["Amazon India", "Flipkart", "Poorvika", "Croma"],
    "televisions":   ["Amazon India", "Flipkart", "Poorvika", "Croma"],
    "tv":            ["Amazon India", "Flipkart", "Poorvika", "Croma"],
    "mobile":        ["Amazon India", "Flipkart", "Poorvika", "Sangeetha"],
    "appliances":    ["Amazon India", "Flipkart", "Croma", "Reliance Digital"],
}


def get_competitors_for_category(category: str) -> list[str]:
    """Resolve competitors from category. Falls back to electronics defaults."""
    cat = category.lower().strip()
    for key, comps in CATEGORY_COMPETITORS.items():
        if key in cat or cat in key:
            return list(comps)
    return CATEGORY_COMPETITORS["electronics"]  # safe default


# ─────────────────────────────────────────────────────────────────
#  DEMO CATALOG  —  TV specialty store in Coimbatore
#  Products match what the demo profile sells so search URLs
#  use exact product names and return real results.
# ─────────────────────────────────────────────────────────────────

DEMO_CATALOG = [
    # {
    #     "name":          "LG 32-inch Smart HD TV LM576",
    #     "sku":           "LG-32-LM576",
    #     "current_price": 16912,
    #     "cost":          14375,
    # },
    {
        "name":          "LG 81.28 cm 32 inch Full HD LED Smart WebOS TV",
        "sku":           "32LQ570BPSA",
        "current_price": 17912,
        "cost":          14375,
    },
    {
        "name":          "108 cm (43 inches) Crystal 4K Vista Pro Ultra HD Smart LED TV",
        "sku":           "UA43UE86AFULXL",
        "current_price": 33230,
        "cost":          28245,
    },
    {
        "name":         "LG HD Ready AI Smart TV 32LR595B6LA 32 inch",
        "sku":          "LG-32-LR595B6LA",
        "current_price": 17912,
        "cost":         14375,
    }
]


def _make_demo_profile() -> RetailerProfile:
    category = "televisions"
    return RetailerProfile(
        store_name           = "The TV Shop Coimbatore",
        category             = category,
        subcategories        = ["LED TV", "OLED TV", "Smart TV", "4K TV"],
        location             = "Saibaba Colony, Coimbatore, Tamil Nadu",
        brand_positioning    = "specialist_retailer",
        known_competitors    = get_competitors_for_category(category),
        pricing_strategy     = "competitive_parity",
        cost_margin_floor    = 0.12,
        max_price_shift_pct  = 0.15,
        auto_apply_prices    = False,
        alert_threshold_pct  = 0.05,
        scan_frequency       = "daily",
        catalog              = DEMO_CATALOG,
        onboarding_complete  = True,
    )


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_intake_node(state: AgentState) -> dict:
    """
    LangGraph node: Intake Agent.
    Runs a multi-turn LCEL dialogue to collect the retailer profile,
    then uses a second LCEL extraction chain to parse it into a
    structured RetailerProfile Pydantic model.

    Uses manual chat history (list of HumanMessage / AIMessage) —
    the modern replacement for the deprecated ConversationChain.
    """
    print("\n" + "═" * 60)
    print("  RETAILAGENT — Retailer Onboarding")
    print("  Powered by LangChain LCEL")
    print("═" * 60 + "\n")

    llm = get_llm(temperature=0.3)

    # LCEL chain for one dialogue turn
    # prompt → llm → plain text response
    dialogue_prompt = ChatPromptTemplate.from_messages([
        ("system",  ONBOARDING_SYSTEM),
        MessagesPlaceholder(variable_name="history"),
        ("human",   "{input}"),
    ])
    dialogue_chain = dialogue_prompt | llm | StrOutputParser()

    # Manual chat history — appended after every turn
    chat_history: list = []

    try:
        # ── Opening turn ─────────────────────────────────────────
        opening = "Hello, I want to set up competitor price monitoring for my store."
        response = dialogue_chain.invoke({"history": chat_history, "input": opening})
        chat_history += [HumanMessage(content=opening), AIMessage(content=response)]
        print(f"  Agent: {response}\n")

        # ── Dialogue loop ─────────────────────────────────────────
        while True:
            user_input = input("  You: ").strip()
            if not user_input:
                continue

            response = dialogue_chain.invoke({"history": chat_history, "input": user_input})
            chat_history += [HumanMessage(content=user_input), AIMessage(content=response)]
            print(f"\n  Agent: {response}\n")

            if "setting up your retailagent now" in response.lower():
                break

    except (KeyboardInterrupt, EOFError):
        print("\n  (Interrupted — loading demo TV shop profile)")
        return _demo_profile_update(state)

    # ── Extract structured profile from transcript ────────────
    transcript = "\n".join(
        f"{'Retailer' if isinstance(m, HumanMessage) else 'Agent'}: {m.content}"
        for m in chat_history
    )

    extract_chain = make_json_chain(
        EXTRACT_SYSTEM,
        "Conversation transcript:\n\n{transcript}\n\nExtract the retailer profile JSON."
    )

    try:
        profile_data = extract_chain.invoke({"transcript": transcript})
    except Exception as e:
        print(f"  [Intake] Extraction failed ({e}) — using fallback profile.")
        profile_data = _fallback_profile_dict()

    if not profile_data.get("catalog"):
        profile_data["catalog"] = DEMO_CATALOG

    profile = RetailerProfile(**{
        k: v for k, v in profile_data.items()
        if k in RetailerProfile.model_fields
    })
    profile.onboarding_complete = True

    # Auto-populate competitors from category if user didn't provide any
    if not profile.known_competitors or len(profile.known_competitors) < 2:
        profile.known_competitors = get_competitors_for_category(profile.category)

    print(f"\n  ✅  {profile.store_name} | {profile.category} | {profile.location}")

    return {
        "retailer_profile": profile,
        "needs_onboarding": False,
        "current_node":     "intake",
    }


# ─────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────

def _fallback_profile_dict() -> dict:
    """Minimal safe fallback when both LLM and extraction fail."""
    return {
        "store_name":          "My Store",
        "category":            "televisions",
        "subcategories":       ["LED TV", "Smart TV"],
        "location":            "Coimbatore",
        "brand_positioning":   "mid-market",
        "known_competitors":   ["Amazon India", "Flipkart", "Croma"],
        "pricing_strategy":    "competitive_parity",
        "cost_margin_floor":   0.12,
        "max_price_shift_pct": 0.15,
        "auto_apply_prices":   False,
        "alert_threshold_pct": 0.05,
        "scan_frequency":      "daily",
        "catalog":             DEMO_CATALOG,
    }


def _demo_profile_update(state: AgentState) -> dict:
    """State update dict using the demo TV shop profile."""
    return {
        "retailer_profile": _make_demo_profile(),
        "needs_onboarding": False,
        "current_node":     "intake",
    }


def load_demo_profile() -> RetailerProfile:
    """
    Public entry point used by main.py --demo flag.
    Returns the fully populated demo TV shop profile.
    """
    return _make_demo_profile()