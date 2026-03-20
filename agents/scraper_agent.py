"""
agents/scraper_agent.py  —  DEPRECATED REDIRECT
================================================
The scraper was refactored into a sub-agentic package at:
    agents/scraper/
        __init__.py      ← exports run_scraper_node
        state.py         ← ScraperSubState TypedDict
        graph.py         ← LangGraph sub-graph (navigator→fetcher→extractor)
        navigator.py     ← Sub-agent 1: renders page with Playwright
        fetcher.py       ← Sub-agent 2: slices DOM to product section
        extractor.py     ← Sub-agent 3: extracts name+price (BS4 → vision fallback)
        orchestrator.py  ← main graph node, runs sub-graph per target

core/graph.py already imports from the new package:
    from agents.scraper import run_scraper_node

This file is kept only so that any direct imports of scraper_agent
continue to work without errors.
"""

# Re-export everything from the new package so old imports don't break
from agents.scraper import run_scraper_node
from agents.scraper.orchestrator import ACTIVE_COMPETITORS

__all__ = ["run_scraper_node", "ACTIVE_COMPETITORS"]