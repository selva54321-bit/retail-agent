"""
RetailAgent — LLM & Embeddings (Switchable: Gemini or Ollama)
==============================================================
Single place where all LLM and embedding models are configured.
Every agent imports get_llm() / get_embeddings() from here.

Edit the _config block below to set your model names and API key.
main.py calls set_provider("gemini") or set_provider("ollama") at
startup based on user choice — all agents pick it up automatically.

LangChain providers:
  Ollama  → ChatOllama                    (langchain-ollama)
  Gemini  → ChatGoogleGenerativeAI        (langchain-google-genai)

Embeddings:
  Ollama  → OllamaEmbeddings              (nomic-embed-text)
  Gemini  → GoogleGenerativeAIEmbeddings  (models/embedding-001)
  Fallback → hash-based pseudo-embedding  (no external deps)
"""

from __future__ import annotations
import math
import hashlib
import os
import requests
from pydantic import BaseModel
from langchain_core.output_parsers  import JsonOutputParser, StrOutputParser
from langchain_core.prompts         import ChatPromptTemplate
from langchain_core.language_models import BaseChatModel
from langchain_core.embeddings      import Embeddings


# ─────────────────────────────────────────────────────────────────
#  ★  CONFIGURE YOUR MODELS HERE  ★
#  Set your API key and preferred model names.
#  These are the only values you ever need to edit in this file.
# ─────────────────────────────────────────────────────────────────

_config: dict = {
    # Active provider — set by main.py at startup ("ollama" | "gemini")
    "provider": "ollama",

    # ── Gemini settings ──────────────────────────────────────────
    "gemini_api_key": os.environ.get("GOOGLE_API_KEY", "AIzaSyCLcAhoek7RMlD0zvPoUCwDj8gOGUvkdBw"),
    "gemini_model":   "gemini-2.5-flash",          # gemini-2.0-flash | gemini-1.5-pro | gemini-2.5-flash-lite

    # ── Ollama settings ──────────────────────────────────────────
    "ollama_model":   "qwen2.5:3b-instruct",        # any model from: ollama list
    "ollama_base_url": "http://localhost:11434",
    "embed_model":    "nomic-embed-text:latest",    # used for product matching

    # ── Generation settings ──────────────────────────────────────
    "temperature_low": 0.05,   # deterministic — structured JSON output
    "temperature_med": 0.30,   # slight creativity — briefing text
}


def set_provider(provider: str) -> None:
    """
    Called once at startup by main.py.
    Switches the active backend. All agents then use the new backend
    automatically — no other code needs to change.

    Args:
        provider: "gemini" or "ollama"
    """
    _config["provider"] = provider.lower().strip()
    print(f"  [LLM] Provider → {_config['provider'].upper()}  "
          f"(model: {get_active_model_name()})")


def get_active_provider() -> str:
    return _config["provider"]


def get_active_model_name() -> str:
    return _config["gemini_model"] if _config["provider"] == "gemini" else _config["ollama_model"]


# ─────────────────────────────────────────────────────────────────
#  LLM FACTORY
# ─────────────────────────────────────────────────────────────────

def get_llm(temperature: float | None = None) -> BaseChatModel:
    """
    Returns the active LLM as a LangChain BaseChatModel.
    All agents call this — never instantiate models directly.
    """
    temp = temperature if temperature is not None else _config["temperature_low"]
    if _config["provider"] == "gemini":
        return _get_gemini_llm(temp)
    return _get_ollama_llm(temp)


def _get_ollama_llm(temperature: float) -> BaseChatModel:
    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=_config["ollama_model"],
        temperature=temperature,
        num_predict=2048,
        base_url=_config["ollama_base_url"],
    )


def _get_gemini_llm(temperature: float) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=_config["gemini_model"],
        temperature=temperature,
        google_api_key=_config["gemini_api_key"],
        convert_system_message_to_human=True,  # Gemini requires this
    )


# ─────────────────────────────────────────────────────────────────
#  EMBEDDINGS FACTORY
# ─────────────────────────────────────────────────────────────────

