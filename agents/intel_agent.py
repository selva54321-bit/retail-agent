"""
RetailAgent — Intel Agent (Competitive Intelligence)
======================================================
Runs after Catalog Spy. Analyses the 30-cycle price history to derive
deeper strategic insights about each competitor's behaviour.

Four intelligence modules (RunnableLambda pipeline):

1. Strategy Classifier
   Compares each competitor's prices to the market average over 30 cycles.
   Labels each as one of:
     price_leader       — consistently cheapest (>50% of time)
     discount_aggressor — frequent steep drops (flash sales >10%)
     price_follower     — tracks average, rarely leads
     premium_anchor     — consistently above average

2. Flash Sale Detector
   If a competitor dropped a product price by >12% vs their own last-cycle
   price, flag it as a potential flash sale (short-term, don't match it).

3. Price Pattern Analyser
   Detects day-of-week patterns:
   "Poorvika drops prices on Fridays" — uses price_history timestamps.

4. Growth Opportunity Finder
   Combines fast_movers (from catalog_spy) + new_arrivals to suggest
   which products the user should consider stocking.

LangChain pattern: RunnableLambda pipeline composed with |
Persists results to market_intelligence DB table.
"""

import json
from collections import defaultdict
from datetime    import datetime

from langchain_core.runnables import RunnableLambda

from core.state import AgentState
from core       import database as db


# ─────────────────────────────────────────────────────────────────
#  MODULE 1 — Competitor Strategy Classifier
# ─────────────────────────────────────────────────────────────────

