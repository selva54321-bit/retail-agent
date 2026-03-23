"""
RetailAgent — Main Entry Point (LangChain + LangGraph Edition)
===============================================================
Usage:
    python main.py                     # Interactive provider select, then run
    python main.py --demo              # Demo mode (no interaction)
    python main.py --demo --stream     # Stream node-by-node output
    python main.py --retailer-id 1     # Resume saved retailer
    python main.py --check             # System status and exit

    # Skip the startup prompt:
    python main.py --provider gemini
    python main.py --provider ollama
    python main.py --provider grok

Set model names and API keys in core/llm.py (_config block).
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database       import init_db, list_retailer_profiles
from core.llm            import (set_provider, check_ollama, check_gemini,
                                  check_grok, get_active_provider,
                                  get_active_model_name, _config)
from core.graph          import run_cycle
from core.dashboard      import show_full_dashboard, interactive_approval
from agents.intake_agent import load_demo_profile


# ─────────────────────────────────────────────────────────────────
#  PROVIDER SELECTION
# ─────────────────────────────────────────────────────────────────

def select_provider(args) -> None:
    if args.provider:
        set_provider(args.provider)
        return

    gemini_model = _config["gemini_model"]
    ollama_model = _config["ollama_model"]
    grok_model   = _config["grok_model"]

    print("\n  ┌──────────────────────────────────────────────────────┐")
    print("  │  Select LLM backend:                                 │")
    print("  │                                                      │")
    print(f"  │   1 → Gemini  (model: {gemini_model:<29}│")
    print(f"  │   2 → Ollama  (model: {ollama_model:<29}│")
    print(f"  │   3 → Grok    (model: {grok_model:<29}│")
    print("  │                                                      │")
    print("  │   Edit core/llm.py to change model names / keys     │")
    print("  └──────────────────────────────────────────────────────┘")

    while True:
        choice = input("  Your choice [1/2/3]: ").strip()
        if choice in ("1", "g", "gemini"):
            set_provider("gemini")
            break
        elif choice in ("2", "o", "ollama"):
            set_provider("ollama")
            break
        elif choice in ("3", "x", "grok"):
            set_provider("grok")
            break
        else:
            print("  Please enter 1, 2, or 3.")


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
    print(f"\n  Ollama:")
    print(f"    Running:       {'✅' if ollama['running'] else '❌'}")
    print(f"    Chat model:    {_config['ollama_model']}  "
          f"({'✅' if ollama['chat_ready'] else '⚠ not found'})")
    print(f"    Embed model:   {_config['embed_model']}  "
          f"({'✅' if ollama['embed_ready'] else '⚠ fallback active'})")

    api_key = _config["gemini_api_key"]
    print(f"\n  Gemini:")
    print(f"    API key:       {'✅ set' if api_key else '❌ not set'}")
    print(f"    Chat model:    {_config['gemini_model']}")

    grok_key = _config["grok_api_key"]
    print(f"\n  Grok (xAI):")
    print(f"    API key:       {'✅ set' if grok_key else '❌ not set  (export XAI_API_KEY=...)'}")
    print(f"    Chat model:    {_config['grok_model']}")

    profiles = list_retailer_profiles()
    print(f"\n  Saved retailers:  {len(profiles)}")
    for p in profiles:
        print(f"    [{p['id']}] {p['store_name']}")

    print("\n  Edit core/llm.py _config to change any model or key.")


# ─────────────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    print_banner()

    parser = argparse.ArgumentParser(description="RetailAgent — Competitive Price Monitoring")
    parser.add_argument("--demo",        action="store_true", help="Run demo without interaction")
    parser.add_argument("--stream",      action="store_true", help="Stream node output")
    parser.add_argument("--cycles",      type=int, default=1, help="Number of demo cycles")
    parser.add_argument("--retailer-id", type=int, default=0, help="Resume existing retailer")
    parser.add_argument("--check",       action="store_true", help="Print system status and exit")
    parser.add_argument("--provider",    type=str, default="",
                        help="gemini | ollama | grok  (skips prompt)")
    args = parser.parse_args()

    init_db()

    if args.check:
        check_status()
        return

    select_provider(args)
    print(f"\n  ✅  {get_active_provider().upper()} ready ({get_active_model_name()})\n")

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