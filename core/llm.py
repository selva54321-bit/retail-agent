"""
RetailAgent — LLM & Embeddings (LangChain-Ollama)
===================================================
Single place where all LLM and embedding models are configured.
Every agent imports from here — never instantiates models directly.

LangChain patterns used:
  - ChatOllama             → local LLM via Ollama
  - OllamaEmbeddings       → nomic-embed-text for product matching
  - JsonOutputParser       → structured JSON from LLM responses
  - PydanticOutputParser   → Pydantic model from LLM responses
  - PromptTemplate         → reusable prompt templates
  - RunnableSequence (|)   → LCEL chains connecting prompt → model → parser
"""

from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda, Runnable
from pydantic import BaseModel
import math, hashlib


# ─── Model names ────────────────────────────────────────────────
CHAT_MODEL      = "qwen2.5:3b-instruct"        # swap for llama3.1:70b if available #qwen3.5:4b
EMBED_MODEL     = "nomic-embed-text:latest"
TEMPERATURE_LOW = 0.05              # deterministic for structured output
TEMPERATURE_MED = 0.3               # slight creativity for briefings


# ─── LLM instances (shared, lazy-loaded) ────────────────────────
def get_llm(temperature: float = TEMPERATURE_LOW) -> ChatOllama:
    """
    Returns a ChatOllama instance.
    LangChain's ChatOllama speaks the /api/chat Ollama endpoint.
    """
    return ChatOllama(
        model=CHAT_MODEL,
        temperature=temperature,
        num_predict=2048,
        base_url="http://localhost:11434",
    )

def get_embeddings() -> OllamaEmbeddings:
    """
    Returns an OllamaEmbeddings instance using nomic-embed-text.
    Used by the Normalizer Agent for semantic product matching.
    """
    return OllamaEmbeddings(
        model=EMBED_MODEL,
        base_url="http://localhost:11434",
    )


# ─── LCEL chain builders ─────────────────────────────────────────

def make_json_chain(system_prompt: str, human_template: str) -> Runnable:
    """
    Build an LCEL chain: prompt | llm | json_parser
    Returns structured dict from LLM response.

    Usage:
        chain = make_json_chain(SYSTEM, "{product} {prices}")
        result = chain.invoke({"product": "...", "prices": "..."})
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt + "\n\nRespond ONLY with valid JSON. No markdown, no explanation."),
        ("human",  human_template),
    ])
    return prompt | get_llm(TEMPERATURE_LOW) | JsonOutputParser()


def make_str_chain(system_prompt: str, human_template: str) -> Runnable:
    """
    Build an LCEL chain: prompt | llm | str_parser
    Returns plain text from LLM response.
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human",  human_template),
    ])
    return prompt | get_llm(TEMPERATURE_MED) | StrOutputParser()


def make_pydantic_chain(system_prompt: str, human_template: str,
                        schema: type[BaseModel]) -> Runnable:
    """
    Build an LCEL chain that parses output into a Pydantic model.
    Uses with_structured_output() which is the modern approach in LangChain.
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human",  human_template),
    ])
    return prompt | get_llm(TEMPERATURE_LOW).with_structured_output(schema)


# ─── Embedding helpers ───────────────────────────────────────────

def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of texts using OllamaEmbeddings.
    Falls back to hash-based pseudo-embedding if Ollama unavailable.
    """
    try:
        embedder = get_embeddings()
        return embedder.embed_documents(texts)
    except Exception:
        return [_fallback_embed(t) for t in texts]


def embed_query(text: str) -> list[float]:
    """Embed a single query text."""
    try:
        embedder = get_embeddings()
        return embedder.embed_query(text)
    except Exception:
        return _fallback_embed(text)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot   = sum(x * y for x, y in zip(a, b))
    na    = math.sqrt(sum(x * x for x in a))
    nb    = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _fallback_embed(text: str, dims: int = 256) -> list[float]:
    """Hash-based fallback embedding when nomic-embed-text is unavailable."""
    text   = text.lower().strip()
    vec    = [0.0] * dims
    tokens = text.split()
    ngrams = tokens + [tokens[i] + tokens[i+1] for i in range(len(tokens) - 1)]
    for gram in ngrams:
        idx = int(hashlib.md5(gram.encode()).hexdigest(), 16) % dims
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ─── Ollama availability check ───────────────────────────────────

def check_ollama() -> dict:
    """Check which Ollama models are available."""
    import requests
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=3)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            return {
                "running": True,
                "models": models,
                "chat_ready": any(CHAT_MODEL in m for m in models),
                "embed_ready": any(EMBED_MODEL in m for m in models),
            }
    except Exception:
        pass
    return {"running": False, "models": [], "chat_ready": False, "embed_ready": False}