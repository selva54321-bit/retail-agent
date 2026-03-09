"""
RetailAgent — Main Entry Point
================================
Usage:
    python main.py                    # Interactive: onboarding + one full cycle
    python main.py --demo             # Non-interactive demo with simulated data
    python main.py --demo --cycles 3  # Run 3 demo cycles continuously
    python main.py --retailer-id 1    # Resume existing retailer (skip onboarding)
    python main.py --check            # Check Ollama status and exit

"""

import sys
import os
import time
import argparse
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database import init_db, list_retailer_profiles
from core.graph    import run_full_cycle, create_initial_state
from core.dashboard import show_full_dashboard, interactive_approval
from core.llm      import status_report as llm_status


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   ██████╗ ███████╗████████╗ █████╗ ██╗██╗                   ║
║   ██╔══██╗██╔════╝╚══██╔══╝██╔══██╗██║██║                   ║
║   ██████╔╝█████╗     ██║   ███████║██║██║                   ║
║   ██╔══██╗██╔══╝     ██║   ██╔══██║██║██║                   ║
║   ██║  ██║███████╗   ██║   ██║  ██║██║███████╗              ║
║   ╚═╝  ╚═╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚══════╝             ║
║                                                              ║
║   Automated Competitor Price Monitoring                      ║
║   Multi-Agent System  |  Python + LangGraph-style           ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)


def check_status():
    """Print system status and exit."""
    print("\n📋 SYSTEM STATUS")
    print("─" * 40)
    status = llm_status()
    print(f"  Ollama running:       {'✅ Yes' if status['ollama_running'] else '❌ No'}")
    print(f"  Models installed:     {', '.join(status['models_installed']) or 'none'}")
    print(f"  Embedding available:  {'✅ Yes' if status['embedding_available'] else '⚠ Fallback mode'}")
    print(f"  Active chat model:    {status['chat_model']}")

    if not status['ollama_running']:
        print("\n  ⚠  Ollama not detected. The system will run in fallback mode:")
        print("     - Rule-based planning (no LLM reasoning)")
        print("     - Simulated competitor prices (no live scraping)")
        print("     - Template briefings (no LLM-generated insights)")
        print("\n  To enable full LLM features:")
        print("     1. Install Ollama: https://ollama.ai")
        print("     2. Pull a model:   ollama pull llama3.1")
        print("     3. Optional:       ollama pull nomic-embed-text")

    print("\n  Database:  retailagent.db")
    profiles = list_retailer_profiles()
    print(f"  Retailers: {len(profiles)} saved")
    for p in profiles:
        print(f"    [{p['id']}] {p['store_name']} (updated {p['updated_at'][:10]})")

    print()


def select_or_create_retailer() -> tuple[int, bool]:
    """
    Interactive: ask user to select existing retailer or create new.
    Returns (retailer_id, is_new).
    """
    profiles = list_retailer_profiles()
    if profiles:
        print("\nExisting retailers:")
        for p in profiles:
            print(f"  [{p['id']}] {p['store_name']}")
        print(f"  [0] Create new retailer")
        try:
            choice = int(input("\nSelect retailer ID (or 0 for new): ").strip())
            if choice > 0 and any(p["id"] == choice for p in profiles):
                return choice, False
        except ValueError:
            pass
    return 0, True


def run_demo(cycles: int = 1):
    """
    Run a full demo without interactive prompts.
    Uses the demo profile with simulated data.
    """
    from agents.intake_agent import load_demo_profile
    from core import database as db

    print("\n🔷 DEMO MODE — Using TechZone Electronics demo profile")
    print("   All competitor prices are simulated (no live web scraping)")

    profile = load_demo_profile()
    retailer_id = db.save_retailer_profile(profile.to_dict())

    for cycle_num in range(1, cycles + 1):
        if cycles > 1:
            print(f"\n{'▶'*3} Starting cycle {cycle_num}/{cycles}")

        state = run_full_cycle(
            retailer_id=retailer_id,
            profile=profile,
            interactive=False,
        )

        # Update profile with any catalog changes
        profile = state.retailer_profile
        db.save_retailer_profile(profile.to_dict())

        show_full_dashboard(state, retailer_id)

        if not profile.auto_apply_prices:
            interactive_approval(state, retailer_id)

        if cycles > 1 and cycle_num < cycles:
            print(f"\n  ⏱  Waiting 3 seconds before next cycle...")
            time.sleep(3)

    print("\n✅ Demo complete.")
    print(f"   All data saved to: retailagent.db")
    print(f"   Run again to see updated price history and trend analysis.")


def run_interactive(retailer_id: int = 0):
    """Run a full interactive cycle with CLI onboarding."""
    state = run_full_cycle(
        retailer_id=retailer_id,
        interactive=True,
    )

    # Get the retailer_id (may have been created during onboarding)
    from core import database as db
    profiles = list_retailer_profiles()
    if profiles:
        retailer_id = profiles[0]["id"]

    show_full_dashboard(state, retailer_id)

    if not state.retailer_profile.auto_apply_prices:
        interactive_approval(state, retailer_id)

    print("\n✅ Cycle complete.")


def main():
    print_banner()

    parser = argparse.ArgumentParser(description="RetailAgent — Automated Price Monitoring")
    parser.add_argument("--demo",        action="store_true", help="Run demo mode (no interaction)")
    parser.add_argument("--cycles",      type=int, default=1, help="Number of demo cycles to run")
    parser.add_argument("--retailer-id", type=int, default=0, help="Resume existing retailer")
    parser.add_argument("--check",       action="store_true", help="Check system status")
    args = parser.parse_args()

    # Initialize database
    init_db()

    if args.check:
        check_status()
        return

    # Status summary
    status = llm_status()
    if status["ollama_running"]:
        print(f"  ✅ Ollama running | Model: {status['chat_model']}")
    else:
        print(f"  ⚠  Ollama not detected — running in fallback mode")
        print(f"     (rule-based planning + simulated prices)\n")

    if args.demo:
        run_demo(cycles=args.cycles)
    elif args.retailer_id > 0:
        run_interactive(retailer_id=args.retailer_id)
    else:
        retailer_id, is_new = select_or_create_retailer()
        run_interactive(retailer_id=retailer_id)


if __name__ == "__main__":
    main()