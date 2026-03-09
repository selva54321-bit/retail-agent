"""
RetailAgent — Pricing Agent
=============================
Combines LLM reasoning with a hard guardrail rules engine to produce
strategy-aware price recommendations.

For each product:
  1. LLM receives: product, competitor prices, analytics, retailer strategy
  2. LLM reasons through a recommendation with justification (Chain-of-Thought)
  3. Guardrail engine enforces: margin floors, max price shift, reasonable bounds
  4. If auto_apply=True, the price is written back to the catalog in the DB
  5. If suggest-only, recommendation is queued for human approval
"""

import json
from datetime import datetime
from core.state import AgentState, PricingRecommendation
from core.llm import chat_json, is_available
from core import database as db


SYSTEM_PROMPT = """You are the Pricing Agent for RetailAgent, a competitive pricing system.

Your job is to recommend optimal pricing for a retailer based on:
- Competitor prices and trends
- The retailer's stated strategy and positioning
- Market analytics (price rank, gap analysis, anomalies)

Think step by step before recommending. Consider:
1. Is the competitor price change temporary (flash sale) or sustained trend?
2. Does the retailer's positioning justify a price premium?
3. What's the minimum price that protects margin?
4. Will a price change actually improve the retailer's competitive position?

Return a JSON object:
{
  "recommended_price": number,
  "action": "hold|reduce|raise|aggressive_reduce",
  "confidence": 0.0-1.0,
  "reasoning": "2-3 sentence explanation of the recommendation"
}

Actions:
- hold: no change needed
- reduce: modest reduction (1-5%)
- aggressive_reduce: larger reduction (5-15%) when significantly behind
- raise: price can be increased (currently cheapest with margin to spare)
"""


