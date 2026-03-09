"""
RetailAgent — Agent Graph (LangGraph-style State Machine)
===========================================================
Implements the full LangGraph-style directed graph in pure Python.

Nodes  = agents (functions that take state → return state)
Edges  = conditional routing based on state flags
State  = AgentState dataclass flowing through every node

Graph flow:
  START
    ↓
  [intake_node]  ← if profile incomplete
    ↓
  [planner_node]
    ↓
  [scraper_node]  ← parallel async per competitor
    ↓
  [normalizer_node]
    ↓
  [analyst_node]
    ↓
  [pricing_node]
    ↓
  [decision_router] → auto_apply → [apply_prices]
                    → suggest    → [queue_for_review]
    ↓
  [reporter_node]
    ↓
  [cycle_log_node]
    ↓
  END
"""

import uuid
from datetime import datetime
from core.state import AgentState, RetailerProfile
from core import database as db

from agents.intake_agent    import run_intake, load_demo_profile
from agents.planner_agent   import run_planner
from agents.scraper_agent   import run_scraper
from agents.normalizer_agent import run_normalizer
from agents.analyst_agent   import run_analyst
from agents.pricing_agent   import run_pricing
from agents.reporter_agent  import run_reporter


# ─── Node definitions ────────────────────────────────────────────

class AgentGraph:
    """
    Pure-Python LangGraph-style state machine.
    Each node is a callable that receives (state, retailer_id) → state.
    Edges are conditional: the router reads state flags to decide next node.
    """

    def __init__(self, retailer_id: int):
        self.retailer_id = retailer_id
        self.node_log    = []

    def _log(self, node_name: str, state: AgentState):
        state.current_node = node_name
        self.node_log.append({
            "node": node_name,
            "at": datetime.now().isoformat(),
        })

    # ── Node: intake ─────────────────────────────
    def intake_node(self, state: AgentState) -> AgentState:
        self._log("intake", state)
        return run_intake(state)

    # ── Node: planner ────────────────────────────
    def planner_node(self, state: AgentState) -> AgentState:
        self._log("planner", state)
        return run_planner(state, self.retailer_id)

    # ── Node: scraper ────────────────────────────
    def scraper_node(self, state: AgentState) -> AgentState:
        self._log("scraper", state)
        return run_scraper(state, self.retailer_id)

    # ── Node: normalizer ─────────────────────────
    def normalizer_node(self, state: AgentState) -> AgentState:
        self._log("normalizer", state)
        return run_normalizer(state, self.retailer_id)

    # ── Node: analyst ────────────────────────────
    def analyst_node(self, state: AgentState) -> AgentState:
        self._log("analyst", state)
        return run_analyst(state, self.retailer_id)

    # ── Node: pricing ────────────────────────────
    def pricing_node(self, state: AgentState) -> AgentState:
        self._log("pricing", state)
        return run_pricing(state, self.retailer_id)

    # ── Node: reporter ───────────────────────────
    def reporter_node(self, state: AgentState) -> AgentState:
        self._log("reporter", state)
        return run_reporter(state, self.retailer_id)

    # ── Node: cycle_log ──────────────────────────
    def cycle_log_node(self, state: AgentState) -> AgentState:
        self._log("cycle_log", state)
        db.save_cycle_log(self.retailer_id, {
            "cycle_id":             state.cycle_id,
            "started_at":           state.cycle_started_at,
            "ended_at":             datetime.now().isoformat(),
            "status":               "completed",
            "records_scraped":      len(state.scraped_records),
            "matches_found":        len(state.product_matches),
            "recommendations_made": len(state.recommendations),
            "briefing":             state.morning_briefing,
            "errors":               state.errors,
        })
        return state

    # ── Conditional Router ───────────────────────
    def _route(self, state: AgentState) -> str:
        """
        Decides which node runs next based on state.
        Mimics LangGraph's conditional_edges.
        """
        if state.needs_onboarding:
            return "intake"
        if state.execution_plan is None:
            return "planner"
        if not state.scraping_complete:
            return "scraper"
        if not state.analysis_complete:
            return "normalizer"
        if not state.analytics:
            return "analyst"
        if not state.recommendations:
            return "pricing"
        if not state.morning_briefing:
            return "reporter"
        return "cycle_log"

    # ── Main execution loop ──────────────────────
    def run(self, state: AgentState) -> AgentState:
        """
        Execute the full agent graph from current state.
        Runs nodes in order, routing based on state after each.
        """
        print(f"\n{'═'*60}")
        print(f"  RetailAgent — Cycle: {state.cycle_id}")
        print(f"  Started: {state.cycle_started_at}")
        print(f"{'═'*60}")

        NODE_MAP = {
            "intake":      self.intake_node,
            "planner":     self.planner_node,
            "scraper":     self.scraper_node,
            "normalizer":  self.normalizer_node,
            "analyst":     self.analyst_node,
            "pricing":     self.pricing_node,
            "reporter":    self.reporter_node,
            "cycle_log":   self.cycle_log_node,
        }

        # Fixed execution order (DAG traversal)
        EXECUTION_ORDER = [
            "intake", "planner", "scraper",
            "normalizer", "analyst", "pricing",
            "reporter", "cycle_log"
        ]

        for node_name in EXECUTION_ORDER:
            # Skip intake if onboarding already done
            if node_name == "intake" and not state.needs_onboarding:
                continue

            try:
                node_fn = NODE_MAP[node_name]
                state   = node_fn(state)
            except Exception as e:
                error_msg = f"Node '{node_name}' failed: {e}"
                state.errors.append(error_msg)
                print(f"\n  ⚠  {error_msg}")

                # Non-fatal nodes: continue to next
                # Fatal nodes: halt the graph
                if node_name in ("intake", "planner"):
                    print("  ✗  Fatal error in setup phase. Stopping.")
                    break
                # For other nodes, mark as skipped and continue
                continue

        print(f"\n{'═'*60}")
        print(f"  Cycle complete. Nodes executed: {[n['node'] for n in self.node_log]}")
        print(f"{'═'*60}\n")

        return state


