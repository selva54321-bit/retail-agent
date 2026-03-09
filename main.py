"""
RetailAgent — Main Entry Point (LangChain + LangGraph Edition)
===============================================================
Usage:
    python main.py                     # Interactive onboarding + cycle
    python main.py --demo              # Demo mode (no interaction)
    python main.py --demo --stream     # Demo with streamed node output
    python main.py --retailer-id 1     # Resume saved retailer
    python main.py --check             # System status
"""

import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database  import init_db, list_retailer_profiles
from core.llm       import check_ollama
from core.graph     import run_cycle, make_initial_state
from core.dashboard import show_full_dashboard, interactive_approval
from agents.intake_agent import load_demo_profile


def print_banner():
    print("""
╔══════════════════════════════════════════════════════════╗
║   RetailAgent — LangChain + LangGraph Edition            ║
║   Automated Competitor Price Monitoring                  ║
║   Multi-Agent: StateGraph | LCEL | Structured Output     ║
╚══════════════════════════════════════════════════════════╝
""")


def check_status():
    status = check_ollama()
    print("\n📋 SYSTEM STATUS")
    print(f"  Ollama:           {'✅ running' if status['running'] else '❌ not running'}")
    print(f"  Chat model ready: {'✅' if status['chat_ready'] else '⚠ not installed'}")
    print(f"  Embeddings ready: {'✅' if status['embed_ready'] else '⚠ fallback mode'}")
    print(f"  Models:           {', '.join(status['models']) or 'none'}")
    if not status["running"]:
        print("\n  To enable full LLM features:")
        print("    ollama serve")
        print("    ollama pull llama3.1")
        print("    ollama pull nomic-embed-text")
    profiles = list_retailer_profiles()
    print(f"\n  Saved retailers:  {len(profiles)}")
    for p in profiles:
        print(f"    [{p['id']}] {p['store_name']}")


def main():
    print_banner()
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo",         action="store_true")
    parser.add_argument("--stream",       action="store_true")
    parser.add_argument("--cycles",       type=int, default=1)
    parser.add_argument("--retailer-id",  type=int, default=0)
    parser.add_argument("--check",        action="store_true")
    args = parser.parse_args()

    init_db()

    status = check_ollama()
    mode   = "LLM" if status["chat_ready"] else "rule-based fallback"
    print(f"  Mode: {mode}\n")

    if args.check:
        check_status()
        return

    if args.demo:
        profile = load_demo_profile()
        rid     = 0
        for _ in range(args.cycles):
            final = run_cycle(rid, profile=profile, stream=args.stream)
            # Retrieve actual retailer_id after first cycle saves profile
            profiles = list_retailer_profiles()
            rid = profiles[0]["id"] if profiles else 0
            profile = final["retailer_profile"]
            show_full_dashboard(final, rid)
            if not profile.auto_apply_prices:
                interactive_approval(final, rid)
    else:
        rid = args.retailer_id
        final = run_cycle(rid, stream=args.stream)
        profiles = list_retailer_profiles()
        rid = profiles[0]["id"] if profiles else rid
        show_full_dashboard(final, rid)
        if not final["retailer_profile"].auto_apply_prices:
            interactive_approval(final, rid)


if __name__ == "__main__":
    main()