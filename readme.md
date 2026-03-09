# RetailAgent — Automated Competitor Price Monitoring
**Multi-Agent System | Python | LangGraph-style | Ollama**

---

## Project Structure

```
retailagent/
├── main.py                        ← Entry point (run this)
├── core/
│   ├── state.py                   ← Shared AgentState & all data schemas
│   ├── database.py                ← SQLite persistence layer
│   ├── llm.py                     ← Ollama client (chat, JSON, embeddings)
│   ├── graph.py                   ← LangGraph-style state machine orchestrator
│   └── dashboard.py               ← Terminal dashboard (colored CLI output)
└── agents/
    ├── intake_agent.py            ← Conversational onboarding
    ├── planner_agent.py           ← Strategy decomposition → execution plan
    ├── scraper_agent.py           ← Web scraping + simulation fallback
    ├── normalizer_agent.py        ← Semantic product matching (embeddings)
    ├── analyst_agent.py           ← Price analytics, trends, anomaly detection
    ├── pricing_agent.py           ← LLM-powered recommendations + guardrails
    └── reporter_agent.py          ← Natural language morning briefing
```

---

## Quick Start

### Option 1: Demo Mode (no setup required)
```bash
cd retailagent
python main.py --demo
```
Runs a full cycle with the demo electronics store profile.
All competitor prices are realistically simulated.

### Option 2: With Ollama (full LLM features)
```bash
# Install Ollama: https://ollama.ai
ollama pull llama3.1          # Chat model (8B works, 70B is better)
ollama pull nomic-embed-text  # Embedding model for product matching

cd retailagent
python main.py --demo         # or: python main.py (interactive onboarding)
```

### Option 3: Multiple cycles (to see trend detection)
```bash
python main.py --demo --cycles 3
```

### Check system status
```bash
python main.py --check
```

---

## How It Works

```
Retailer Profile (SQLite)
        ↓
   Planner Agent    → Builds execution plan (LLM or rule-based)
        ↓
   Scraper Agents   → Parallel per competitor (live scraping or simulated)
        ↓
   Normalizer Agent → Semantic product matching (embeddings + LLM)
        ↓
   Analyst Agent    → Price ranking, gap analysis, trends, anomaly detection
        ↓
   Pricing Agent    → LLM recommendations + guardrail rules engine
        ↓
   Reporter Agent   → Plain-English morning briefing
        ↓
   Dashboard        → Colored terminal output (price table, alerts, recs)
```

---

## Fallback Behavior (no Ollama / no internet)

| Feature              | With Ollama | Without Ollama |
|----------------------|-------------|----------------|
| Onboarding parsing   | LLM         | Direct input   |
| Planning strategy    | LLM         | Rule-based     |
| Competitor discovery | LLM search  | Preconfigured  |
| Live web scraping    | Playwright  | Simulated      |
| Product matching     | nomic-embed | Hash embedding |
| Price recommendations| LLM         | Rule-based     |
| Morning briefing     | LLM         | Template       |

The system runs fully end-to-end even without Ollama or internet.

---

## Requirements

```
Python 3.10+
beautifulsoup4
playwright   (pip install playwright && playwright install chromium)
pandas
numpy
scipy
scikit-learn
requests
```

All available in standard pip. No LangChain, no LangGraph package required —
the state machine is implemented in pure Python in `core/graph.py`.