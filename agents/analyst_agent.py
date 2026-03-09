"""
RetailAgent — Analyst Agent
=============================
Runs four analysis modules on matched price data:
  1. Price Position Ranking     — where does the retailer rank vs. competitors
  2. Price Gap Analysis         — gap to min, avg, max competitor price
  3. Trend Detection            — 7-day rolling direction per competitor-product
  4. Anomaly Detection          — Z-score / IQR flagging of unusual price moves

All pure Python + Pandas + SciPy. No LLM calls in this layer.
Results written to LangGraph state and analytics_results DB table.
"""

import statistics
from datetime import datetime, timedelta
from collections import defaultdict

from core.state import AgentState, ProductAnalytics
from core import database as db


def _compute_trend(price_history: list) -> str:
    """
    Given a time-ordered list of {price, scraped_at} records,
    compute the 7-day price direction.
    Returns: 'rising' | 'falling' | 'stable'
    """
    if len(price_history) < 3:
        return "stable"

    prices = [float(r["price"]) for r in price_history]

    # Simple linear regression slope
    n = len(prices)
    x_mean = (n - 1) / 2
    y_mean = statistics.mean(prices)

    numerator   = sum((i - x_mean) * (p - y_mean) for i, p in enumerate(prices))
    denominator = sum((i - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return "stable"

    slope = numerator / denominator
    pct_slope = slope / y_mean if y_mean else 0

    if pct_slope < -0.005:
        return "falling"
    elif pct_slope > 0.005:
        return "rising"
    return "stable"


def _detect_anomaly(current_price: float, price_history: list) -> tuple[bool, str]:
    """
    Detect if current_price is anomalous using Z-score.
    Returns (is_anomaly, reason_string).
    """
    if len(price_history) < 5:
        return False, ""

    historical = [float(r["price"]) for r in price_history[:-1]]   # exclude current
    if not historical:
        return False, ""

    mean = statistics.mean(historical)
    try:
        stdev = statistics.stdev(historical)
    except statistics.StatisticsError:
        return False, ""

    if stdev == 0:
        return False, ""

    z_score = abs(current_price - mean) / stdev

    if z_score > 2.5:
        direction = "dropped" if current_price < mean else "spiked"
        change_pct = abs(current_price - mean) / mean * 100
        return True, (f"Price {direction} by {change_pct:.1f}% "
                      f"(Z-score={z_score:.1f}, hist.avg=₹{mean:.0f})")

    # IQR check
    sorted_prices = sorted(historical)
    q1 = sorted_prices[len(sorted_prices) // 4]
    q3 = sorted_prices[3 * len(sorted_prices) // 4]
    iqr = q3 - q1

    if iqr > 0:
        lower_fence = q1 - 1.5 * iqr
        upper_fence = q3 + 1.5 * iqr
        if current_price < lower_fence or current_price > upper_fence:
            direction = "below" if current_price < lower_fence else "above"
            return True, f"Price {direction} IQR fence (Q1={q1:.0f}, Q3={q3:.0f})"

    return False, ""


def run_analyst(state: AgentState, retailer_id: int) -> AgentState:
    """
    Computes analytics for all matched products.
    Updates state.analytics and triggers alerts.
    """
    print("\n[Analyst] Computing competitive intelligence...")

    matches   = state.product_matches
    catalog   = {p["sku"]: p for p in state.retailer_profile.catalog}
    threshold = state.retailer_profile.alert_threshold_pct
    cycle_id  = state.cycle_id

    if not matches:
        print("[Analyst] No product matches to analyze.")
        return state

    # Group matches: sku → {competitor: price}
    sku_competitors: dict = defaultdict(dict)
    for m in matches:
        sku  = m.get("retailer_sku", "")
        comp = m.get("competitor_name", "")
        price = float(m.get("competitor_price", 0))
        if sku and comp and price > 0:
            sku_competitors[sku][comp] = price

    analytics_list = []
    alerts = list(state.alerts)

    for sku, comp_prices in sku_competitors.items():
        if sku not in catalog:
            continue

        product      = catalog[sku]
        my_price     = float(product.get("current_price", 0))
        product_name = product.get("name", sku)

        if my_price == 0 or not comp_prices:
            continue

        # ── Price Position ──────────────────────────
        all_prices  = list(comp_prices.values()) + [my_price]
        sorted_prices = sorted(all_prices)
        rank        = sorted_prices.index(my_price) + 1
        n_comp      = len(comp_prices)

        min_price   = min(comp_prices.values())
        avg_price   = sum(comp_prices.values()) / len(comp_prices)
        max_price   = max(comp_prices.values())

        gap_to_min  = my_price - min_price
        gap_pct_min = gap_to_min / min_price if min_price > 0 else 0.0

        # ── Trend Detection ─────────────────────────
        # Use the cheapest competitor's history as market trend signal
        cheapest_comp = min(comp_prices, key=comp_prices.get)
        history = db.get_price_history(
            retailer_id, cheapest_comp, product_name, days=7
        )
        trend = _compute_trend(history)

        # ── Anomaly Detection ───────────────────────
        current_min = min_price
        all_history = []
        for comp in comp_prices:
            h = db.get_price_history(retailer_id, comp, product_name, days=30)
            all_history.extend(h)
        all_history.sort(key=lambda x: x.get("scraped_at", ""))

        is_anomaly, anomaly_reason = _detect_anomaly(current_min, all_history)

        # ── Build Analytics Object ──────────────────
        analytics = ProductAnalytics(
            retailer_sku          = sku,
            product_name          = product_name,
            retailer_price        = my_price,
            competitor_prices     = comp_prices,
            min_competitor_price  = min_price,
            avg_competitor_price  = round(avg_price, 2),
            max_competitor_price  = max_price,
            price_rank            = rank,
            total_competitors     = n_comp,
            price_gap_to_min      = round(gap_to_min, 2),
            price_gap_pct_to_min  = round(gap_pct_min, 4),
            trend                 = trend,
            is_anomaly            = is_anomaly,
            anomaly_reason        = anomaly_reason,
        )
        analytics_list.append(vars(analytics))

        # ── Alerts ──────────────────────────────────
        if is_anomaly:
            alerts.append({
                "type":    "anomaly",
                "sku":     sku,
                "product": product_name,
                "message": f"⚠ Price anomaly detected: {anomaly_reason}",
                "severity": "high",
                "at": datetime.now().isoformat(),
            })

        if gap_pct_min > threshold and rank > 1:
            cheapest_comp_name = min(comp_prices, key=comp_prices.get)
            pct_display = gap_pct_min * 100
            alerts.append({
                "type":    "price_gap",
                "sku":     sku,
                "product": product_name,
                "message": (f"📊 You are {pct_display:.1f}% above the cheapest competitor "
                            f"({cheapest_comp_name} @ ₹{min_price:.0f})"),
                "severity": "medium",
                "at": datetime.now().isoformat(),
            })

        if trend == "falling":
            alerts.append({
                "type":    "trend",
                "sku":     sku,
                "product": product_name,
                "message": f"📉 Market prices for {product_name} are trending downward.",
                "severity": "low",
                "at": datetime.now().isoformat(),
            })

    # ── Persist analytics to DB ──────────────────
    if analytics_list:
        conn = db.get_conn()
        conn.executemany("""
            INSERT INTO analytics_results
                (retailer_id, cycle_id, retailer_sku, product_name,
                 retailer_price, competitor_prices_json,
                 min_competitor_price, avg_competitor_price, max_competitor_price,
                 price_rank, total_competitors, price_gap_to_min, price_gap_pct_to_min,
                 trend, is_anomaly, anomaly_reason, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            (retailer_id, cycle_id,
             a["retailer_sku"], a["product_name"],
             a["retailer_price"], str(a["competitor_prices"]),
             a["min_competitor_price"], a["avg_competitor_price"], a["max_competitor_price"],
             a["price_rank"], a["total_competitors"],
             a["price_gap_to_min"], a["price_gap_pct_to_min"],
             a["trend"], int(a["is_anomaly"]), a["anomaly_reason"],
             datetime.now().isoformat())
            for a in analytics_list
        ])
        conn.commit()
        conn.close()

    state.analytics = analytics_list
    state.alerts    = alerts
    state.analysis_complete = True

    print(f"[Analyst] Done. {len(analytics_list)} products analyzed | "
          f"{len(alerts)} alerts raised")

    # Summary statistics
    cheapest_count = sum(1 for a in analytics_list if a["price_rank"] == 1)
    above_count    = sum(1 for a in analytics_list if a["price_rank"] > 1)
    anomalies      = sum(1 for a in analytics_list if a["is_anomaly"])

    print(f"  Cheapest on: {cheapest_count}/{len(analytics_list)} products")
    print(f"  Above market: {above_count}/{len(analytics_list)} products")
    print(f"  Anomalies: {anomalies}")

    return state