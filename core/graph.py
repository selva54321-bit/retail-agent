"""
RetailAgent — LangGraph StateGraph Orchestrator
=================================================
This is the core of RetailAgent. Everything connects here.

LangGraph patterns used:
  - StateGraph                  → defines the agent graph
  - add_node()                  → registers each agent as a node
  - add_edge()                  → unconditional A→B connection
  - add_conditional_edges()     → route based on state after a node
  - MemorySaver                 → persist state across sessions (checkpointing)
  - interrupt_before            → human-in-the-loop pause for approvals
  - Command                     → explicit node-to-node routing
  - START / END                 → LangGraph reserved entry/exit nodes

Graph topology:
                    ┌──────────────────────────────┐
                    ▼                              │
    START → [route_start] ─► [intake] ─────────────┤
                │                                  │
                ▼                                  │
           [planner]   ← known big-brand targets   │
                │                                  │
                ▼                                  │
            [scout]    ← discovers LOCAL shops in  │
                │         retailer's city via search│
                ▼                                  │
           [scraper]   ← parallel per competitor   │
                │                                  │
                ▼                                  │
           [normalizer]                            │
                │                                  │
                ▼                                  │
            [analyst]                              │
                │                                  │
                ▼                                  │
            [pricing]                              │
                │                                  │
         ┌──────┴──────┐                           │
         ▼             ▼                           │
   [auto_apply]  [queue_review] ◄─ human-in-loop   │
         └──────┬──────┘                           │
                ▼                                  │
           [reporter]                              │
                │                                  │
                ▼                                  │
           [cycle_log] ──────────────────────────► END
"""

import uuid
from datetime  import datetime
from typing    import Literal

from langgraph.graph        import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types        import interrupt

from core.state  import AgentState, RetailerProfile
from core        import database as db

from agents.intake_agent      import run_intake_node,     load_demo_profile
from agents.planner_agent     import run_planner_node
from agents.scout_agent       import run_scout_node
from agents.scraper_agent     import run_scraper_node
from agents.normalizer_agent  import run_normalizer_node
from agents.analyst_agent     import run_analyst_node
from agents.pricing_agent     import run_pricing_node
from agents.reporter_agent    import run_reporter_node


# ─── Routing functions ────────────────────────────────────────────

def route_start(state: AgentState) -> Literal["intake", "planner"]:
    """
    Conditional edge from START.
    If the retailer hasn't completed onboarding → intake node.
    Otherwise jump straight to planner.
    """
    if state.get("needs_onboarding", True):
        return "intake"
    return "planner"


def route_after_pricing(state: AgentState) -> Literal["auto_apply", "queue_review"]:
    """
    Conditional edge after pricing node.
    auto_apply → prices written automatically
    queue_review → human approval required (interrupt_before triggers here)
    """
    if state["retailer_profile"].auto_apply_prices:
        return "auto_apply"
    return "queue_review"


# ─── Utility nodes ────────────────────────────────────────────────

def auto_apply_node(state: AgentState) -> dict:
    """
    Node: auto-apply all approved recommendations to catalog.
    Only reached when retailer has auto_apply_prices=True.
    """
    recs    = state["recommendations"]
    catalog = state["retailer_profile"].catalog
    idx_map = {p["sku"]: i for i, p in enumerate(catalog)}
    applied = 0

    for rec in recs:
        if rec["action"] != "hold":
            idx = idx_map.get(rec["retailer_sku"])
            if idx is not None:
                catalog[idx]["current_price"] = rec["recommended_price"]
                rec["approved"] = True
                applied += 1

    print(f"[Auto-apply] {applied} prices updated.")
    return {"current_node": "auto_apply"}


def queue_review_node(state: AgentState) -> dict:
    """
    Node: queue recommendations for human review.
    LangGraph interrupt_before='queue_review' pauses the graph here,
    lets the human approve/reject via the dashboard, then resumes.
    All recommendations start as approved=None (pending).
    """
    actionable = [r for r in state["recommendations"] if r["action"] != "hold"]
    print(f"[Queue Review] {len(actionable)} recommendations queued for approval.")
    return {"current_node": "queue_review"}


def cycle_log_node(state: AgentState) -> dict:
    """Node: write cycle summary to audit log."""
    db.save_cycle_log(state["retailer_id"], {
        "cycle_id":             state["cycle_id"],
        "started_at":           state["cycle_started_at"],
        "ended_at":             datetime.now().isoformat(),
        "status":               "completed",
        "records_scraped":      len(state["scraped_records"]),
        "matches_found":        len(state["product_matches"]),
        "recommendations_made": len(state["recommendations"]),
        "briefing":             state["morning_briefing"],
        "errors":               state["errors"],
    })
    print(f"\n[Cycle Log] Cycle {state['cycle_id']} complete.")
    return {"current_node": "cycle_log"}


# ─── Graph builder ────────────────────────────────────────────────

