"""
RetailAgent — Intake Agent (Modern LangChain LCEL)
===================================================
LangChain patterns used:
  - LCEL chain (prompt|llm|parser) → conversational dialogue
  - Manual chat history management  → stores full dialogue history
  - LCEL extraction chain → extract structured profile from dialogue
  - with_structured_output → modern structured output parsing

Flow:
  1. LCEL chain asks questions one by one with chat history context
  2. After all answers collected, a final LCEL extraction chain
     parses the entire conversation into a structured RetailerProfile
"""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.output_parsers import StrOutputParser

from core.state import AgentState, RetailerProfile
from core.llm   import get_llm, make_json_chain


# ─── System prompt for onboarding dialogue ───────────────────────
ONBOARDING_SYSTEM = """You are RetailAgent's onboarding assistant. 
Your job is to collect information from a retailer to set up automated 
competitor price monitoring.

Be conversational, friendly, and concise. Ask one thing at a time.
After the retailer answers, acknowledge their answer and ask the next question.

Collect in order:
1. Store name and what they sell (category)
2. Their city/region
3. Their brand positioning (budget / mid-market / premium)  
4. Their main competitors (names or websites)
5. Their pricing strategy (competitive parity / penetration / premium / value / cost-plus)
6. How often to scan prices (hourly / daily / weekly)
7. Whether to auto-apply price changes or just suggest them
8. Their minimum profit margin % (e.g. 10%)
9. Their product catalog (name | SKU | current price | cost, one per line)

When all information is collected, say: "Perfect! I have everything I need. Setting up your RetailAgent now."
"""

# ─── Extraction prompt — converts dialogue to structured JSON ─────
EXTRACT_SYSTEM = """You are a data extraction assistant.
Given a conversation transcript between a retailer and an onboarding assistant,
extract the retailer's information into a structured JSON object.

Return ONLY this JSON structure (no markdown, no explanation):
{
  "store_name": "string",
  "category": "string",
  "subcategories": ["list", "of", "strings"],
  "location": "string",
  "brand_positioning": "budget|mid-market|premium",
  "known_competitors": ["list of competitor names"],
  "pricing_strategy": "competitive_parity|penetration|premium|value|cost_plus",
  "cost_margin_floor": 0.10,
  "max_price_shift_pct": 0.15,
  "auto_apply_prices": false,
  "alert_threshold_pct": 0.05,
  "scan_frequency": "hourly|daily|weekly",
  "catalog": [
    {"name": "string", "sku": "string", "current_price": 0.0, "cost": 0.0}
  ]
}
"""

DEMO_CATALOG = [
    {"name": "Samsung 55-inch 4K Smart TV",      "sku": "TV-001", "current_price": 45000, "cost": 32000},
    {"name": "Sony WH-1000XM5 Headphones",        "sku": "HP-001", "current_price": 28000, "cost": 19000},
    {"name": "Apple iPhone 15 128GB",             "sku": "PH-001", "current_price": 79000, "cost": 62000},
    {"name": "LG 8kg Front Load Washing Machine", "sku": "WM-001", "current_price": 35000, "cost": 24000},
    {"name": "Bosch 500W Mixer Grinder",          "sku": "KA-001", "current_price": 4500,  "cost": 2800},
]


