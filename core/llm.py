"""
RetailAgent — LLM & Embeddings (Gemini | Ollama | Grok)
=========================================================
Single place for all LLM and embedding configuration.
Every agent imports get_llm() / get_embeddings() from here.

Chat providers:
  Gemini → ChatGoogleGenerativeAI   (langchain-google-genai)
  Ollama → ChatOllama               (langchain-ollama)
  Grok   → ChatOpenAI at xAI URL   (langchain-openai)

Embeddings:
  Gemini → GoogleGenerativeAIEmbeddings (text-embedding-004)
  Ollama → OllamaEmbeddings (nomic-embed-text)
  Grok   → falls back to Ollama or hash (no xAI embeddings API yet)
  Fallback → hash-based pseudo-embedding (no external deps)
"""

from __future__ import annotations
import math
import hashlib
import os
import time
import requests
from pydantic import BaseModel
from langchain_core.output_parsers  import JsonOutputParser, StrOutputParser
from langchain_core.prompts         import ChatPromptTemplate
from langchain_core.language_models import BaseChatModel
from langchain_core.embeddings      import Embeddings


# ─────────────────────────────────────────────────────────────────
#  ★  CONFIGURE YOUR MODELS HERE  ★
# ─────────────────────────────────────────────────────────────────

_config: dict = {
    # Active provider — set by main.py at startup
    # Active provider — set by main.py at startup
    # Choices: "gemini" | "ollama" | "grok"
    "provider": "gemini",

    # ── Gemini ────────────────────────────────────────────────────
    "gemini_api_key": os.environ.get("GOOGLE_API_KEY", "AIzaSyBcSFLrsFiW7KWbau4HoiTui-QEmOFrVG8"),
    "gemini_model":   "gemini-2.5-flash-lite",

    # ── Ollama (local) ────────────────────────────────────────────
    "ollama_model":    "qwen2:1.5b",
    "ollama_base_url": "http://localhost:11434",
    "embed_model":     "nomic-embed-text:latest",

    # ── Grok (xAI) ───────────────────────────────────────────────
    "grok_api_key":  os.environ.get("XAI_API_KEY", ""),
    "grok_model":    "grok-3-mini",
    "grok_base_url": "https://api.x.ai/v1",

    # ── Generation settings ───────────────────────────────────────
    "temperature_low": 0.05,
    "temperature_med": 0.30,
}


# ─────────────────────────────────────────────────────────────────
#  RETRY WRAPPER
# ─────────────────────────────────────────────────────────────────

def call_with_retry(fn, *args, max_retries: int = 3, **kwargs):
    """
    Call fn(*args, **kwargs) with exponential back-off on rate-limit errors.
    Works for any LangChain chain invoke or plain callable.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            err = str(e)
            is_rate = any(k in err for k in ("429", "RESOURCE_EXHAUSTED",
                                              "rate_limit", "quota", "RateLimitError"))
            if attempt < max_retries:
                wait = 2 ** attempt * 3   # 6s, 12s, 24s
                print(f"    [Retry] Attempt {attempt}/{max_retries} failed: {err[:80]}")
                if is_rate:
                    print(f"    [Retry] Rate limit — waiting {wait}s...")
                    time.sleep(wait)
                else:
                    time.sleep(2)
                continue
            raise


# ─────────────────────────────────────────────────────────────────
#  PROVIDER SWITCH
# ─────────────────────────────────────────────────────────────────

def set_provider(provider: str) -> None:
    _config["provider"] = provider.lower().strip()
    print(f"  [LLM] Provider → {_config['provider'].upper()}  "
          f"(model: {get_active_model_name()})")


def get_active_provider() -> str:
    return _config["provider"]


def get_active_model_name() -> str:
    p = _config["provider"]
    if p == "gemini": return _config["gemini_model"]
    if p == "grok":   return _config["grok_model"]
    return _config["ollama_model"]


# ─────────────────────────────────────────────────────────────────
#  CHAT LLM FACTORY
# ─────────────────────────────────────────────────────────────────

def get_llm(temperature: float | None = None) -> BaseChatModel:
    """Returns the active chat LLM. All agents call this."""
    temp = temperature if temperature is not None else _config["temperature_low"]
    p    = _config["provider"]
    if p == "gemini": return _get_gemini_llm(temp)
    if p == "grok":   return _get_grok_llm(temp)
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
        convert_system_message_to_human=True,
    )


def _get_grok_llm(temperature: float) -> BaseChatModel:
    """Grok uses xAI's OpenAI-compatible endpoint via ChatOpenAI."""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=_config["grok_model"],
        temperature=temperature,
        api_key=_config["grok_api_key"],
        base_url=_config["grok_base_url"],
        max_tokens=2048,
    )


