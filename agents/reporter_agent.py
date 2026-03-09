"""
RetailAgent — Reporter Agent
==============================
Generates a plain-English morning briefing summarising the latest cycle.
Uses Llama 3.1 8B (via Ollama) if available; falls back to a structured
template report if the LLM is offline.
"""

from datetime import datetime
from core.state import AgentState
from core.llm import chat, is_available
from core import database as db


SYSTEM_PROMPT = """You are the Reporter Agent for RetailAgent, a competitive pricing system.

Write a concise, actionable morning briefing for a retail store owner based on the 
latest competitive pricing cycle data.

The briefing should:
1. Start with a 1-sentence competitive health summary
2. Highlight the most urgent pricing opportunities (max 3)
3. Note any significant competitor moves or anomalies
4. End with a recommended focus for today

Keep it under 200 words. Write in plain English, like a trusted analyst talking to a busy owner.
Do NOT use bullet points — write in short paragraphs.
"""


def _template_briefing(state: AgentState) -> str:
    """Structured template briefing when LLM is unavailable."""
    analytics      = state.analytics
    recommendations = state.recommendations
    alerts         = state.alerts
    profile        = state.retailer_profile

    if not analytics:
        return "No data collected in this cycle. Check scraper status."

    total         = len(analytics)
    cheapest      = sum(1 for a in analytics if a["price_rank"] == 1)
    above_market  = sum(1 for a in analytics if a["price_gap_pct_to_min"] > 0.05)
    falling_trend = sum(1 for a in analytics if a["trend"] == "falling")
    anomalies     = sum(1 for a in analytics if a["is_anomaly"])
    actionable    = [r for r in recommendations if r["action"] != "hold"]
    high_alerts   = [a for a in alerts if a.get("severity") == "high"]

    lines = [
        f"📊 COMPETITIVE BRIEFING — {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
        f"Store: {profile.store_name}",
        "─" * 50,
        "",
        f"MARKET POSITION",
        f"You are the cheapest on {cheapest} of {total} tracked products "
        f"({cheapest/total*100:.0f}%). "
        f"{above_market} products are priced more than 5% above the cheapest competitor.",
    ]

    if falling_trend > 0:
        lines.append(
            f"\n⚠ MARKET MOVEMENT\n"
            f"{falling_trend} product(s) show a sustained downward price trend. "
            f"Competitors may be systematically lowering prices in these categories."
        )

    if anomalies > 0:
        anomaly_products = [a["product_name"] for a in analytics if a["is_anomaly"]]
        lines.append(
            f"\n🚨 ANOMALIES DETECTED\n"
            f"{anomalies} unusual price movement(s): "
            f"{', '.join(anomaly_products[:3])}."
        )

    if high_alerts:
        lines.append(f"\n🔴 HIGH PRIORITY ALERTS: {len(high_alerts)} items need attention.")

    if actionable:
        lines.append(f"\n💡 RECOMMENDATIONS")
        for r in sorted(actionable, key=lambda x: abs(x["price_change_pct"]), reverse=True)[:3]:
            symbol = "↓" if r["price_change"] < 0 else "↑"
            lines.append(
                f"  {symbol} {r['product_name']}: "
                f"₹{r['current_price']:.0f} → ₹{r['recommended_price']:.0f} "
                f"({r['price_change_pct']*100:+.1f}%) — {r['action']}"
            )
            if r.get("reasoning"):
                lines.append(f"    Reason: {r['reasoning'][:100]}")

    if not actionable:
        lines.append("\n✅ Your pricing is well-positioned. No urgent changes needed today.")

    lines.append(f"\nNext scan: {profile.scan_frequency}")
    return "\n".join(lines)


def run_reporter(state: AgentState, retailer_id: int) -> AgentState:
    """
    Generates and stores the morning briefing.
    Updates state.morning_briefing.
    """
    print("\n[Reporter] Generating briefing...")

    if is_available() and state.analytics:
        try:
            # Build a rich context for the LLM
            analytics      = state.analytics
            recommendations = state.recommendations
            alerts         = state.alerts
            profile        = state.retailer_profile

            total     = len(analytics)
            cheapest  = sum(1 for a in analytics if a["price_rank"] == 1)
            actionable = [r for r in recommendations if r["action"] != "hold"]

            top_recs = []
            for r in sorted(actionable, key=lambda x: abs(x["price_change_pct"]), reverse=True)[:3]:
                top_recs.append(
                    f"- {r['product_name']}: current ₹{r['current_price']:.0f} → "
                    f"recommended ₹{r['recommended_price']:.0f} ({r['action']}): {r['reasoning'][:100]}"
                )

            alert_summary = []
            for a in alerts[:5]:
                alert_summary.append(f"- [{a['severity']}] {a['message'][:100]}")

            context = f"""
Store: {profile.store_name} | Category: {profile.category} | Positioning: {profile.brand_positioning}
Strategy: {profile.pricing_strategy}

Cycle Summary:
- Products analyzed: {total}
- Cheapest on: {cheapest}/{total}
- Products above market (>5% gap): {sum(1 for a in analytics if a['price_gap_pct_to_min'] > 0.05)}
- Anomalies detected: {sum(1 for a in analytics if a['is_anomaly'])}
- Price recommendations: {len(actionable)} actionable

Top Recommendations:
{chr(10).join(top_recs) if top_recs else 'No urgent changes needed.'}

Key Alerts:
{chr(10).join(alert_summary) if alert_summary else 'No alerts.'}
"""
            briefing = chat(
                "reporter",
                SYSTEM_PROMPT,
                f"Write the morning briefing based on this data:\n{context}"
            )
        except Exception as e:
            print(f"[Reporter] LLM failed, using template: {e}")
            briefing = _template_briefing(state)
    else:
        briefing = _template_briefing(state)

    state.morning_briefing = briefing
    print("[Reporter] Briefing ready.\n")
    return state