"""
RetailAgent — Reporter Agent (LangChain LCEL Summarization Chain)
==================================================================
LangChain patterns used:
  - LCEL chain                   → context_builder | prompt | llm | str_parser
  - ChatPromptTemplate           → structured prompt for briefing generation
  - StrOutputParser              → parse plain text briefing from LLM
"""

from langchain_core.prompts        import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from core.state import AgentState
from core.llm   import get_llm
from datetime   import datetime


REPORTER_SYSTEM = """You are the morning briefing writer for RetailAgent.
Write a concise, plain-English briefing for a retail store owner.

Structure:
1. One-line competitive health summary
2. Top 3 most urgent pricing opportunities (be specific with numbers)
3. Any significant competitor moves or anomalies  
4. One recommended focus for today

Under 200 words. Write in short paragraphs. No bullet points. 
Sound like a trusted analyst talking to a busy owner.
"""


def run_reporter_node(state: AgentState) -> dict:
    """
    LangGraph node: Reporter Agent.
    Uses StuffDocumentsChain to summarize the cycle context into a briefing.
    """
    analytics       = state["analytics"]
    recommendations = state["recommendations"]
    alerts          = state["alerts"]
    profile         = state["retailer_profile"]

    print("\n[Reporter] Generating morning briefing...")

    if not analytics:
        return {"morning_briefing": "No data collected this cycle.", "current_node": "reporter"}

    # Compute summary stats
    total    = len(analytics)
    cheapest = sum(1 for a in analytics if a["price_rank"] == 1)
    above    = sum(1 for a in analytics if a["price_gap_pct_to_min"] > 0.05)
    anomalies = sum(1 for a in analytics if a["is_anomaly"])
    actionable = [r for r in recommendations if r["action"] != "hold"]

    top_recs = "\n".join([
        f"- {r['product_name']}: ₹{r['current_price']:,.0f} → ₹{r['recommended_price']:,.0f} "
        f"({r['action']}): {r['reasoning'][:100]}"
        for r in sorted(actionable, key=lambda x: abs(x["price_change_pct"]), reverse=True)[:3]
    ]) or "No urgent changes needed."

    alert_lines = "\n".join([
        f"- [{a['severity'].upper()}] {a['message']}"
        for a in alerts[:5]
    ]) or "No alerts."

    # Build context as LangChain Document
    context_text = f"""
Store: {profile.store_name} | Category: {profile.category} | Positioning: {profile.brand_positioning}
Strategy: {profile.pricing_strategy} | Date: {datetime.now().strftime('%d %b %Y')}

Cycle Summary:
- Products monitored: {total}
- Cheapest on: {cheapest}/{total} ({cheapest/total*100:.0f}%)
- Above market (>5% gap): {above}/{total}
- Anomalies detected: {anomalies}
- Price changes recommended: {len(actionable)}

Top Recommendations:
{top_recs}

Key Alerts:
{alert_lines}
"""

    try:
        llm = get_llm(temperature=0.3)

        # Modern LCEL chain pattern
        prompt = ChatPromptTemplate.from_messages([
            ("system", REPORTER_SYSTEM),
            ("human",  "Write the morning briefing based on:\n\n{context}"),
        ])
        
        chain = prompt | llm | StrOutputParser()
        briefing = chain.invoke({"context": context_text})

    except Exception as e:
        print(f"  [Reporter] LLM unavailable ({e}), using template briefing.")
        briefing = _template_briefing(state, total, cheapest, above, anomalies, actionable, alerts)

    print("[Reporter] Briefing ready.")

    return {
        "morning_briefing": briefing,
        "current_node":     "reporter",
    }


def _template_briefing(state, total, cheapest, above, anomalies, actionable, alerts) -> str:
    profile = state["retailer_profile"]
    lines = [
        f"📊 COMPETITIVE BRIEFING — {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
        f"Store: {profile.store_name}",
        "─" * 50,
        f"\nMARKET POSITION",
        f"You are cheapest on {cheapest}/{total} products ({cheapest/total*100:.0f}%). "
        f"{above} products are priced more than 5% above the cheapest competitor.",
    ]

    if anomalies:
        anomaly_prods = [a["product_name"] for a in state["analytics"] if a["is_anomaly"]]
        lines.append(f"\n🚨 ANOMALY: Unusual price moves on {', '.join(anomaly_prods[:2])}.")

    high_alerts = [a for a in alerts if a.get("severity") == "high"]
    if high_alerts:
        lines.append(f"\n🔴 {len(high_alerts)} HIGH PRIORITY alert(s) need attention.")

    if actionable:
        lines.append("\n💡 TOP RECOMMENDATIONS")
        for r in sorted(actionable, key=lambda x: abs(x["price_change_pct"]), reverse=True)[:3]:
            sym = "↓" if r["price_change"] < 0 else "↑"
            lines.append(f"  {sym} {r['product_name']}: ₹{r['current_price']:,.0f} → "
                         f"₹{r['recommended_price']:,.0f} ({r['price_change_pct']*100:+.1f}%)")
            lines.append(f"    → {r['reasoning'][:100]}")
    else:
        lines.append("\n✅ Pricing is well-positioned. No urgent changes needed.")

    return "\n".join(lines)