def build_graph(checkpointer=None) -> StateGraph:
    """
    Constructs and compiles the RetailAgent LangGraph StateGraph.

    Args:
        checkpointer: Optional LangGraph checkpointer for state persistence.
                      Pass MemorySaver() for in-memory persistence across calls.

    Returns:
        Compiled CompiledStateGraph ready to invoke/stream.
    """
    graph = StateGraph(AgentState)

    # ── Register all nodes ──────────────────────────────────────
    graph.add_node("intake",       run_intake_node)
    graph.add_node("planner",      run_planner_node)
    graph.add_node("scout",        run_scout_node)
    graph.add_node("scraper",      run_scraper_node)
    graph.add_node("normalizer",   run_normalizer_node)
    graph.add_node("analyst",      run_analyst_node)
    graph.add_node("pricing",      run_pricing_node)
    graph.add_node("auto_apply",   auto_apply_node)
    graph.add_node("queue_review", queue_review_node)
    graph.add_node("reporter",     run_reporter_node)
    graph.add_node("cycle_log",    cycle_log_node)

    # ── Entry: conditional routing based on onboarding state ────
    graph.add_conditional_edges(
        START,
        route_start,
        {
            "intake":  "intake",
            "planner": "planner",
        }
    )

    # ── After intake → always go to planner ────────────────────
    graph.add_edge("intake", "planner")

    # ── planner → scout (discover locals) → scraper ────────────
    # Scout runs after planner so it has the retailer profile + location.
    # It adds local competitors to the registry before scraper runs.
    graph.add_edge("planner",    "scout")
    graph.add_edge("scout",      "scraper")

    graph.add_edge("scraper",    "normalizer")
    graph.add_edge("normalizer", "analyst")
    graph.add_edge("analyst",    "pricing")

    # ── After pricing: conditional on auto_apply preference ─────
    graph.add_conditional_edges(
        "pricing",
        route_after_pricing,
        {
            "auto_apply":   "auto_apply",
            "queue_review": "queue_review",
        }
    )

    # ── Both pricing paths converge at reporter ─────────────────
    graph.add_edge("auto_apply",   "reporter")
    graph.add_edge("queue_review", "reporter")

    # ── Final nodes ─────────────────────────────────────────────
    graph.add_edge("reporter",  "cycle_log")
    graph.add_edge("cycle_log", END)

    # ── Compile with optional checkpointer ──────────────────────
    compile_kwargs = {}
    if checkpointer:
        compile_kwargs["checkpointer"] = checkpointer

    # Human-in-the-loop: pause BEFORE queue_review for approval
    # compile_kwargs["interrupt_before"] = ["queue_review"]

    return graph.compile(**compile_kwargs)


# ─── Initial state factory ────────────────────────────────────────

def make_initial_state(retailer_id: int,
                       profile: RetailerProfile | None = None) -> AgentState:
    """
    Create a fresh AgentState for a new cycle.
    LangGraph requires all TypedDict fields to be initialised.
    """
    # Load existing profile from DB if not provided
    if profile is None and retailer_id > 0:
        profile_data = db.load_retailer_profile(retailer_id)
        if profile_data:
            profile = RetailerProfile(**{
                k: v for k, v in profile_data.items()
                if k in RetailerProfile.model_fields
            })

    needs_onboarding = (profile is None or not profile.onboarding_complete)

    return AgentState(
        retailer_id        = retailer_id,
        retailer_profile   = profile or RetailerProfile(),
        execution_plan     = None,
        cycle_id           = str(uuid.uuid4())[:8],
        cycle_started_at   = datetime.now().isoformat(),
        needs_onboarding   = needs_onboarding,
        scraping_complete  = False,
        analysis_complete  = False,
        scraped_records    = [],
        product_matches    = [],
        analytics          = [],
        recommendations    = [],
        alerts             = [],
        errors             = [],
        morning_briefing   = "",
        current_node       = "start",
    )


# ─── Main runner ─────────────────────────────────────────────────

def run_cycle(retailer_id: int,
              profile: RetailerProfile | None = None,
              stream: bool = False) -> AgentState:
    """
    Run one complete RetailAgent cycle.

    Args:
        retailer_id:  DB id of the retailer (0 for new)
        profile:      Pre-loaded profile (skips onboarding if complete)
        stream:       If True, print each node's output as it completes

    Returns:
        Final AgentState after the cycle completes
    """
    db.init_db()
    checkpointer = MemorySaver()
    compiled     = build_graph(checkpointer=checkpointer)

    initial_state = make_initial_state(retailer_id, profile)

    # Thread config for MemorySaver checkpointing
    config = {
        "configurable": {
            "thread_id": f"retailagent-{retailer_id}-{initial_state['cycle_id']}",
        }
    }

    print(f"\n{'═'*60}")
    print(f"  RetailAgent LangGraph Cycle: {initial_state['cycle_id']}")
    print(f"  Nodes: intake→planner→scraper→normalizer→analyst→pricing→reporter")
    print(f"{'═'*60}")

    if stream:
        # Stream mode: print node name as each finishes
        final_state = None
        for chunk in compiled.stream(initial_state, config=config):
            node_name = list(chunk.keys())[0]
            print(f"  ✓ Node [{node_name}] complete")
            # Merge chunk into final state
            if final_state is None:
                final_state = dict(initial_state)
            final_state.update(chunk.get(node_name, {}))
        return final_state or initial_state
    else:
        # Invoke mode: run to completion
        final_state = compiled.invoke(initial_state, config=config)

        # Save updated profile to DB
        if final_state["retailer_profile"].onboarding_complete:
            rid = db.save_retailer_profile(final_state["retailer_profile"].model_dump())
            if retailer_id == 0:
                retailer_id = rid

        return final_state