def get_embeddings() -> Embeddings:
    """Returns the active embeddings model."""
    if _config["provider"] == "gemini":
        return _get_gemini_embeddings()
    return _get_ollama_embeddings()


def _get_ollama_embeddings() -> Embeddings:
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(
        model=_config["embed_model"],
        base_url=_config["ollama_base_url"],
    )


def _get_gemini_embeddings() -> Embeddings:
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    return GoogleGenerativeAIEmbeddings(
        model="models/embedding-001",
        google_api_key=_config["gemini_api_key"],
    )


# ─────────────────────────────────────────────────────────────────
#  LCEL CHAIN BUILDERS  —  provider-agnostic
# ─────────────────────────────────────────────────────────────────

def make_json_chain(system_prompt: str, human_template: str):
    """LCEL chain: prompt | llm | JsonOutputParser → dict"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt + "\n\nRespond ONLY with valid JSON. No markdown, no explanation."),
        ("human",  human_template),
    ])
    return prompt | get_llm(_config["temperature_low"]) | JsonOutputParser()


def make_str_chain(system_prompt: str, human_template: str):
    """LCEL chain: prompt | llm | StrOutputParser → str"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human",  human_template),
    ])
    return prompt | get_llm(_config["temperature_med"]) | StrOutputParser()


def make_pydantic_chain(system_prompt: str, human_template: str,
                        schema: type[BaseModel]):
    """LCEL chain: prompt | llm | PydanticOutputParser → Pydantic model"""
    from langchain.output_parsers import PydanticOutputParser
    parser = PydanticOutputParser(pydantic_object=schema)
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt + "\n\n{format_instructions}"),
        ("human",  human_template),
    ]).partial(format_instructions=parser.get_format_instructions())
    return prompt | get_llm(_config["temperature_low"]) | parser


# ─────────────────────────────────────────────────────────────────
#  EMBEDDING HELPERS
# ─────────────────────────────────────────────────────────────────

def embed_texts(texts: list[str]) -> list[list[float]]:
    try:
        return get_embeddings().embed_documents(texts)
    except Exception:
        return [_fallback_embed(t) for t in texts]


def embed_query(text: str) -> list[float]:
    try:
        return get_embeddings().embed_query(text)
    except Exception:
        return _fallback_embed(text)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _fallback_embed(text: str, dims: int = 256) -> list[float]:
    """Hash-based pseudo-embedding when no embedding model is available."""
    text   = text.lower().strip()
    vec    = [0.0] * dims
    tokens = text.split()
    ngrams = tokens + [tokens[i] + tokens[i + 1] for i in range(len(tokens) - 1)]
    for gram in ngrams:
        idx = int(hashlib.md5(gram.encode()).hexdigest(), 16) % dims
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ─────────────────────────────────────────────────────────────────
#  STATUS CHECKS
# ─────────────────────────────────────────────────────────────────

def check_ollama() -> dict:
    """Check Ollama availability and installed models."""
    try:
        r = requests.get(f"{_config['ollama_base_url']}/api/tags", timeout=3)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            return {
                "running":     True,
                "models":      models,
                "chat_ready":  any(_config["ollama_model"] in m for m in models),
                "embed_ready": any(_config["embed_model"]  in m for m in models),
            }
    except Exception:
        pass
    return {"running": False, "models": [], "chat_ready": False, "embed_ready": False}


def check_gemini() -> dict:
    """Validate the configured Gemini API key with a quick test call."""
    if not _config["gemini_api_key"]:
        return {"valid": False, "error": "No API key set in _config['gemini_api_key']"}
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm  = ChatGoogleGenerativeAI(
            model=_config["gemini_model"],
            google_api_key=_config["gemini_api_key"],
            temperature=0,
        )
        resp = llm.invoke("Reply with the single word: OK")
        ok   = "ok" in resp.content.lower()
        return {"valid": ok, "error": "" if ok else "Unexpected response"}
    except Exception as e:
        return {"valid": False, "error": str(e)}