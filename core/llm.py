"""
RetailAgent — Ollama LLM Client
================================
Thin wrapper around the Ollama REST API.
All agents call this module for local LLM inference.
Supports: text generation, structured JSON output, embeddings.
"""

import json
import requests
import time
from typing import Optional

OLLAMA_BASE = "http://localhost:11434"

# Model assignments per task type
MODELS = {
    "planner":    "qwen3.5:4b",   # use 70b if available
    "pricing":    "qwen3.5:4b",
    "analyst":    "qwen3.5:4b",
    "reporter":   "qwen3.5:4b",
    "normalizer": "qwen3.5:4b",
    "intake":     "qwen3.5:4b",
    "selector":   "qwen3.5:4b",   # self-healing scraper
    "embedding":  "nomic-embed-text",
}


def _check_ollama() -> bool:
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def list_available_models() -> list:
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []


def _pick_best_model(role: str) -> str:
    """Pick the best available model for a role."""
    available = list_available_models()
    preferred = MODELS.get(role, "qwen3.5:4b")

    # Try exact match
    if preferred in available:
        return preferred

    # Try family match
    for m in available:
        if "llama3" in m or "llama3.1" in m or "mistral" in m:
            return m

    # Fallback to whatever is installed
    return available[0] if available else preferred


def chat(role: str, system_prompt: str, user_message: str,
         temperature: float = 0.1, max_retries: int = 2) -> str:
    """
    Send a chat completion request to Ollama.
    Returns the assistant's text response.
    """
    model = _pick_best_model(role)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": 2048,
        }
    }

    for attempt in range(max_retries + 1):
        try:
            r = requests.post(
                f"{OLLAMA_BASE}/api/chat",
                json=payload,
                timeout=120
            )
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Ollama is not running. Start it with: ollama serve"
            )
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2)
                continue
            raise RuntimeError(f"Ollama chat failed after {max_retries+1} attempts: {e}")


def chat_json(role: str, system_prompt: str, user_message: str,
              temperature: float = 0.05) -> dict:
    """
    Request structured JSON output from the LLM.
    Automatically strips markdown fences and parses.
    """
    system_with_json = (
        system_prompt
        + "\n\nIMPORTANT: Respond ONLY with valid JSON. "
          "No markdown fences, no explanation, no preamble. "
          "Just the raw JSON object."
    )

    raw = chat(role, system_with_json, user_message, temperature=temperature)

    # Strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON object from the text
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        raise ValueError(f"LLM returned invalid JSON: {raw[:300]}")


def embed(text: str) -> list:
    """
    Generate a text embedding using nomic-embed-text via Ollama.
    Returns a list of floats (768-dim vector).
    Falls back to a simple TF-IDF-style hash vector if model unavailable.
    """
    available = list_available_models()
    embed_model = MODELS["embedding"]

    if embed_model not in available:
        # Graceful fallback: hash-based pseudo-embedding
        return _fallback_embed(text)

    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": embed_model, "prompt": text},
            timeout=30
        )
        r.raise_for_status()
        return r.json()["embedding"]
    except Exception:
        return _fallback_embed(text)


def _fallback_embed(text: str) -> list:
    """
    Hash-based fallback embedding when nomic-embed-text is unavailable.
    Uses character n-gram hashing into a 256-dim vector.
    Not as good as a real embedding but enables the system to run
    without the embedding model installed.
    """
    import hashlib
    import math

    text = text.lower().strip()
    vec = [0.0] * 256

    # Unigrams and bigrams
    tokens = text.split()
    ngrams = tokens + [tokens[i] + tokens[i+1] for i in range(len(tokens)-1)]

    for gram in ngrams:
        h = int(hashlib.md5(gram.encode()).hexdigest(), 16)
        idx = h % 256
        vec[idx] += 1.0

    # L2 normalize
    norm = math.sqrt(sum(x*x for x in vec)) or 1.0
    return [x / norm for x in vec]


def cosine_similarity(vec_a: list, vec_b: list) -> float:
    """Cosine similarity between two vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def is_available() -> bool:
    return _check_ollama()


def status_report() -> dict:
    available = _check_ollama()
    models = list_available_models() if available else []
    return {
        "ollama_running": available,
        "models_installed": models,
        "embedding_available": MODELS["embedding"] in models,
        "chat_model": _pick_best_model("intake") if available else "none",
    }