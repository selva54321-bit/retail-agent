"""
RetailAgent — Main Entry Point (LangChain + LangGraph Edition)
===============================================================
Usage:
    python main.py                     # Prompts for Gemini/Ollama, then runs
    python main.py --demo              # Demo mode (no interaction)
    python main.py --demo --stream     # Stream node-by-node output
    python main.py --retailer-id 1     # Resume saved retailer
    python main.py --check             # System status and exit

    # Skip the startup prompt:
    python main.py --provider gemini
    python main.py --provider ollama

Set your model names and API key directly in core/llm.py (_config block).
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database       import init_db, list_retailer_profiles
from core.llm            import (set_provider, check_ollama, check_gemini,
                                  get_active_provider, get_active_model_name,
                                  _config)
from core.graph          import run_cycle
from core.dashboard      import show_full_dashboard, interactive_approval
from agents.intake_agent import load_demo_profile


# ─────────────────────────────────────────────────────────────────
#  PROVIDER SELECTION — single clean prompt at startup
# ─────────────────────────────────────────────────────────────────

def select_provider(args) -> None:
    """
    Ask the user to pick Gemini or Ollama (local).
    All model names and credentials come from _config in core/llm.py.
    If --provider flag was passed, skip the prompt entirely.
    """

    # ── Skip prompt if CLI flag given ────────────────────────────
    if args.provider:
        set_provider(args.provider)
        return

    # ── Interactive: just pick the backend ───────────────────────
    print("\n  ┌──────────────────────────────────────────────────┐")
    print("  │  Select LLM backend:                             │")
    print("  │                                                  │")
    print(f"  │   1 → Gemini  (model: {_config['gemini_model']:<25}│")
    print(f"  │   2 → Ollama  (model: {_config['ollama_model']:<25}│")
    print("  │                                                  │")
    print("  │  Edit core/llm.py to change model names / key   │")
    print("  └──────────────────────────────────────────────────┘")

    while True:
        choice = input("  Your choice [1/2]: ").strip()
        if choice in ("1", "g", "gemini"):
            set_provider("gemini")
            break
        elif choice in ("2", "o", "ollama"):
            set_provider("ollama")
            break
        else:
            print("  Please enter 1 or 2.")


# ─────────────────────────────────────────────────────────────────
#  BANNER & STATUS
# ─────────────────────────────────────────────────────────────────

def print_banner():
    print("""
╔══════════════════════════════════════════════════════════╗
║   RetailAgent — LangChain + LangGraph Edition            ║
║   Automated Competitor Price Monitoring                  ║
║   Multi-Agent: StateGraph | LCEL | Structured Output     ║
╚══════════════════════════════════════════════════════════╝""")


def check_status():
    print("\n📋 SYSTEM STATUS")

    ollama = check_ollama()
    print(f"  Ollama running:   {'✅' if ollama['running'] else '❌'}")
    print(f"  Ollama model:     {_config['ollama_model']}  "
          f"({'✅ installed' if ollama['chat_ready'] else '⚠ not found'})")
    print(f"  Embed model:      {_config['embed_model']}  "
          f"({'✅ installed' if ollama['embed_ready'] else '⚠ not found — fallback active'})")

    api_key = _config["gemini_api_key"]
    print(f"  Gemini API key:   {'✅ set' if api_key else '❌ not set'}")
    print(f"  Gemini model:     {_config['gemini_model']}")

    profiles = list_retailer_profiles()
    print(f"\n  Saved retailers:  {len(profiles)}")
    for p in profiles:
        print(f"    [{p['id']}] {p['store_name']}")

    print("\n  To change models or API key: edit _config in core/llm.py")


# ─────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    print_banner()

    parser = argparse.ArgumentParser(description="RetailAgent — Competitive Price Monitoring")
    parser.add_argument("--demo",         action="store_true", help="Run demo without interaction")
    parser.add_argument("--stream",       action="store_true", help="Stream node output as graph runs")
    parser.add_argument("--cycles",       type=int, default=1, help="Number of demo cycles")
    parser.add_argument("--retailer-id",  type=int, default=0, help="Resume existing retailer by DB id")
    parser.add_argument("--check",        action="store_true", help="Print system status and exit")
    parser.add_argument("--provider",     type=str, default="", help="gemini | ollama  (skips prompt)")
    args = parser.parse_args()

    init_db()

    if args.check:
        check_status()
        return

    # ── Pick backend (one prompt, done) ──────────────────────────
    select_provider(args)
    print(f"\n  ✅  {get_active_provider().upper()} ready "
          f"({get_active_model_name()})\n")

    # ── Run cycles ───────────────────────────────────────────────
    if args.demo:
        profile = load_demo_profile()
        rid     = 0
        for cycle_num in range(1, args.cycles + 1):
            if args.cycles > 1:
                print(f"\n{'▶' * 3} Cycle {cycle_num}/{args.cycles}")
            final    = run_cycle(rid, profile=profile, stream=args.stream)
            profiles = list_retailer_profiles()
            rid      = profiles[0]["id"] if profiles else 0
            profile  = final["retailer_profile"]
            show_full_dashboard(final, rid)
            if not profile.auto_apply_prices:
                interactive_approval(final, rid)
    else:
        rid   = args.retailer_id
        final = run_cycle(rid, stream=args.stream)
        profiles = list_retailer_profiles()
        rid      = profiles[0]["id"] if profiles else rid
        show_full_dashboard(final, rid)
        if not final["retailer_profile"].auto_apply_prices:
            interactive_approval(final, rid)


if __name__ == "__main__":
    main()