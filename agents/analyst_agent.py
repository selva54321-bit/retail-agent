"""
RetailAgent — Analyst Agent (Pure Analytics)
=============================================
No LLM calls here — pure Python + Pandas + SciPy analytics.
LangChain pattern: RunnableLambda wrapping each analysis function,
composed into a pipeline using the | operator.

Four modules run in sequence:
  1. price_ranker       → rank + gap analysis per product
  2. trend_detector     → 7-day rolling linear regression slope
  3. anomaly_detector   → Z-score + IQR statistical flagging
  4. alert_builder      → converts analysis results to Alert objects

All results written to SQLite analytics_results table and LangGraph state.
"""

import statistics
from collections import defaultdict
from datetime    import datetime

from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from core.state import AgentState
from core       import database as db


def _get_seasonality_signal() -> str:
    month = datetime.now().month
    if month in (9, 10, 11):
        return "Deepavali/Festive season"
    elif month in (12, 1):
        return "New Year season"
    elif month in (4, 5):
        return "Summer season"
    elif month in (7, 8):
        return "Independence Day sales"
    return "the upcoming season"


# ─── Analysis functions (each wrapped as RunnableLambda) ─────────

def _price_rank_analysis(payload: dict) -> dict:
    """Module 1: Compute price rank and gap for each matched product."""
    matches  = payload["matches"]
    catalog  = payload["catalog"]
    threshold = payload["threshold"]

    sku_competitors: dict = defaultdict(dict)
    for m in matches:
        sku   = m.get("retailer_sku", "")
        comp  = m.get("competitor_name", "")
        price = float(m.get("competitor_price", 0))
        if sku and comp and price > 0:
            sku_competitors[sku][comp] = price

    results = {}
    for sku, comp_prices in sku_competitors.items():
        if sku not in catalog:
            continue
        product   = catalog[sku]
        my_price  = float(product.get("current_price", 0))
        if my_price == 0:
            continue

        all_prices    = sorted(list(comp_prices.values()) + [my_price])
        rank          = all_prices.index(my_price) + 1
        min_price     = min(comp_prices.values())
        avg_price     = sum(comp_prices.values()) / len(comp_prices)
        max_price     = max(comp_prices.values())
        gap_to_min    = my_price - min_price
        gap_pct_min   = gap_to_min / min_price if min_price > 0 else 0.0

        results[sku] = {
            "retailer_sku":          sku,
            "product_name":          product.get("name", sku),
            "retailer_price":        my_price,
            "competitor_prices":     comp_prices,
            "min_competitor_price":  min_price,
            "avg_competitor_price":  round(avg_price, 2),
            "max_competitor_price":  max_price,
            "price_rank":            rank,
            "total_competitors":     len(comp_prices),
            "price_gap_to_min":      round(gap_to_min, 2),
            "price_gap_pct_to_min":  round(gap_pct_min, 4),
            "trend":                 "stable",   # filled by next module
            "is_anomaly":            False,
            "anomaly_reason":        "",
        }

    payload["ranked"] = results
    return payload


def _trend_detector(payload: dict) -> dict:
    """Module 2: 7-day rolling trend per product via linear regression slope."""
    ranked      = payload["ranked"]
    retailer_id = payload["retailer_id"]

    for sku, data in ranked.items():
        cheapest_comp = min(data["competitor_prices"], key=data["competitor_prices"].get)
        history       = db.get_price_history(retailer_id, cheapest_comp, data["product_name"], days=7)
        data["trend"] = _compute_trend(history)

    payload["ranked"] = ranked
    return payload


def _anomaly_detector(payload: dict) -> dict:
    """Module 3: Z-score + IQR anomaly detection on price history."""
    ranked      = payload["ranked"]
    retailer_id = payload["retailer_id"]

    for sku, data in ranked.items():
        all_history = []
        for comp in data["competitor_prices"]:
            h = db.get_price_history(retailer_id, comp, data["product_name"], days=30)
            all_history.extend(h)
        all_history.sort(key=lambda x: x.get("scraped_at", ""))

        is_anomaly, reason = _detect_anomaly(data["min_competitor_price"], all_history)
        data["is_anomaly"]    = is_anomaly
        data["anomaly_reason"] = reason

    payload["ranked"] = ranked
    return payload


