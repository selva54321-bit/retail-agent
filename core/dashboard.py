"""
RetailAgent — Terminal Dashboard
===================================
A rich terminal UI that displays the competitive pricing results
without needing Streamlit or any web server.

Shows:
  - Price comparison table (your price vs each competitor)
  - Recommendations queue
  - Alerts
  - Morning briefing
  - Cycle history
"""

import os
import sys
from datetime import datetime
from core import database as db
from core.state import AgentState


# Terminal color codes
class C:
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"


def _header(title: str):
    width = 70
    print(f"\n{C.BLUE}{'═'*width}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {title}{C.RESET}")
    print(f"{C.BLUE}{'─'*width}{C.RESET}")


def _row(label: str, value: str, color: str = C.WHITE):
    print(f"  {C.DIM}{label:<28}{C.RESET}{color}{value}{C.RESET}")


def show_price_comparison(state: AgentState):
    """Show a live price comparison table."""
    analytics = state["analytics"]
    if not analytics:
        print(f"  {C.YELLOW}No price data available yet.{C.RESET}")
        return

    _header("📊 PRICE COMPARISON TABLE")

    # Collect all competitor names
    all_comps = set()
    for a in analytics:
        all_comps.update(a.get("competitor_prices", {}).keys())
    all_comps = sorted(all_comps)

    # Header row
    col_w = 14
    name_w = 30
    header = f"  {'Product':<{name_w}} {'Yours':>{col_w}}"
    for c in all_comps[:4]:   # cap at 4 competitors for display width
        short = c[:12]
        header += f" {short:>{col_w}}"
    header += f" {'Rank':>6} {'Gap%':>6}"
    print(f"{C.BOLD}{header}{C.RESET}")
    print(f"  {'─'*(name_w + (len(all_comps[:4])+1)*col_w + 15)}")

    # Data rows
    for a in sorted(analytics, key=lambda x: x.get("price_rank", 99)):
        name      = a["product_name"][:name_w-2]
        my_price  = a["retailer_price"]
        rank      = a["price_rank"]
        gap_pct   = a["price_gap_pct_to_min"] * 100

        # Color-code my price by rank
        if rank == 1:
            price_color = C.GREEN
        elif rank <= 2:
            price_color = C.YELLOW
        else:
            price_color = C.RED

        row = f"  {name:<{name_w}} {price_color}₹{my_price:>11,.0f}{C.RESET}"
        for comp in all_comps[:4]:
            comp_price = a.get("competitor_prices", {}).get(comp)
            if comp_price:
                # Highlight if they're cheaper
                c_color = C.RED if comp_price < my_price else C.DIM
                row += f" {c_color}₹{comp_price:>11,.0f}{C.RESET}"
            else:
                row += f" {'—':>{col_w}}"

        rank_color = C.GREEN if rank == 1 else (C.YELLOW if rank == 2 else C.RED)
        row += f" {rank_color}{rank:>6}{C.RESET}"

        gap_color = C.GREEN if gap_pct < 2 else (C.YELLOW if gap_pct < 8 else C.RED)
        row += f" {gap_color}{gap_pct:>+5.1f}%{C.RESET}"

        # Trend indicator
        trend = a.get("trend", "stable")
        trend_sym = {"rising": "↑", "falling": "↓", "stable": "─"}.get(trend, "─")
        trend_col = {"rising": C.GREEN, "falling": C.RED, "stable": C.DIM}.get(trend, C.DIM)
        row += f" {trend_col}{trend_sym}{C.RESET}"

        if a.get("is_anomaly"):
            row += f" {C.YELLOW}⚠{C.RESET}"

        print(row)

    # Summary
    total    = len(analytics)
    cheapest = sum(1 for a in analytics if a["price_rank"] == 1)
    print(f"\n  {C.DIM}Cheapest on {cheapest}/{total} products "
          f"({cheapest/total*100:.0f}% of catalog){C.RESET}")


def show_recommendations(state: AgentState, retailer_id: int):
    """Show recommendations queue."""
    _header("💡 PRICING RECOMMENDATIONS")

    recs = state["recommendations"]
    if not recs:
        print(f"  {C.GREEN}✓ No price changes recommended.{C.RESET}")
        return

    actionable = [r for r in recs if r["action"] != "hold"]
    holds      = [r for r in recs if r["action"] == "hold"]

    if not actionable:
        print(f"  {C.GREEN}✓ All {len(holds)} products: hold current pricing.{C.RESET}")
        return

    print(f"  {len(actionable)} actionable | {len(holds)} hold\n")

    for r in sorted(actionable, key=lambda x: abs(x.get("price_change_pct", 0)), reverse=True):
        action    = r["action"]
        change    = r["price_change"]
        change_pct = r["price_change_pct"] * 100

        if change < 0:
            sym   = "↓"
            color = C.CYAN
        else:
            sym   = "↑"
            color = C.GREEN

        print(f"  {color}{sym} {r['product_name'][:45]:<45}{C.RESET}")
        print(f"    Price:  ₹{r['current_price']:,.0f}  →  {color}₹{r['recommended_price']:,.0f}{C.RESET} "
              f"({color}{change_pct:+.1f}%{C.RESET})"
              f"  [{action}]  conf={r.get('confidence', 0):.0%}")
        if r.get("reasoning"):
            print(f"    {C.DIM}↳ {r['reasoning'][:100]}{C.RESET}")
        if r.get("guardrail_applied"):
            print(f"    {C.YELLOW}⚙ Guardrail: {r.get('guardrail_note','')}{C.RESET}")
        print()


