# RetailAgent — LangChain + LangGraph Edition

## Quick Start
```bash
pip install -r requirements.txt
playwright install chromium
ollama pull llama3.1
ollama pull nomic-embed-text
python main.py --demo
```

## LangChain / LangGraph Patterns Used Per File

| File | LangChain/LangGraph Pattern |
|------|-----------------------------|
| `core/state.py` | `TypedDict` + `Annotated[list, operator.add]` for LangGraph state merging |
| `core/llm.py` | `ChatOllama`, `OllamaEmbeddings`, `make_json_chain()` via LCEL `|` operator |
| `core/graph.py` | `StateGraph`, `add_node`, `add_conditional_edges`, `MemorySaver`, `START/END` |
| `agents/intake_agent.py` | `ConversationChain`, `ConversationBufferMemory`, `MessagesPlaceholder` |
| `agents/planner_agent.py` | LCEL chain: `ChatPromptTemplate | ChatOllama | JsonOutputParser` |
| `agents/scraper_agent.py` | `@tool` decorator, LCEL self-healing selector chain |
| `agents/normalizer_agent.py` | `OllamaEmbeddings`, `Chroma.from_documents()`, `similarity_search_with_score` |
| `agents/analyst_agent.py` | `RunnableLambda` pipeline composed with `|` operator |
| `agents/pricing_agent.py` | `llm.with_structured_output(PydanticSchema)`, `RunnableLambda` guardrails |
| `agents/reporter_agent.py` | `load_summarize_chain`, `StuffDocumentsChain`, `Document` |

## Graph Structure
```
START
  │
  ├─► [intake]     ← ConversationChain + memory (if onboarding needed)
  │
  ▼
[planner]          ← LCEL: prompt | llm | JsonOutputParser
  │
  ▼
[scraper]          ← @tool + parallel ThreadPoolExecutor + self-healing LCEL
  │
  ▼
[normalizer]       ← Chroma vectorstore + similarity_search_with_score + LLM judgment
  │
  ▼
[analyst]          ← RunnableLambda pipeline (rank → trend → anomaly → alerts)
  │
  ▼
[pricing]          ← llm.with_structured_output() + RunnableLambda guardrails
  │
  ├─► [auto_apply]    (if auto_apply_prices=True)
  └─► [queue_review]  (human-in-the-loop pause)
  │
  ▼
[reporter]         ← load_summarize_chain (StuffDocumentsChain)
  │
  ▼
[cycle_log] ──► END
```