def _strategy_classifier(payload: dict) -> dict:
    """
    Classify each competitor's pricing strategy.

    Benchmark: for each product (by catalog_sku), compute the MEDIAN price
    across all competitors THIS cycle. Then compare each competitor's price
    to that median. This avoids the product-name mismatch problem completely.

    Labels:
      price_leader       — avg >5% BELOW median (cheapest)
      premium_anchor     — avg >5% ABOVE median (most expensive)
      discount_aggressor — frequent price changes AND sometimes below median
      price_follower     — within ±5% of median (tracks market)
      unknown            — not enough data
    """
    retailer_id    = payload["retailer_id"]
    scraped        = payload["scraped_records"]

    strategies: dict[str, dict] = {}

    # ── Build per-SKU price map from THIS cycle's scraped records ──
    # {catalog_sku → {competitor → [prices]}}
    sku_comp_prices: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in scraped:
        sku  = r.get("catalog_sku", "")
        comp = r.get("competitor_name", "")
        price = r.get("price", 0)
        if sku and comp and price:
            sku_comp_prices[sku][comp].append(float(price))

    if not sku_comp_prices:
        payload["strategies"] = strategies
        return payload

    # ── Compute per-SKU median across all competitors ──────────────
    sku_medians: dict[str, float] = {}
    for sku, comp_map in sku_comp_prices.items():
        all_prices = [p for prices in comp_map.values() for p in prices]
        if all_prices:
            sorted_p = sorted(all_prices)
            n = len(sorted_p)
            sku_medians[sku] = (sorted_p[n//2] + sorted_p[(n-1)//2]) / 2

    # ── Score each competitor against the median ───────────────────
    all_competitors = {r.get("competitor_name","") for r in scraped if r.get("competitor_name")}

    for comp in all_competitors:
        if not comp:
            continue

        gap_pcts     = []
        change_count = 0

        for sku, comp_map in sku_comp_prices.items():
            if comp not in comp_map:
                continue
            median = sku_medians.get(sku)
            if not median:
                continue

            comp_avg = sum(comp_map[comp]) / len(comp_map[comp])
            gap_pct  = (comp_avg - median) / median
            gap_pcts.append(gap_pct)

        # Also pull 30-day history to count price changes
        history = db.get_price_history_for_intel(retailer_id, comp, days=30)
        by_product: dict[str, list] = defaultdict(list)
        for row in history:
            by_product[row["product_name_raw"]].append(float(row.get("price", 0)))
        for pname, prices in by_product.items():
            for i in range(1, len(prices)):
                if prices[i-1] > 0 and abs(prices[i] - prices[i-1]) / prices[i-1] > 0.01:
                    change_count += 1

        if not gap_pcts:
            label    = "unknown"
            avg_gap  = 0.0
        else:
            avg_gap  = sum(gap_pcts) / len(gap_pcts)
            below    = sum(1 for g in gap_pcts if g < -0.05)
            above    = sum(1 for g in gap_pcts if g > 0.05)
            n        = len(gap_pcts)

            if below / n >= 0.5:
                label = "price_leader"
            elif above / n >= 0.5:
                label = "premium_anchor"
            elif change_count >= n * 2:
                label = "discount_aggressor"
            else:
                label = "price_follower"

        strategies[comp] = {
            "competitor_name":    comp,
            "strategy_label":     label,
            "avg_price_gap_pct":  round(avg_gap * 100, 2),
            "price_change_count": change_count,
            "flash_sales_count":  0,
            "insights": {
                "skus_analysed":  len(gap_pcts),
                "avg_gap_pct":    round(avg_gap * 100, 2),
            },
        }

    payload["strategies"] = strategies
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 2 — Flash Sale Detector
# ─────────────────────────────────────────────────────────────────

def _flash_sale_detector(payload: dict) -> dict:
    """
    Detect flash sales: competitor dropped a product >12% vs their own
    previous cycle price. These are temporary — don't recommend matching.
    """
    retailer_id  = payload["retailer_id"]
    scraped      = payload["scraped_records"]
    strategies   = payload["strategies"]
    flash_events = []

    # Group current scraped prices by competitor+product
    current: dict[str, dict[str, float]] = defaultdict(dict)
    for r in scraped:
        comp  = r.get("competitor_name", "")
        pname = r.get("product_name_raw", "")[:100]
        price = r.get("price", 0)
        if comp and pname and price:
            current[comp][pname] = float(price)

    for comp, products in current.items():
        history = db.get_price_history_for_intel(retailer_id, comp, days=3)

        # Build previous cycle prices (exclude today)
        prev: dict[str, float] = {}
        for row in history:
            pname = row.get("product_name_raw", "")[:100]
            price = float(row.get("price", 0))
            # Keep the most recent previous price (not today's)
            scraped_at = row.get("scraped_at", "")
            today = datetime.now().strftime("%Y-%m-%d")
            if scraped_at[:10] < today and pname and price:
                prev[pname] = price

        for pname, cur_price in products.items():
            if pname not in prev:
                continue
            prev_price = prev[pname]
            if prev_price <= 0:
                continue
            drop_pct = (prev_price - cur_price) / prev_price

            if drop_pct >= 0.12:   # 12% or more drop
                event = {
                    "competitor": comp,
                    "product":    pname,
                    "prev_price": prev_price,
                    "cur_price":  cur_price,
                    "drop_pct":   round(drop_pct * 100, 1),
                }
                flash_events.append(event)
                if comp in strategies:
                    strategies[comp]["flash_sales_count"] += 1
                    strategies[comp]["insights"]["latest_flash_sale"] = event

    payload["flash_events"] = flash_events
    payload["strategies"]   = strategies
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 3 — Price Drop Pattern Analyser
#  Per (competitor × SKU): detect day-of-week pattern, drop magnitude,
#  consistency, and predict the next likely drop date.
# ─────────────────────────────────────────────────────────────────

DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]


def _price_pattern_analyser(payload: dict) -> dict:
    """
    For each (competitor × catalog_sku) pair, analyse the full price history to:
      1. Find which day of week drops happen most
      2. Compute average and max drop percentage
      3. Score consistency (how reliably it happens every N days)
      4. Predict the next drop date
      5. Persist patterns to price_drop_patterns DB

    Requires at least 4 price observations to compute a pattern.
    """
    retailer_id    = payload["retailer_id"]
    strategies     = payload["strategies"]
    scraped        = payload["scraped_records"]

    # Get unique (competitor, sku) pairs from this cycle
    comp_sku_pairs: set[tuple] = set()
    for r in scraped:
        comp = r.get("competitor_name", "")
        sku  = r.get("catalog_sku", "")
        if comp and sku:
            comp_sku_pairs.add((comp, sku))

    all_patterns: list[dict] = []

    for comp, sku in comp_sku_pairs:
        history = db.get_price_history_by_sku(retailer_id, comp, sku, days=60)
        if len(history) < 4:
            continue

        # Sort by time
        history.sort(key=lambda x: x.get("scraped_at", ""))

        prices   = [float(r["price"]) for r in history if r.get("price")]
        dates    = [r.get("scraped_at", "")[:10] for r in history]
        pname    = history[-1].get("product_name_raw", "")[:80]

        if len(prices) < 4:
            continue

        # ── Detect all price drops ────────────────────────────────
        drop_events: list[dict] = []
        for i in range(1, len(prices)):
            if prices[i-1] <= 0:
                continue
            change_pct = (prices[i-1] - prices[i]) / prices[i-1]
            if change_pct >= 0.01:    # 1%+ drop
                try:
                    dt      = datetime.strptime(dates[i], "%Y-%m-%d")
                    drop_events.append({
                        "date":      dates[i],
                        "weekday":   dt.weekday(),
                        "drop_pct":  round(change_pct * 100, 2),
                        "from_price": prices[i-1],
                        "to_price":  prices[i],
                    })
                except Exception:
                    continue

        if not drop_events:
            continue

        total_obs = len(prices)
        drop_count = len(drop_events)

        # ── Day-of-week distribution ──────────────────────────────
        drops_by_day: dict[int, list] = defaultdict(list)
        for ev in drop_events:
            drops_by_day[ev["weekday"]].append(ev["drop_pct"])

        peak_day      = max(drops_by_day, key=lambda d: len(drops_by_day[d]))
        peak_day_count = len(drops_by_day[peak_day])
        day_concentration = peak_day_count / drop_count   # 0–1

        # ── Drop magnitude ────────────────────────────────────────
        all_drop_pcts = [ev["drop_pct"] for ev in drop_events]
        avg_drop_pct  = sum(all_drop_pcts) / len(all_drop_pcts)
        max_drop_pct  = max(all_drop_pcts)

        # ── Consistency score ─────────────────────────────────────
        # How many of the peak_day occurrences in the date range had a drop?
        # Estimate weeks covered
        try:
            first_dt = datetime.strptime(dates[0], "%Y-%m-%d")
            last_dt  = datetime.strptime(dates[-1], "%Y-%m-%d")
            weeks_covered = max(1, (last_dt - first_dt).days / 7)
        except Exception:
            weeks_covered = 1

        # Expected occurrences of peak_day in observed window
        expected_peak_days = max(1, weeks_covered)
        # How many actually had drops?
        consistency = min(1.0, peak_day_count / expected_peak_days)

        # ── Predict next drop date ────────────────────────────────
        last_drop_date  = drop_events[-1]["date"]
        next_predicted  = _predict_next_drop(last_drop_date, peak_day,
                                             avg_interval_days=_avg_interval(drop_events))

        # ── Build pattern summary ─────────────────────────────────
        pattern = {
            "competitor_name":    comp,
            "catalog_sku":        sku,
            "product_name":       pname,
            "peak_day_of_week":   peak_day,
            "avg_drop_pct":       round(avg_drop_pct, 2),
            "max_drop_pct":       round(max_drop_pct, 2),
            "drop_count":         drop_count,
            "total_observations": total_obs,
            "consistency_score":  round(consistency, 3),
            "last_drop_date":     last_drop_date,
            "next_predicted_date": next_predicted,
            "day_concentration":  round(day_concentration, 3),
        }
        all_patterns.append(pattern)

        # Persist to DB
        db.upsert_price_drop_pattern(retailer_id, pattern)

        # ── Build human-readable insight ──────────────────────────
        if consistency >= 0.3 and drop_count >= 2:
            day_name = DAY_NAMES[peak_day]
            insight  = (f"Drops {pname[:35]} on {day_name}s "
                        f"~{avg_drop_pct:.1f}% avg "
                        f"(next: {next_predicted})")

            if comp in strategies:
                existing = strategies[comp]["insights"].get("price_patterns", [])
                existing.append(insight)
                strategies[comp]["insights"]["price_patterns"] = existing[:3]

                # Upgrade the legacy single string too (for backward compat)
                if len(existing) == 1:
                    strategies[comp]["insights"]["price_pattern"] = insight

    payload["strategies"]     = strategies
    payload["drop_patterns"]  = all_patterns
    return payload


def _avg_interval(drop_events: list[dict]) -> float:
    """Average days between consecutive drop events."""
    if len(drop_events) < 2:
        return 7.0   # default guess: weekly
    intervals = []
    for i in range(1, len(drop_events)):
        try:
            d1 = datetime.strptime(drop_events[i-1]["date"], "%Y-%m-%d")
            d2 = datetime.strptime(drop_events[i]["date"],   "%Y-%m-%d")
            intervals.append((d2 - d1).days)
        except Exception:
            continue
    return sum(intervals) / len(intervals) if intervals else 7.0


def _predict_next_drop(last_drop_date: str,
                       peak_weekday: int,
                       avg_interval_days: float) -> str:
    """
    Predict the next likely drop date.
    Strategy: start from (last_drop + avg_interval), then advance to
    the nearest occurrence of peak_weekday.
    """
    from datetime import timedelta
    try:
        last_dt   = datetime.strptime(last_drop_date, "%Y-%m-%d")
        candidate = last_dt + timedelta(days=max(1, int(avg_interval_days)))

        # Advance to the nearest peak_weekday at or after candidate
        days_ahead = (peak_weekday - candidate.weekday()) % 7
        next_dt    = candidate + timedelta(days=days_ahead)

        # Never predict in the past
        today = datetime.now()
        if next_dt < today:
            next_dt = today + timedelta(days=(peak_weekday - today.weekday()) % 7 or 7)

        return next_dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────
#  MODULE 4 — Growth Opportunity Finder
# ─────────────────────────────────────────────────────────────────

def _growth_opportunity_finder(payload: dict) -> dict:
    """
    Combines fast_movers + new_arrivals to suggest growth opportunities.
    Fast movers (frequent stock-outs) = products in high demand you should stock more of.
    New arrivals at competitors = products you should consider adding to your catalog.
    """
    fast_movers    = payload.get("fast_movers", [])
    catalog_alerts = payload.get("catalog_alerts", [])
    new_arrivals   = [a["data"] for a in catalog_alerts
                      if a.get("type") == "new_arrival"]

    opportunities = []

    # Fast movers across competitors = high demand
    seen_products = set()
    for fm in fast_movers[:5]:
        key = fm["product"][:40].lower()
        if key not in seen_products:
            seen_products.add(key)
            opportunities.append({
                "type":       "stock_up",
                "product":    fm["product"],
                "competitor": fm["competitor"],
                "reason":     f"Out of stock {fm['times_out']}x — high demand signal",
                "priority":   "high" if fm["stockout_rate"] > 0.4 else "medium",
            })

    # New arrivals competitors have = products to consider stocking
    for na in new_arrivals[:5]:
        key = na["product"][:40].lower()
        if key not in seen_products:
            seen_products.add(key)
            opportunities.append({
                "type":       "add_to_catalog",
                "product":    na["product"],
                "competitor": na["competitor"],
                "price":      na.get("price", 0),
                "reason":     f"Competitor {na['competitor']} is selling this — you don't carry it",
                "priority":   "medium",
            })

    payload["opportunities"] = opportunities
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 5 — Price Window Finder (Your Unique Price Advantage)
#  Find products where the retailer IS the cheapest — marketing signal.
# ─────────────────────────────────────────────────────────────────

def _price_window_finder(payload: dict) -> dict:
    """
    For each product where price_rank == 1 (you're cheapest):
      - How much cheaper vs next competitor?
      - How many consecutive cycles have you held this position?
      - Build a ready-to-use marketing message.
    """
    analytics   = payload.get("analytics", [])
    retailer_id = payload["retailer_id"]

    price_windows = []

    for a in analytics:
        if a.get("price_rank") != 1:
            continue

        sku            = a.get("retailer_sku", "")
        my_price       = a.get("retailer_price", 0)
        comp_prices    = a.get("competitor_prices", {})
        product_name   = a.get("product_name", "")

        if not comp_prices or not my_price:
            continue

        next_cheapest_comp  = min(comp_prices, key=comp_prices.get)
        next_cheapest_price = comp_prices[next_cheapest_comp]
        gap_abs = next_cheapest_price - my_price
        gap_pct = gap_abs / next_cheapest_price * 100 if next_cheapest_price else 0

        # Count consecutive cycles at rank 1
        try:
            rows = db.get_recent_price_rank_history(retailer_id, sku, limit=10)
            streak = 0
            for row in rows:
                if row["price_rank"] == 1:
                    streak += 1
                else:
                    break
        except Exception:
            streak = 1

        msg = (f"{product_name[:50]} — you're ₹{gap_abs:,.0f} cheaper than "
               f"{next_cheapest_comp} (₹{my_price:,.0f} vs ₹{next_cheapest_price:,.0f})")
        if streak >= 3:
            msg += f" — cheapest for {streak} cycles in a row"

        price_windows.append({
            "sku":                  sku,
            "product_name":         product_name,
            "your_price":           my_price,
            "next_cheapest_comp":   next_cheapest_comp,
            "next_cheapest_price":  next_cheapest_price,
            "gap_abs":              round(gap_abs, 0),
            "gap_pct":              round(gap_pct, 1),
            "consecutive_cheapest": streak,
            "marketing_message":    msg,
        })

    price_windows.sort(key=lambda x: x["gap_pct"], reverse=True)
    payload["price_windows"] = price_windows
    return payload


# ─────────────────────────────────────────────────────────────────
#  MODULE 6 — Demand Forecaster
#
#  Three signals combined per catalog SKU:
#
#  Signal 1 — Price Drop Velocity
#    If competitors dropped prices repeatedly in the last 14 days,
#    one of two things is happening:
#      a) Demand RISING  → competition intensifying, customers active
#      b) Demand FALLING → trying to clear slow-moving stock
#    We disambiguate using stockout rate: rising stockouts + drops = (a)
#
#  Signal 2 — Stockout Frequency (from competitor_catalog)
#    Frequent stock-outs at competitors = product flying off shelves.
#    This is the strongest demand signal available.
#
#  Signal 3 — Indian Retail Seasonality Calendar
#    Hardcoded upcoming sale events with typical demand surge windows.
#    If an event is <30 days away, flag it + recommend stocking up.
#
#  Output: demand_signal ∈ {rising, falling, stable, unknown}
#          + a plain-English recommendation for the retailer
# ─────────────────────────────────────────────────────────────────

# Indian retail calendar — (month, day, name, demand_categories, surge_days_before)
INDIAN_SALE_EVENTS = [
    (1,  26, "Republic Day Sale",    ["electronics","tv","mobile"],  7),
    (2,  14, "Valentine's Day Sale", ["mobile","accessories"],        5),
    (3,  21, "Holi Sale",            ["electronics","tv","mobile"],   5),
    (8,  15, "Independence Day Sale",["electronics","tv","mobile"],   7),
    (10,  1, "Navratri/Dussehra",    ["tv","electronics","appliance"],14),
    (11,  1, "Deepavali Season",     ["tv","electronics","appliance"],21),
    (11, 11, "Singles Day Sale",     ["electronics","mobile","tv"],   5),
    (11, 23, "Black Friday / Big Billion", ["tv","electronics"],      7),
    (12, 25, "Christmas Sale",       ["tv","electronics","mobile"],  10),
    (1,  14, "Pongal / Makar Sankranti", ["tv","appliance"],         10),
]


def _days_to_next_event(category: str) -> tuple[str, int]:
    """
    Return (event_name, days_until) for the nearest upcoming sale event
    relevant to this category. Returns ('', 9999) if none within 60 days.
    """
    from datetime import timedelta
    today = datetime.now()
    cat   = category.lower()

    best_name = ""
    best_days = 9999

    for month, day, name, categories, surge_days in INDIAN_SALE_EVENTS:
        # Build event date for this year and next year
        for year_offset in (0, 1):
            try:
                event_dt = datetime(today.year + year_offset, month, day)
            except ValueError:
                continue

            days_until = (event_dt - today).days
            if days_until < 0:
                continue

            # Check category relevance
            relevant = any(c in cat for c in categories)
            if not relevant:
                continue

            # Within the surge window?
            if days_until <= surge_days + 30 and days_until < best_days:
                best_days = days_until
                best_name = name

    return best_name, best_days if best_name else ("", 9999)


def _demand_forecaster(payload: dict) -> dict:
    """
    Module 5: Demand Forecaster.
    Runs per catalog SKU, combining price velocity, stockout rate,
    and seasonal calendar to produce a demand signal + recommendation.
    """
    retailer_id = payload["retailer_id"]
    catalog     = payload.get("catalog", [])
    category    = payload.get("category", "electronics")
    drop_patterns = payload.get("drop_patterns", [])

    if not catalog:
        payload["demand_forecasts"] = []
        return payload

    forecasts = []

    for product in catalog:
        sku   = product.get("sku", "")
        pname = product.get("name", "")
        if not sku:
            continue

        # ── Signal 1: Price drop velocity ────────────────────────
        velocity_rows = db.get_price_velocity(retailer_id, sku, days=14)
        comp_prices: dict[str, list] = defaultdict(list)
        for r in velocity_rows:
            comp_prices[r["competitor_name"]].append(float(r["price"]))

        total_drops    = 0
        drop_velocity  = 0.0    # avg pct drop across competitors
        drop_comps     = 0

        for comp, prices in comp_prices.items():
            if len(prices) < 2:
                continue
            comp_drops = sum(
                (prices[i-1] - prices[i]) / prices[i-1]
                for i in range(1, len(prices))
                if prices[i-1] > 0 and prices[i] < prices[i-1]
            )
            if comp_drops > 0:
                drop_velocity += comp_drops / (len(prices) - 1)
                drop_comps    += 1
                total_drops   += 1

        if drop_comps > 0:
            drop_velocity = (drop_velocity / drop_comps) * 100   # to percent

        # ── Signal 2: Stockout rate from competitor_catalog ───────
        all_catalog_rows = db.get_competitor_catalog(retailer_id)
        sku_rows = [r for r in all_catalog_rows
                    if r.get("catalog_sku") == sku and r.get("times_seen", 0) >= 2]

        if sku_rows:
            total_seen    = sum(r["times_seen"] for r in sku_rows)
            total_oos     = sum(r["times_out_of_stock"] for r in sku_rows)
            stockout_rate = total_oos / total_seen if total_seen > 0 else 0.0
        else:
            stockout_rate = 0.0

        # ── Signal 3: Seasonal calendar ───────────────────────────
        event_name, days_to_event = _days_to_next_event(category)

        # ── Combine signals ───────────────────────────────────────
        #
        # demand RISING when:
        #   - stockout_rate >= 0.25 (out of stock 1 in 4 times) OR
        #   - drop_velocity >= 3% AND total_drops >= 2 competitors AND
        #     stockout_rate > 0.05 (price drops + some scarcity = hot product)
        #   - OR event within 21 days in relevant category
        #
        # demand FALLING when:
        #   - drop_velocity >= 5% AND stockout_rate < 0.05
        #     (aggressive drops but no stock-outs = trying to push slow stock)
        #   - consistent drops every cycle with no restocking signal
        #
        # demand STABLE otherwise

        rising_score  = 0
        falling_score = 0

        if stockout_rate >= 0.25:
            rising_score += 3
        elif stockout_rate >= 0.10:
            rising_score += 1

        if drop_velocity >= 3.0 and total_drops >= 2:
            if stockout_rate >= 0.05:
                rising_score += 2   # drops + some scarcity = rising demand
            else:
                falling_score += 2  # drops + no scarcity = clearing slow stock

        if event_name and days_to_event <= 21:
            rising_score += 3
        elif event_name and days_to_event <= 45:
            rising_score += 1

        if drop_velocity >= 8.0 and stockout_rate < 0.03:
            falling_score += 2   # very aggressive drops, no stock pressure

        # Determine signal
        if rising_score >= 3:
            signal     = "rising"
            confidence = "high" if rising_score >= 5 else "medium"
        elif falling_score >= 3:
            signal     = "falling"
            confidence = "high" if falling_score >= 5 else "medium"
        elif rising_score >= 1 or falling_score >= 1:
            signal     = "rising" if rising_score > falling_score else "falling"
            confidence = "low"
        else:
            signal     = "stable"
            confidence = "low"

        # ── Build recommendation ──────────────────────────────────
        rec_parts = []
        if signal == "rising":
            rec_parts.append(f"Demand for {pname[:35]} is RISING")
            if stockout_rate >= 0.25:
                rec_parts.append(
                    f"— competitors running out of stock ({stockout_rate:.0%} OOS rate)"
                )
            if drop_velocity >= 3.0 and total_drops >= 2:
                rec_parts.append(
                    f"— {total_drops} competitor(s) dropping prices ({drop_velocity:.1f}% avg)"
                )
            if event_name and days_to_event <= 45:
                rec_parts.append(
                    f"— {event_name} is {days_to_event} days away"
                )
            rec_parts.append("→ Stock up now, consider holding your price")

        elif signal == "falling":
            rec_parts.append(f"Demand for {pname[:35]} appears COOLING")
            if drop_velocity >= 5.0:
                rec_parts.append(
                    f"— {total_drops} competitor(s) aggressively cutting prices "
                    f"({drop_velocity:.1f}% avg) with no stock pressure"
                )
            rec_parts.append("→ Review your inventory levels, avoid overstocking")

        else:
            rec_parts.append(
                f"{pname[:35]}: demand is STABLE — maintain current stock levels"
            )
            if event_name and days_to_event <= 60:
                rec_parts.append(
                    f"(Note: {event_name} in {days_to_event} days — monitor closely)"
                )

        recommendation = " ".join(rec_parts)

        forecasts.append({
            "catalog_sku":           sku,
            "product_name":          pname,
            "demand_signal":         signal,
            "confidence":            confidence,
            "price_drop_velocity":   round(drop_velocity, 2),
            "stockout_rate":         round(stockout_rate, 3),
            "competitor_drop_count": total_drops,
            "seasonal_event":        event_name,
            "days_to_event":         days_to_event if event_name else 0,
            "recommendation":        recommendation,
        })

    payload["demand_forecasts"] = forecasts
    return payload


# ─── Compose RunnableLambda pipeline ─────────────────────────────

_intel_pipeline = (
    RunnableLambda(_strategy_classifier)
    | RunnableLambda(_flash_sale_detector)
    | RunnableLambda(_price_pattern_analyser)
    | RunnableLambda(_growth_opportunity_finder)
    | RunnableLambda(_price_window_finder)
    | RunnableLambda(_demand_forecaster)
)


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_intel_node(state: AgentState) -> dict:
    """
    LangGraph node: Intel Agent.
    Runs 5-module competitive intelligence pipeline:
      1. Strategy Classifier
      2. Flash Sale Detector
      3. Price Drop Pattern Analyser
      4. Growth Opportunity Finder
      5. Demand Forecaster
    """
    scraped        = state["scraped_records"]
    analytics      = state["analytics"]
    retailer_id    = state["retailer_id"]
    cycle_id       = state["cycle_id"]
    catalog_alerts = state.get("catalog_alerts", [])
    catalog        = state["retailer_profile"].catalog
    category       = state["retailer_profile"].category

    print(f"\n[Intel] Running competitive intelligence analysis...")

    if not scraped:
        return {"intel_insights": {}, "current_node": "intel"}

    result = _intel_pipeline.invoke({
        "scraped_records": scraped,
        "analytics":       analytics,
        "retailer_id":     retailer_id,
        "fast_movers":     state.get("intel_insights", {}).get("fast_movers", []),
        "catalog_alerts":  catalog_alerts,
        "catalog":         catalog,
        "category":        category,
    })

    strategies       = result.get("strategies", {})
    flash_events     = result.get("flash_events", [])
    opportunities    = result.get("opportunities", [])
    drop_patterns    = result.get("drop_patterns", [])
    demand_forecasts = result.get("demand_forecasts", [])
    price_windows    = result.get("price_windows", [])

    # Persist to DB
    db.save_market_intelligence(retailer_id, cycle_id, list(strategies.values()))
    db.save_demand_forecasts(retailer_id, cycle_id, demand_forecasts)

    # ── Print: strategies ─────────────────────────────────────────
    print(f"[Intel] Competitor strategies:")
    for comp, data in strategies.items():
        patterns = data["insights"].get("price_patterns",
                   [data["insights"].get("price_pattern", "")])
        label    = data["strategy_label"]
        gap      = data["avg_price_gap_pct"]
        sign     = "+" if gap >= 0 else ""
        pat_str  = " | " + patterns[0] if patterns and patterns[0] else ""
        print(f"  {comp:25} → {label:22} (avg {sign}{gap:.1f}% vs market){pat_str}")

    # ── Print: drop patterns ──────────────────────────────────────
    if drop_patterns:
        actionable = [p for p in drop_patterns if p["consistency_score"] >= 0.3]
        print(f"[Intel] 📉 {len(drop_patterns)} price drop pattern(s) "
              f"({len(actionable)} actionable):")
        for p in sorted(drop_patterns, key=lambda x: x["consistency_score"], reverse=True)[:5]:
            if p["consistency_score"] < 0.2:
                continue
            print(f"  {p['competitor_name']:20} | {p['product_name'][:35]:35} | "
                  f"{DAY_NAMES[p['peak_day_of_week']]}s "
                  f"~{p['avg_drop_pct']:.1f}% | "
                  f"consistency={p['consistency_score']:.0%} | "
                  f"next≈{p['next_predicted_date']}")

    # ── Print: flash sales ────────────────────────────────────────
    if flash_events:
        print(f"[Intel] ⚡ {len(flash_events)} flash sale(s) detected:")
        for fe in flash_events[:3]:
            print(f"  {fe['competitor']}: {fe['product'][:40]} "
                  f"dropped {fe['drop_pct']}% "
                  f"(₹{fe['prev_price']:,.0f} → ₹{fe['cur_price']:,.0f})")

    # ── Print: price windows ──────────────────────────────────────
    if price_windows:
        print(f"[Intel] 🏆 {len(price_windows)} price advantage(s) — use in marketing:")
        for pw in price_windows:
            print(f"  {pw['marketing_message']}")

    # ── Print: demand forecasts ───────────────────────────────────
    if demand_forecasts:
        print(f"[Intel] 📊 Demand forecasts:")
        signal_icons = {"rising": "🔺", "falling": "🔻", "stable": "➡", "unknown": "❓"}
        for f in demand_forecasts:
            icon = signal_icons.get(f["demand_signal"], "❓")
            conf = f"[{f['confidence']}]"
            print(f"  {icon} {conf:8} {f['product_name'][:45]}")
            print(f"           {f['recommendation'][:100]}")

    # ── Print: growth opportunities ───────────────────────────────
    if opportunities:
        print(f"[Intel] 💡 {len(opportunities)} growth opportunity(ies):")
        for op in opportunities[:3]:
            print(f"  [{op['priority'].upper()}] {op['type']}: {op['product'][:45]}")

    # ── Merge insights ────────────────────────────────────────────
    existing_insights = state.get("intel_insights", {})
    merged_insights   = {
        **existing_insights,
        "competitor_strategies": {
            comp: data["strategy_label"]
            for comp, data in strategies.items()
        },
        "strategy_details":    strategies,
        "flash_sales":         flash_events,
        "opportunities":       opportunities,
        "drop_patterns":       drop_patterns,
        "demand_forecasts":    demand_forecasts,
        "price_windows":       price_windows,
    }

    return {
        "intel_insights": merged_insights,
        "current_node":   "intel",
    }