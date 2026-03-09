"""
RetailAgent — Pricing Agent (LCEL Chain + Structured Output)
=============================================================
LangChain patterns used:
  - with_structured_output()   → bind Pydantic schema directly to model
                                  guarantees structured JSON every time
  - ChatPromptTemplate         → multi-variable prompt with strategy context
  - LCEL pipe chain (|)        → prompt | llm.with_structured_output | validator
  - RunnableLambda             → wraps guardrail rules as a Runnable in the chain
  - batch()                    → process all products in one LLM batch call

Chain structure:
  pricing_prompt
    | llm.with_structured_output(RecommendationOutput)
    | RunnableLambda(apply_guardrails)

Each product gets a Chain-of-Thought recommendation with reasoning,
then guardrails enforce business constraints before final output.
"""

from pydantic import BaseModel, Field
from datetime import datetime

from langchain_core.prompts   import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from core.state import AgentState
from core.llm   import get_llm
from core       import database as db


# ─── Structured output schema ─────────────────────────────────────

class RecommendationOutput(BaseModel):
    """Schema for LLM pricing recommendation — used with with_structured_output()."""
    recommended_price: float = Field(description="The recommended selling price in rupees")
    action:            str   = Field(description="hold | reduce | raise | aggressive_reduce")
    confidence:        float = Field(description="Confidence score 0.0 to 1.0", ge=0.0, le=1.0)
    reasoning:         str   = Field(description="2-3 sentence explanation of the recommendation")


# ─── Pricing prompt ───────────────────────────────────────────────

PRICING_SYSTEM = """You are the Pricing Agent for RetailAgent, a competitive intelligence system.

Reason step by step before recommending:
1. What is the retailer's rank vs competitors?
2. Does their positioning justify a premium over the cheapest option?
3. Is the competitor move temporary (one data point) or a sustained trend?
4. What price preserves margin while improving competitiveness?

Actions:
  hold              → no change needed (within acceptable range)
  reduce            → modest reduction 1–5% (slightly above market)
  aggressive_reduce → larger reduction 5–15% (significantly behind market)
  raise             → price can be raised (currently cheapest, margin room exists)
"""

PRICING_HUMAN = """
Product: {product_name}
Your current price: ₹{current_price}
Your cost: ₹{cost}
Your positioning: {positioning}
Your strategy: {strategy}

Competitor prices:
{competitor_breakdown}

Analytics:
- Price rank: {rank} of {total} (including you)
- Gap to cheapest: ₹{gap_abs} ({gap_pct}% above cheapest)
- 7-day trend: {trend}
- Anomaly detected: {is_anomaly}

Minimum acceptable margin: {margin_floor}%
Max single-cycle price shift: {max_shift}%

Recommend the optimal price for this product.
"""


# ─── Guardrail function (wrapped as RunnableLambda) ───────────────

def _apply_guardrails(payload: dict) -> dict:
    """
    Enforce hard business rules AFTER the LLM recommendation.
    Wrapped as a RunnableLambda — part of the LCEL chain.
    """
    rec          = payload["llm_rec"]       # RecommendationOutput
    current      = payload["current_price"]
    cost         = payload["cost"]
    margin_floor = payload["margin_floor"]
    max_shift    = payload["max_shift"]

    price    = rec.recommended_price
    note     = ""
    applied  = False

    # Rule 1: margin floor
    if cost > 0:
        min_ok = cost * (1 + margin_floor)
        if price < min_ok:
            price   = round(min_ok, -1)
            applied = True
            note   += f"Margin floor applied (min ₹{min_ok:.0f}). "

    # Rule 2: max single-cycle downshift
    max_down = current * (1 - max_shift)
    if price < max_down:
        price   = round(max_down, -1)
        applied = True
        note   += f"Max downshift cap ({max_shift*100:.0f}%) applied. "

    # Rule 3: max single-cycle upshift
    max_up = current * (1 + max_shift)
    if price > max_up:
        price   = round(max_up, -1)
        applied = True
        note   += f"Max upshift cap ({max_shift*100:.0f}%) applied. "

    # Rule 4: ignore negligible changes
    change     = price - current
    change_pct = change / current if current else 0.0
    action     = rec.action

    if abs(change_pct) < 0.005:
        price      = current
        change     = 0.0
        change_pct = 0.0
        action     = "hold"

    return {
        "retailer_sku":      payload["sku"],
        "product_name":      payload["product_name"],
        "current_price":     current,
        "recommended_price": price,
        "price_change":      round(change, 2),
        "price_change_pct":  round(change_pct, 4),
        "action":            action,
        "confidence":        rec.confidence,
        "reasoning":         rec.reasoning,
        "guardrail_applied": applied,
        "guardrail_note":    note.strip(),
        "approved":          None,
        "created_at":        datetime.now().isoformat(),
    }


# ─── Rule-based fallback ──────────────────────────────────────────

