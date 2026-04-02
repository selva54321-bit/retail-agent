# RetailAgent — LangChain + LangGraph Edition

## Quick Start
```bash
pip install -r requirements.txt
playwright install chromium
python main.py --demo
```

## LangChain / LangGraph Patterns Used Per File

| File | LangChain/LangGraph Pattern |
|------|-----------------------------|
| `core/state.py` | `TypedDict` + Pydantic models for LangGraph state management |
| `core/llm.py` | `ChatGoogleGenerativeAI` / `ChatOllama` factory with retry logic |
| `core/graph.py` | `StateGraph` orchestrator with `Command` routing and `interrupt` for human-in-the-loop |
| `agents/intake_agent.py` | `ConversationChain` for guided retailer onboarding |
| `agents/planner_agent.py` | LCEL planning chain: `prompt | llm | JsonOutputParser` |
| `agents/scout_agent.py` | Regional Chain Lookup + deduplication logic |
| `agents/scraper/` | Sub-graph orchestration: `navigator` (Playwright) → `fetcher` → `extractor` (BS4) |
| `agents/normalizer_agent.py` | LLM-based product matching with token-overlap fallback |
| `agents/analyst_agent.py` | `RunnableLambda` pipeline for price trend and anomaly detection |
| `agents/pricing_agent.py` | Structured output (`with_structured_output`) for strategy-based recommendations |
| `agents/reporter_agent.py` | `load_summarize_chain` for generating morning briefings |

## Graph Structure
```
START
  │
  ├─► [intake]     ← Onboarding (if profile missing)
  │
  ▼
[planner]          ← Strategic target mapping (Amazon, Flipkart, etc.)
  │
  ▼
[scout]            ← Regional chain discovery (Poorvika, Girias, etc.)
  │
  ▼
[scraper]          ← Parallel Sub-graph (Navigator → Fetcher → Extractor)
  │
  ▼
[normalizer]       ← Cross-competitor SKU matching
  │
  ▼
[analyst]          ← Competitive price analysis & alerts
  │
  ▼
[pricing]          ← Recommendation engine (Strategy: parity/penetration/etc.)
  │
  ├─► [auto_apply]    (if auto_apply_prices=True)
  └─► [queue_review]  (Interactive CLI approval)
  │
  ▼
[reporter]         ← Executive briefing generator
  │
  ▼
[cycle_log] ──► END
```

## Scraper Pipeline
The scraper is a dedicated sub-graph designed for high reliability:
1. **Navigator**: Uses Playwright to load homepages and physically type into search boxes, mimicking human behavior to avoid bot detection.
2. **Fetcher**: Uses CSS focus to strip HTML noise (scripts, styles, navs) and isolate search results.
3. **Extractor**: Uses BeautifulSoup with site-specific selectors for top retailers and a "fuzzy" generic extractor for regional sites. No longer relies on Vision LLMs.