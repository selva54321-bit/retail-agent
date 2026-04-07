"""
RetailAgent — Reporter Agent (LangChain LCEL Summarization Chain)
==================================================================
LangChain patterns used:
  - load_summarize_chain         → LangChain's built-in summarization chain
  - StuffDocumentsChain          → stuff all context into one prompt (small data)
  - Document                     → wraps analytics context as LangChain Documents
  - StrOutputParser              → parse plain text briefing from LLM
  - LCEL chain                   → context_builder | prompt | llm | str_parser
"""

from langchain_core.prompts        import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents      import Document
from langchain.chains.summarize    import load_summarize_chain

from core.state import AgentState
from core.llm   import get_llm
from datetime   import datetime


REPORTER_SYSTEM = """You are the morning briefing writer for RetailAgent.
Write a concise, plain-English briefing for a retail store owner.

Structure:
1. One-line competitive health summary (price position)
2. Top 2-3 pricing opportunities (specific numbers)
3. Competitor moves: strategy labels, flash sales, unusual price drops
4. Market intelligence: what's selling fast, what new products competitors have
5. One recommended focus for today

Under 250 words. Short paragraphs. No bullet points.
Sound like a trusted analyst talking to a busy owner.
If flash sales were detected, say clearly: "Do NOT match this — it's temporary."
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
    catalog_alerts  = state.get("catalog_alerts", [])
    intel_insights  = state.get("intel_insights", {})

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

    # Catalog spy context
    new_arrivals = [a for a in catalog_alerts if a["type"] == "new_arrival"]
    stock_outs   = [a for a in catalog_alerts if a["type"] == "stock_out"]
    catalog_lines = ""
    if new_arrivals:
        catalog_lines += f"\nNew competitor products ({len(new_arrivals)}):\n"
        catalog_lines += "\n".join(f"- {a['message']}" for a in new_arrivals[:3])
    if stock_outs:
        catalog_lines += f"\nStock-outs detected ({len(stock_outs)}):\n"
        catalog_lines += "\n".join(f"- {a['message']}" for a in stock_outs[:3])

    # Intel context
    strategies   = intel_insights.get("competitor_strategies", {})
    flash_sales  = intel_insights.get("flash_sales", [])
    fast_movers  = intel_insights.get("fast_movers", [])
    opportunities = intel_insights.get("opportunities", [])

    intel_lines = ""
    if strategies:
        intel_lines += "\nCompetitor strategies:\n"
        intel_lines += "\n".join(
            f"- {comp}: {label}" for comp, label in strategies.items()
        )
    if flash_sales:
        intel_lines += f"\n\nFlash sales detected ({len(flash_sales)}):\n"
        intel_lines += "\n".join(
            f"- {f['competitor']}: {f['product'][:40]} dropped {f['drop_pct']}%"
            for f in flash_sales[:3]
        )
    if fast_movers:
        intel_lines += f"\n\nHigh demand products (frequent stock-outs):\n"
        intel_lines += "\n".join(
            f"- {fm['product'][:50]} at {fm['competitor']} ({fm['times_out']}x OOS)"
            for fm in fast_movers[:3]
        )
    if opportunities:
        intel_lines += f"\n\nGrowth opportunities:\n"
        intel_lines += "\n".join(
            f"- [{op['priority'].upper()}] {op['type']}: {op['product'][:45]}"
            for op in opportunities[:3]
        )

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
{catalog_lines}
{intel_lines}
"""

    try:
        llm = get_llm(temperature=0.3)

        # LangChain StuffDocumentsChain pattern
        docs  = [Document(page_content=context_text)]
        chain = load_summarize_chain(
            llm,
            chain_type="stuff",
            prompt=ChatPromptTemplate.from_messages([
                ("system", REPORTER_SYSTEM),
                ("human",  "Write the morning briefing based on:\n\n{text}"),
            ]),
        )
        briefing = chain.invoke({"input_documents": docs})["output_text"]

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