def _rule_based_recommendation(analytics: dict, profile) -> dict:
    """
    Rule-based fallback when LLM is unavailable.
    Implements a simple competitive parity strategy with guardrails.
    """
    my_price  = analytics["retailer_price"]
    min_price = analytics["min_competitor_price"]
    avg_price = analytics["avg_competitor_price"]
    rank      = analytics["price_rank"]
    trend     = analytics["trend"]
    strategy  = profile.pricing_strategy

    # Base recommendation on strategy
    if strategy == "penetration":
        target = min_price * 0.98   # undercut cheapest by 2%
        action = "aggressive_reduce" if my_price > target else "hold"
    elif strategy == "premium":
        # Premium brands don't chase down to minimum
        target = max(avg_price * 1.05, my_price * 0.97)
        action = "hold" if my_price >= avg_price else "reduce"
    elif strategy in ("competitive_parity", "value"):
        target = avg_price
        if my_price > avg_price * 1.05:
            action = "reduce"
        elif my_price < avg_price * 0.97 and rank == 1:
            action = "raise"
        else:
            action = "hold"
        target = avg_price
    else:  # cost_plus
        action = "hold"
        target = my_price

    confidence = 0.70 if trend == "falling" else 0.55
    reasoning = (
        f"Rule-based {strategy} strategy. "
        f"Current rank: {rank}/{analytics['total_competitors']+1}. "
        f"Market avg: ₹{avg_price:.0f}, min: ₹{min_price:.0f}. "
        f"Trend: {trend}."
    )

    return {
        "recommended_price": round(target, -1),   # round to nearest 10
        "action": action,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def _apply_guardrails(rec_price: float, current_price: float,
                      product: dict, profile) -> tuple[float, bool, str]:
    """
    Enforce hard business rules on the recommendation.
    Returns (final_price, guardrail_was_triggered, note).
    """
    cost           = float(product.get("cost", 0))
    margin_floor   = profile.cost_margin_floor
    max_shift_pct  = profile.max_price_shift_pct

    note = ""
    triggered = False

    # 1. Margin floor: never go below cost + minimum margin
    if cost > 0:
        min_acceptable = cost * (1 + margin_floor)
        if rec_price < min_acceptable:
            rec_price = round(min_acceptable, -1)
            triggered = True
            note += f"Margin floor applied (min ₹{min_acceptable:.0f}). "

    # 2. Max single-cycle shift
    max_down = current_price * (1 - max_shift_pct)
    max_up   = current_price * (1 + max_shift_pct)

    if rec_price < max_down:
        rec_price = round(max_down, -1)
        triggered = True
        note += f"Max downshift cap applied ({max_shift_pct*100:.0f}% limit). "

    if rec_price > max_up:
        rec_price = round(max_up, -1)
        triggered = True
        note += f"Max upshift cap applied ({max_shift_pct*100:.0f}% limit). "

    # 3. Sanity: price must be positive
    if rec_price <= 0:
        rec_price = current_price
        triggered = True
        note += "Invalid price clamped to current. "

    return rec_price, triggered, note.strip()


def run_pricing(state: AgentState, retailer_id: int) -> AgentState:
    """
    Generates pricing recommendations for all analyzed products.
    Applies guardrails and saves to DB.
    """
    print("\n[Pricing] Generating strategy-aware recommendations...")

    analytics_list = state.analytics
    profile        = state.retailer_profile
    catalog        = {p["sku"]: p for p in profile.catalog}
    cycle_id       = state.cycle_id

    if not analytics_list:
        print("[Pricing] No analytics to process.")
        return state

    recommendations = []
    llm_used = 0
    rule_used = 0

    for analytics in analytics_list:
        sku          = analytics["retailer_sku"]
        product_name = analytics["product_name"]
        current_price = analytics["retailer_price"]
        product      = catalog.get(sku, {})

        # Skip if no meaningful competitor data
        if analytics["total_competitors"] == 0:
            continue

        # ── Get raw recommendation ──────────────────
        if is_available():
            try:
                comp_breakdown = "\n".join(
                    f"  - {c}: ₹{p:.0f}" for c, p in
                    analytics.get("competitor_prices", {}).items()
                )
                user_msg = f"""
Product: {product_name}
Retailer's current price: ₹{current_price:.0f}
Retailer's cost: ₹{product.get('cost', 'unknown')}
Retailer positioning: {profile.brand_positioning}
Pricing strategy: {profile.pricing_strategy}

Competitor prices:
{comp_breakdown}

Analytics:
- Price rank: {analytics['price_rank']} out of {analytics['total_competitors']+1}
- Gap to cheapest: ₹{analytics['price_gap_to_min']:.0f} ({analytics['price_gap_pct_to_min']*100:.1f}%)
- 7-day trend: {analytics['trend']}
- Anomaly: {analytics['is_anomaly']} {analytics.get('anomaly_reason','') or ''}
- Min competitor: ₹{analytics['min_competitor_price']:.0f}
- Avg competitor: ₹{analytics['avg_competitor_price']:.0f}

Recommend a price. Be strategic — don't just match the minimum if it's not appropriate.
"""
                raw_rec = chat_json("pricing", SYSTEM_PROMPT, user_msg)
                llm_used += 1
            except Exception as e:
                print(f"  [Pricing] LLM failed for {product_name}: {e}")
                raw_rec = _rule_based_recommendation(analytics, profile)
                rule_used += 1
        else:
            raw_rec = _rule_based_recommendation(analytics, profile)
            rule_used += 1

        # ── Apply guardrails ───────────────────────
        raw_price   = float(raw_rec.get("recommended_price", current_price))
        final_price, guardrail_applied, guardrail_note = _apply_guardrails(
            raw_price, current_price, product, profile
        )

        price_change     = final_price - current_price
        price_change_pct = price_change / current_price if current_price else 0.0

        # Don't recommend if change is negligible (<0.5%)
        action = raw_rec.get("action", "hold")
        if abs(price_change_pct) < 0.005:
            action      = "hold"
            final_price = current_price
            price_change = 0.0
            price_change_pct = 0.0

        rec = {
            "retailer_sku":      sku,
            "product_name":      product_name,
            "current_price":     current_price,
            "recommended_price": final_price,
            "price_change":      round(price_change, 2),
            "price_change_pct":  round(price_change_pct, 4),
            "action":            action,
            "confidence":        float(raw_rec.get("confidence", 0.5)),
            "reasoning":         raw_rec.get("reasoning", ""),
            "guardrail_applied": guardrail_applied,
            "guardrail_note":    guardrail_note,
            "approved":          None,  # pending human review
            "created_at":        datetime.now().isoformat(),
        }
        recommendations.append(rec)

        symbol = "↓" if price_change < 0 else ("↑" if price_change > 0 else "─")
        print(f"  {symbol} {product_name[:45]:<45} "
              f"₹{current_price:.0f} → ₹{final_price:.0f} "
              f"[{action}] conf={raw_rec.get('confidence',0):.0%}")

    # ── Auto-apply if configured ────────────────
    if profile.auto_apply_prices:
        _auto_apply(recommendations, profile.catalog, retailer_id)

    db.save_recommendations(retailer_id, cycle_id, recommendations)
    state.recommendations = recommendations

    print(f"[Pricing] {len(recommendations)} recommendations | "
          f"LLM={llm_used} | rule-based={rule_used}")

    actionable = sum(1 for r in recommendations if r["action"] != "hold")
    print(f"  Actionable (non-hold): {actionable}")

    return state


def _auto_apply(recommendations: list, catalog: list, retailer_id: int):
    """Write approved price changes directly back to the retailer's catalog."""
    price_map = {p["sku"]: i for i, p in enumerate(catalog)}
    applied = 0
    for rec in recommendations:
        if rec["action"] != "hold":
            idx = price_map.get(rec["retailer_sku"])
            if idx is not None:
                catalog[idx]["current_price"] = rec["recommended_price"]
                rec["approved"] = True
                applied += 1
    if applied:
        print(f"  [Auto-apply] {applied} price changes written to catalog.")