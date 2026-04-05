# RetailAgent — LangChain + LangGraph Edition

## Overview
RetailAgent is an automated, multi-agent AI framework for monitoring competitor retail pricing. Built using LangGraph, it autonomously identifies local and national competitors, dynamically scrapes their websites for specific SKUs, normalizes product variations using AI embeddings, and issues daily strategic price recommendations based on your business rules.

## Quick Start
```bash
pip install -r requirements.txt
playwright install chromium
python main.py --demo
```

## Agent Functionality Overview
The pipeline operates as a state machine. The full cycle flows seamlessly from capturing retail targets down to generating actionable insights.

1. **Intake Agent (`intake_agent.py`)**: Interactively interviews the user (if it's their first time) to understand the nature of their retail store, business strategy (e.g., penetration vs. premium), and minimum acceptable margin floors. 
2. **Planner Agent (`planner_agent.py`)**: Takes the user's local product catalog and maps them to known national competitors, building a blueprint of exactly which SKUs need to be searched on which domains today.
3. **Scout Agent (`scout_agent.py`)**: Dynamically discovers local/regional competitors. It searches the area around the retailer's zip code to find relevant chain stores (e.g., "Poorvika", "Croma") and registers them into the monitoring database.
4. **Scraper Sub-Graph (`scraper/`)**: The engine of data extraction. It runs a parallel, 3-stage mini-graph via a ThreadPoolExecutor for every single target:
   - **Navigator**: Uses Playwright to physically load the target homepage and type into the search bar, mimicking human behavior to bypass simple bot protection.
   - **Fetcher**: Identifies the primary product container in the DOM and aggressively strips out noise (headers, footers, javascript, tracking pixels) to massively shrink the HTML payload.
   - **Extractor**: Uses precise CSS selectors to parse product cards. It calculates string/token overlaps to dynamically filter out irrelevant search results on the fly.
5. **Normalizer Agent (`normalizer_agent.py`)**: The ultimate matching engine. Because vendors use messy variations of names (e.g. "LG 32 inch" vs "LG 80cms"), the Normalizer calculates AI embeddings wrapped in ChromaDB to verify if the scraped item definitively matches your internal catalog SKU.
6. **Analyst Agent (`analyst_agent.py`)**: Performs heavy statistical crunching on the matched records to identify price gaps, market rank, and flag severe anomalies (e.g., predatory competitor pricing).
7. **Pricing Agent (`pricing_agent.py`)**: Acts directly against your configured strategy (e.g., match lowest competitor, stay 2% above average) to generate discrete percentage adjustments and guaranteed mathematical margin safeguards.
8. **Reporter Agent (`reporter_agent.py`)**: Condenses the pipeline metrics into a clean, executive "Morning Briefing" summary for the human in the loop to review.

## Recent Architecture Upgrades
* **Robust Web Extraction**: Transitioned entirely away from expensive Vision AI scraping in favor of hyper-optimized Playwright DOM retrieval coupled with BeautifulSoup. This ensures lightning-fast extraction and bypasses cloud-model latency.
* **Component-Resigned Match Scoring**: Extensively patched complex extraction edge-cases (like Amazon heavily nesting or splitting product titles across multiple `<h2>` nodes or randomly swapping out their price CSS variables `a-offscreen`/`a-price-whole`) via robust text concatenation.
* **Granular Database Tracking**: Improved the competitor registry SQLite schema to properly separate and cache concurrent product mappings per domain via `(retailer_id, url, catalog_sku)` tracking so products never overwrite each other.
* **Decoupled Embedding Engine**: Resolved upstream API bugs with the Gemini endpoints (`models/gemini-embedding-001`) and decoupled the underlying embedding layer from the Chat layer. This allows the system to seamlessly route intensive vector computations through a free local Ollama server if the cloud API key fails, while keeping Gemini 2.5 strictly focused on high-intelligence logic.

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
| `agents/normalizer_agent.py` | LLM-based product matching via Chroma DB embeddings + LLM judgment fallbacks |
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
[planner]          ← Strategic target mapping
  │
  ▼
[scout]            ← Regional chain discovery 
  │
  ▼
[scraper]          ← Parallel Sub-graph (Navigator → Fetcher → Extractor)
  │
  ▼
[normalizer]       ← Cross-competitor SKU matching (Embeddings)
  │
  ▼
[analyst]          ← Competitive price analysis & alerts
  │
  ▼
[pricing]          ← Recommendation engine
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