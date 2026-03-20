"""
agents/scraper/__init__.py
==========================
Exposes run_scraper_node so graph.py imports stay clean:
  from agents.scraper import run_scraper_node
"""
from agents.scraper.orchestrator import run_scraper_node

__all__ = ["run_scraper_node"]