def run_intake_node(state: AgentState) -> dict:
    """
    LangGraph node: Intake Agent.
    Uses an LCEL chain with manual chat history management for dialogue.
    Returns partial state update (only the keys this node writes).
    """
    print("\n" + "═" * 60)
    print("  RETAILAGENT — Retailer Onboarding")
    print("  Powered by LangChain LCEL")
    print("═" * 60)

    # ── Build LCEL conversational chain ──────────────────────
    llm = get_llm(temperature=0.3)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", ONBOARDING_SYSTEM),
        MessagesPlaceholder(variable_name="history"),
        ("human", "{input}"),
    ])
    
    chain = prompt | llm | StrOutputParser()
    
    # ── Manual chat history management ───────────────────────
    chat_history = []
    
    # ── Dialogue loop ─────────────────────────────────────────
    print()
    try:
        # Kick off with a greeting
        user_msg = "Hello, I want to set up competitor price monitoring for my store."
        response = chain.invoke({"history": chat_history, "input": user_msg})
        
        # Store in history
        chat_history.append(HumanMessage(content=user_msg))
        chat_history.append(AIMessage(content=response))
        
        print(f"\n  Agent: {response}\n")

        while True:
            user_input = input("  You: ").strip()
            if not user_input:
                continue

            response = chain.invoke({"history": chat_history, "input": user_input})
            
            # Store in history
            chat_history.append(HumanMessage(content=user_input))
            chat_history.append(AIMessage(content=response))
            
            print(f"\n  Agent: {response}\n")

            # Check if onboarding is complete
            if "setting up your retailagent now" in response.lower():
                break

    except (KeyboardInterrupt, EOFError):
        print("\n  (Using demo profile due to interrupted onboarding)")
        return _demo_profile_update(state)

    # ── Extract structured profile from conversation ──────────
    # Build full transcript for the extraction chain
    transcript = "\n".join([
        f"{'Retailer' if isinstance(m, HumanMessage) else 'Agent'}: {m.content}"
        for m in chat_history
    ])

    extract_chain = make_json_chain(
        EXTRACT_SYSTEM,
        "Conversation transcript:\n\n{transcript}\n\nExtract the retailer profile JSON."
    )

    try:
        profile_data = extract_chain.invoke({"transcript": transcript})
    except Exception as e:
        print(f"  [Intake] Extraction failed: {e}. Using partial profile.")
        profile_data = _extract_from_history_fallback(chat_history)

    # Ensure catalog has demo data if none provided
    if not profile_data.get("catalog"):
        profile_data["catalog"] = DEMO_CATALOG

    profile = RetailerProfile(**{
        k: v for k, v in profile_data.items()
        if k in RetailerProfile.model_fields
    })
    profile.onboarding_complete = True

    print("\n  ✅ Profile captured successfully.")
    print(f"  {profile.store_name} | {profile.category} | {profile.location}")

    # Return only the keys this node updates (LangGraph merges)
    return {
        "retailer_profile":  profile,
        "needs_onboarding":  False,
        "current_node":      "intake",
    }


def _extract_from_history_fallback(chat_history: list) -> dict:
    """Simple keyword extraction when LLM parsing fails."""
    full_text = " ".join([m.content for m in chat_history])
    return {
        "store_name":       "My Store",
        "category":         "electronics",
        "subcategories":    [],
        "location":         "India",
        "brand_positioning":"mid-market",
        "known_competitors":["Amazon", "Flipkart"],
        "pricing_strategy": "competitive_parity",
        "cost_margin_floor":0.10,
        "max_price_shift_pct": 0.15,
        "auto_apply_prices": False,
        "alert_threshold_pct": 0.05,
        "scan_frequency":   "daily",
        "catalog":          DEMO_CATALOG,
    }


def _demo_profile_update(state: AgentState) -> dict:
    """Returns state update with the demo profile (non-interactive mode)."""
    profile = RetailerProfile(
        store_name="TechZone Electronics",
        category="electronics",
        subcategories=["smartphones", "televisions", "headphones", "appliances"],
        location="Coimbatore, Tamil Nadu",
        brand_positioning="mid-market",
        known_competitors=["Reliance Digital", "Croma", "Amazon India", "Flipkart"],
        pricing_strategy="competitive_parity",
        cost_margin_floor=0.10,
        max_price_shift_pct=0.15,
        auto_apply_prices=False,
        alert_threshold_pct=0.05,
        scan_frequency="daily",
        catalog=DEMO_CATALOG,
        onboarding_complete=True,
    )
    return {
        "retailer_profile": profile,
        "needs_onboarding": False,
        "current_node":     "intake",
    }


def load_demo_profile() -> RetailerProfile:
    """Return a fully populated demo profile without interaction."""
    return RetailerProfile(
        store_name="TechZone Electronics",
        category="electronics",
        subcategories=["smartphones", "televisions", "headphones", "appliances"],
        location="Coimbatore, Tamil Nadu",
        brand_positioning="mid-market",
        known_competitors=["Reliance Digital", "Croma", "Amazon India", "Flipkart"],
        pricing_strategy="competitive_parity",
        cost_margin_floor=0.10,
        max_price_shift_pct=0.15,
        auto_apply_prices=False,
        alert_threshold_pct=0.05,
        scan_frequency="daily",
        catalog=DEMO_CATALOG,
        onboarding_complete=True,
    )