# ─── Graph Factory ───────────────────────────────────────────────

def create_initial_state(profile: RetailerProfile = None) -> AgentState:
    """Creates a fresh AgentState for a new cycle."""
    state = AgentState()
    state.cycle_id       = str(uuid.uuid4())[:8]
    state.cycle_started_at = datetime.now().isoformat()

    if profile:
        state.retailer_profile = profile
        state.needs_onboarding = not profile.onboarding_complete
    else:
        state.needs_onboarding = True

    return state


def run_full_cycle(retailer_id: int, profile: RetailerProfile = None,
                   interactive: bool = True) -> AgentState:
    """
    Entry point: run one complete RetailAgent cycle.

    Args:
        retailer_id:  DB id of the retailer (0 for new)
        profile:      Pre-loaded profile (skips onboarding if provided)
        interactive:  If True, run intake interactively via CLI
    """
    # Load profile from DB if retailer_id is known
    if retailer_id > 0 and profile is None:
        profile_data = db.load_retailer_profile(retailer_id)
        if profile_data:
            p = RetailerProfile()
            for k, v in profile_data.items():
                if hasattr(p, k):
                    setattr(p, k, v)
            profile = p

    state = create_initial_state(profile)

    if not interactive and state.needs_onboarding:
        # Use demo profile for non-interactive runs
        state.retailer_profile = load_demo_profile()
        state.needs_onboarding = False

    graph = AgentGraph(retailer_id)
    state = graph.run(state)

    # Save updated retailer profile
    if state.retailer_profile.onboarding_complete:
        rid = db.save_retailer_profile(state.retailer_profile.to_dict())
        if retailer_id == 0:
            retailer_id = rid
        # Re-save cycle log with correct retailer_id
        graph.retailer_id = retailer_id

    return state