# ─────────────────────────────────────────────────────────────────
#  EMBEDDINGS FACTORY
# ─────────────────────────────────────────────────────────────────

def get_embeddings() -> Embeddings:
    """Returns the active embeddings model."""
    p = _config["provider"]
    if p == "gemini": return _get_gemini_embeddings()
    if p == "grok":   return _get_grok_embeddings()
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
        model="models/gemini-embedding-001",
        google_api_key=_config["gemini_api_key"],
    )


def _get_grok_embeddings() -> Embeddings:
    """
    xAI has no embeddings API yet.
    Try Ollama nomic-embed first, fall back to hash-based vectors.
    """
    try:
        r = requests.get(f"{_config['ollama_base_url']}/api/tags", timeout=2)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            if any("nomic" in m or "embed" in m for m in models):
                from langchain_ollama import OllamaEmbeddings
                return OllamaEmbeddings(
                    model=_config["embed_model"],
                    base_url=_config["ollama_base_url"],
                )
    except Exception:
        pass
    return _HashEmbeddings()


class _HashEmbeddings(Embeddings):
    """Hash-based pseudo-embeddings — no external dependencies."""
    def embed_documents(self, texts):
        return [_fallback_embed(t) for t in texts]
    def embed_query(self, text):
        return _fallback_embed(text)


# ─────────────────────────────────────────────────────────────────
#  LCEL CHAIN BUILDERS  —  provider-agnostic
# ─────────────────────────────────────────────────────────────────

def make_json_chain(system_prompt: str, human_template: str):
    """LCEL: prompt | llm | JsonOutputParser → dict"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt + "\n\nRespond ONLY with valid JSON. No markdown, no explanation."),
        ("human",  human_template),
    ])
    return prompt | get_llm(_config["temperature_low"]) | JsonOutputParser()


def make_str_chain(system_prompt: str, human_template: str):
    """LCEL: prompt | llm | StrOutputParser → str"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human",  human_template),
    ])
    return prompt | get_llm(_config["temperature_med"]) | StrOutputParser()


def make_pydantic_chain(system_prompt: str, human_template: str,
                        schema: type[BaseModel]):
    """LCEL: prompt | llm | PydanticOutputParser → Pydantic model"""
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
    """Hash-based pseudo-embedding when no model is available."""
    text   = text.lower().strip()
    vec    = [0.0] * dims
    tokens = text.split()
    ngrams = tokens + [tokens[i] + tokens[i+1] for i in range(len(tokens)-1)]
    for gram in ngrams:
        idx = int(hashlib.md5(gram.encode()).hexdigest(), 16) % dims
        vec[idx] += 1.0
    norm = math.sqrt(sum(x*x for x in vec)) or 1.0
    return [x / norm for x in vec]


# ─────────────────────────────────────────────────────────────────
#  STATUS CHECKS
# ─────────────────────────────────────────────────────────────────

def check_ollama() -> dict:
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
    if not _config["gemini_api_key"]:
        return {"valid": False, "error": "No API key set"}
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


def check_grok() -> dict:
    if not _config["grok_api_key"]:
        return {"valid": False, "error": "No API key — set XAI_API_KEY env var"}
    try:
        from langchain_openai import ChatOpenAI
        llm  = ChatOpenAI(
            model=_config["grok_model"],
            api_key=_config["grok_api_key"],
            base_url=_config["grok_base_url"],
            max_tokens=10,
            temperature=0,
        )
        resp = llm.invoke("Reply with the single word: OK")
        ok   = "ok" in resp.content.lower()
        return {"valid": ok, "error": "" if ok else "Unexpected response"}
    except Exception as e:
        return {"valid": False, "error": str(e)}