def show_alerts(state: AgentState):
    """Show alert feed."""
    alerts = state["alerts"]
    if not alerts:
        return

    _header("🔔 ALERTS")

    severity_order = {"high": 0, "medium": 1, "low": 2}
    sorted_alerts = sorted(alerts, key=lambda a: severity_order.get(a.get("severity", "low"), 3))

    for a in sorted_alerts[:10]:
        sev = a.get("severity", "low")
        color = C.RED if sev == "high" else (C.YELLOW if sev == "medium" else C.DIM)
        print(f"  {color}{a['message']}{C.RESET}")


def show_briefing(state: AgentState):
    """Show the morning briefing."""
    if not state["morning_briefing"]:
        return

    _header("📰 MORNING BRIEFING")
    for line in state["morning_briefing"].split("\n"):
        print(f"  {line}")


def show_cycle_summary(state: AgentState):
    """Show cycle execution summary."""
    _header("⚡ CYCLE SUMMARY")
    _row("Cycle ID:",          state["cycle_id"])
    _row("Started:",           state["cycle_started_at"][:19].replace("T", " "))
    _row("Records scraped:",   str(len(state["scraped_records"])))
    _row("Products matched:",  str(len(state["product_matches"])))
    _row("Analytics computed:",str(len(state["analytics"])))
    _row("Recommendations:",   str(len(state["recommendations"])))
    _row("Alerts raised:",     str(len(state["alerts"])))

    if state["errors"]:
        print(f"\n  {C.YELLOW}Errors encountered:{C.RESET}")
        for e in state["errors"]:
            print(f"    {C.RED}• {e}{C.RESET}")


def show_full_dashboard(state: AgentState, retailer_id: int):
    """Show the complete terminal dashboard."""
    # os.system("clear" if os.name == "posix" else "cls")

    print(f"\n{C.BOLD}{C.BLUE}  ██████╗ ███████╗████████╗ █████╗ ██╗██╗      █████╗  ██████╗ ███████╗███╗  ██╗████████╗{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  RetailAgent — Competitive Price Intelligence{C.RESET}")
    print(f"  {C.DIM}Store: {state['retailer_profile'].store_name}  |  "
          f"Category: {state['retailer_profile'].category}  |  "
          f"{datetime.now().strftime('%d %b %Y %H:%M')}{C.RESET}")

    show_price_comparison(state)
    show_recommendations(state, retailer_id)
    show_alerts(state)
    show_briefing(state)
    show_cycle_summary(state)

    print(f"\n{C.BLUE}{'═'*70}{C.RESET}\n")


def interactive_approval(state: AgentState, retailer_id: int) -> AgentState:
    """
    Allow the retailer to approve/reject recommendations interactively.
    Only runs if auto_apply is False and there are actionable recommendations.
    """
    if state["retailer_profile"].auto_apply_prices:
        return state

    actionable = [r for r in state["recommendations"] if r["action"] != "hold"]
    if not actionable:
        return state

    _header("✅ RECOMMENDATION APPROVAL")
    print(f"  {len(actionable)} price changes are waiting for your approval.")
    print(f"  {C.DIM}Options: a=approve, r=reject, s=skip, q=approve all{C.RESET}\n")

    for r in actionable:
        change_pct = r["price_change_pct"] * 100
        print(f"  Product: {C.BOLD}{r['product_name']}{C.RESET}")
        print(f"  Change:  ₹{r['current_price']:,.0f} → ₹{r['recommended_price']:,.0f} ({change_pct:+.1f}%)")
        print(f"  Reason:  {C.DIM}{r.get('reasoning','')[:100]}{C.RESET}")

        choice = input("  [a/r/s/q] → ").strip().lower()

        if choice == "q":
            for remaining in actionable:
                remaining["approved"] = True
            print(f"  {C.GREEN}✓ All approved.{C.RESET}")
            break
        elif choice == "a":
            r["approved"] = True
            # Apply to catalog
            for product in state["retailer_profile"].catalog:
                if product["sku"] == r["retailer_sku"]:
                    product["current_price"] = r["recommended_price"]
                    break
            print(f"  {C.GREEN}✓ Approved.{C.RESET}\n")
        elif choice == "r":
            r["approved"] = False
            print(f"  {C.RED}✗ Rejected.{C.RESET}\n")
        else:
            print(f"  {C.DIM}Skipped.{C.RESET}\n")

    # Update DB with approval decisions
    approved_recs = [r for r in actionable if r.get("approved") is not None]
    if approved_recs:
        db.update_recommendation_approvals(
            retailer_id=retailer_id,
            cycle_id=state["cycle_id"],
            decisions=approved_recs,
        )

    return state