def _alert_builder(payload: dict) -> dict:
    """Module 4: Build Alert objects from analysis results."""
    ranked    = payload["ranked"]
    threshold = payload["threshold"]
    alerts    = []
    now       = datetime.now().isoformat()

    for sku, data in ranked.items():
        if data["is_anomaly"]:
            alerts.append({
                "type": "anomaly", "sku": sku, "product": data["product_name"],
                "message": f"⚠ Price anomaly: {data['anomaly_reason']}",
                "severity": "high", "at": now,
            })

        if data["price_gap_pct_to_min"] > threshold and data["price_rank"] > 1:
            cheapest = min(data["competitor_prices"], key=data["competitor_prices"].get)
            pct      = data["price_gap_pct_to_min"] * 100
            alerts.append({
                "type": "price_gap", "sku": sku, "product": data["product_name"],
                "message": (f"📊 {pct:.1f}% above cheapest ({cheapest} "
                            f"@ ₹{data['min_competitor_price']:.0f})"),
                "severity": "medium", "at": now,
            })

        if data["trend"] == "falling":
            season = _get_seasonality_signal()
            alerts.append({
                "type": "demand_forecast", "sku": sku, "product": data["product_name"],
                "message": f"📈 Prices dropping market-wide for {data['product_name']}. Demand is likely rising / category cooling — stock up before {season}!",
                "severity": "medium", "at": now,
            })

        if data["price_rank"] == 1 and data["retailer_price"] < data["min_competitor_price"]:
            gap = data["min_competitor_price"] - data["retailer_price"]
            alerts.append({
                "type": "marketing_opportunity", "sku": sku, "product": data["product_name"],
                "message": (f"🎉 You're the only store with {data['product_name']} below ₹{data['min_competitor_price']:,.0f} "
                            f"(You are ₹{gap:,.0f} cheaper than the nearest competitor). Use this in your marketing!"),
                "severity": "low", "at": now,
            })

    payload["alerts"] = alerts
    return payload


# ─── Compose into a LangChain RunnableLambda pipeline ────────────

# Each function wrapped as a Runnable, chained with |
_analysis_pipeline = (
    RunnableLambda(_price_rank_analysis)
    | RunnableLambda(_trend_detector)
    | RunnableLambda(_anomaly_detector)
    | RunnableLambda(_alert_builder)
)


# ─── LangGraph node ───────────────────────────────────────────────

def run_analyst_node(state: AgentState) -> dict:
    """
    LangGraph node: Analyst Agent.
    Runs the four-module analytics pipeline via RunnableLambda chain.
    Returns partial state update with analytics and alerts.
    """
    matches     = state["product_matches"]
    catalog     = {p["sku"]: p for p in state["retailer_profile"].catalog}
    retailer_id = state["retailer_id"]
    cycle_id    = state["cycle_id"]

    print(f"\n[Analyst] Running analytics pipeline on {len(matches)} matches...")

    if not matches:
        return {"analytics": [], "alerts": [], "analysis_complete": True, "current_node": "analyst"}

    # Run the composed RunnableLambda pipeline
    result = _analysis_pipeline.invoke({
        "matches":    matches,
        "catalog":    catalog,
        "threshold":  state["retailer_profile"].alert_threshold_pct,
        "retailer_id": retailer_id,
    })

    analytics_list = list(result["ranked"].values())
    alerts         = result["alerts"]

    # Persist to DB
    if analytics_list:
        db.save_analytics_results(retailer_id, cycle_id, analytics_list)


    cheapest = sum(1 for a in analytics_list if a["price_rank"] == 1)
    print(f"[Analyst] {len(analytics_list)} products analyzed | "
          f"{len(alerts)} alerts | cheapest on {cheapest}/{len(analytics_list)}")

    return {
        "analytics":        analytics_list,
        "alerts":           alerts,
        "analysis_complete": True,
        "current_node":     "analyst",
    }


# ─── Pure analytics helpers ───────────────────────────────────────

def _compute_trend(history: list) -> str:
    if len(history) < 3:
        return "stable"
    prices  = [float(r["price"]) for r in history]
    n       = len(prices)
    x_mean  = (n - 1) / 2
    y_mean  = statistics.mean(prices)
    num     = sum((i - x_mean) * (p - y_mean) for i, p in enumerate(prices))
    den     = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return "stable"
    slope = num / den / (y_mean or 1)
    if slope < -0.005:
        return "falling"
    if slope >  0.005:
        return "rising"
    return "stable"


def _detect_anomaly(current_price: float, history: list) -> tuple[bool, str]:
    if len(history) < 5:
        return False, ""
    historical = [float(r["price"]) for r in history[:-1]]
    if not historical:
        return False, ""
    mean  = statistics.mean(historical)
    try:
        stdev = statistics.stdev(historical)
    except statistics.StatisticsError:
        return False, ""
    if stdev == 0:
        return False, ""

    z = abs(current_price - mean) / stdev
    if z > 2.5:
        direction = "dropped" if current_price < mean else "spiked"
        return True, f"Price {direction} by {abs(current_price-mean)/mean*100:.1f}% (Z={z:.1f})"

    sorted_h = sorted(historical)
    q1 = sorted_h[len(sorted_h) // 4]
    q3 = sorted_h[3 * len(sorted_h) // 4]
    iqr = q3 - q1
    if iqr > 0:
        if current_price < q1 - 1.5 * iqr or current_price > q3 + 1.5 * iqr:
            side = "below" if current_price < q1 - 1.5 * iqr else "above"
            return True, f"Price {side} IQR fence (Q1=₹{q1:.0f}, Q3=₹{q3:.0f})"

    return False, ""