def _rule_based_rec(analytics: dict, profile) -> RecommendationOutput:
    my_price  = analytics["retailer_price"]
    min_price = analytics["min_competitor_price"]
    avg_price = analytics["avg_competitor_price"]
    strategy  = profile.pricing_strategy

    if strategy == "penetration":
        target = min_price * 0.98
        action = "aggressive_reduce" if my_price > target * 1.02 else "hold"
    elif strategy == "premium":
        target = max(avg_price * 1.05, my_price * 0.97)
        action = "hold" if my_price >= avg_price else "reduce"
    else:
        target = avg_price
        if my_price > avg_price * 1.05:
            action = "reduce"
        elif my_price < avg_price * 0.97 and analytics["price_rank"] == 1:
            action = "raise"
        else:
            action = "hold"

    return RecommendationOutput(
        recommended_price = round(target, -1),
        action            = action,
        confidence        = 0.65,
        reasoning         = (f"Rule-based {strategy}. Rank {analytics['price_rank']}/"
                             f"{analytics['total_competitors']+1}. "
                             f"Market avg ₹{avg_price:.0f}, min ₹{min_price:.0f}."),
    )


# ─── LangGraph node ───────────────────────────────────────────────

def run_pricing_node(state: AgentState) -> dict:
    """
    LangGraph node: Pricing Agent.
    Uses LCEL chain with with_structured_output() for guaranteed JSON.
    Falls back to rule-based if LLM unavailable.
    """
    analytics_list = state["analytics"]
    profile        = state["retailer_profile"]
    catalog        = {p["sku"]: p for p in profile.catalog}
    retailer_id    = state["retailer_id"]
    cycle_id       = state["cycle_id"]

    print(f"\n[Pricing] Generating recommendations for {len(analytics_list)} products...")

    if not analytics_list:
        return {"recommendations": [], "current_node": "pricing"}

    # Build LCEL chain with structured output
    try:
        llm   = get_llm(temperature=0.05)
        prompt = ChatPromptTemplate.from_messages([
            ("system", PRICING_SYSTEM),
            ("human",  PRICING_HUMAN),
        ])
        # with_structured_output guarantees RecommendationOutput every time
        structured_llm = llm.with_structured_output(RecommendationOutput)
        llm_chain      = prompt | structured_llm
        use_llm        = True
    except Exception:
        use_llm = False

    recommendations = []
    llm_ct, rule_ct = 0, 0

    for analytics in analytics_list:
        sku     = analytics["retailer_sku"]
        product = catalog.get(sku, {})
        if analytics["total_competitors"] == 0:
            continue

        current  = analytics["retailer_price"]
        cost     = float(product.get("cost", 0))

        comp_breakdown = "\n".join(
            f"  • {c}: ₹{p:,.0f}" for c, p in analytics["competitor_prices"].items()
        )

        # Get LLM recommendation
        if use_llm:
            try:
                llm_rec = llm_chain.invoke({
                    "product_name":        analytics["product_name"],
                    "current_price":       f"{current:,.0f}",
                    "cost":                f"{cost:,.0f}" if cost else "unknown",
                    "positioning":         profile.brand_positioning,
                    "strategy":            profile.pricing_strategy,
                    "competitor_breakdown": comp_breakdown,
                    "rank":                analytics["price_rank"],
                    "total":               analytics["total_competitors"] + 1,
                    "gap_abs":             f"{analytics['price_gap_to_min']:,.0f}",
                    "gap_pct":             f"{analytics['price_gap_pct_to_min']*100:.1f}",
                    "trend":               analytics["trend"],
                    "is_anomaly":          analytics["is_anomaly"],
                    "margin_floor":        f"{profile.cost_margin_floor*100:.0f}",
                    "max_shift":           f"{profile.max_price_shift_pct*100:.0f}",
                })
                llm_ct += 1
            except Exception as e:
                print(f"  [Pricing] LLM failed for {analytics['product_name']}: {e}")
                llm_rec   = _rule_based_rec(analytics, profile)
                rule_ct  += 1
        else:
            llm_rec  = _rule_based_rec(analytics, profile)
            rule_ct += 1

        # Apply guardrails via RunnableLambda
        guardrail_fn = RunnableLambda(_apply_guardrails)
        final_rec    = guardrail_fn.invoke({
            "llm_rec":       llm_rec,
            "current_price": current,
            "cost":          cost,
            "margin_floor":  profile.cost_margin_floor,
            "max_shift":     profile.max_price_shift_pct,
            "sku":           sku,
            "product_name":  analytics["product_name"],
        })

        recommendations.append(final_rec)

        sym   = "↓" if final_rec["price_change"] < 0 else ("↑" if final_rec["price_change"] > 0 else "─")
        print(f"  {sym} {final_rec['product_name'][:45]:<45} "
              f"₹{current:,.0f} → ₹{final_rec['recommended_price']:,.0f} "
              f"[{final_rec['action']}] {final_rec['confidence']:.0%}")

    # Auto-apply if configured
    if profile.auto_apply_prices:
        _auto_apply(recommendations, profile.catalog)
        print(f"  [Auto-applied] {sum(1 for r in recommendations if r['approved'])} price changes.")

    db.save_recommendations(retailer_id, cycle_id, recommendations)

    actionable = sum(1 for r in recommendations if r["action"] != "hold")
    print(f"[Pricing] {len(recommendations)} recs | LLM={llm_ct} | rule={rule_ct} | actionable={actionable}")

    return {
        "recommendations": recommendations,
        "current_node":    "pricing",
    }


def _auto_apply(recommendations: list, catalog: list):
    price_idx = {p["sku"]: i for i, p in enumerate(catalog)}
    for rec in recommendations:
        if rec["action"] != "hold":
            idx = price_idx.get(rec["retailer_sku"])
            if idx is not None:
                catalog[idx]["current_price"] = rec["recommended_price"]
                rec["approved"] = True