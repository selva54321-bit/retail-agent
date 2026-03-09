"""
RetailAgent — Normalizer Agent
================================
Matches competitor product names to the retailer's catalog using:
  1. Exact / substring match (fastest, zero LLM cost)
  2. Embedding cosine similarity via nomic-embed-text (Ollama)
     or hash-based fallback if embedding model unavailable
  3. LLM judgment for gray-zone matches (0.55 – 0.80 similarity)

Confirmed mappings are cached in the DB so future cycles skip re-matching.
"""

import re
import json
from core.state import AgentState, ProductMatch
from core.llm import embed, cosine_similarity, chat_json, is_available
from core import database as db


# Similarity thresholds
EXACT_THRESHOLD     = 0.90
LLM_LOWER_THRESHOLD = 0.50
LLM_UPPER_THRESHOLD = 0.85


def _normalize_name(name: str) -> str:
    """Lowercase, remove punctuation and extra spaces for fuzzy matching."""
    name = name.lower()
    name = re.sub(r"[^\w\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _token_overlap(a: str, b: str) -> float:
    """Token Jaccard overlap as a fast pre-filter."""
    ta = set(_normalize_name(a).split())
    tb = set(_normalize_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _llm_judge_match(retailer_product: str, competitor_product: str,
                     similarity_score: float) -> bool:
    """Ask the LLM if two product names refer to the same product."""
    try:
        result = chat_json(
            "normalizer",
            "You are a product matching expert for retail. "
            "Determine if two product names refer to the same physical product. "
            "Return JSON: {\"is_same_product\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"brief\"}",
            f"Product A (retailer): {retailer_product}\n"
            f"Product B (competitor): {competitor_product}\n"
            f"Embedding similarity: {similarity_score:.2f}"
        )
        return bool(result.get("is_same_product", False))
    except Exception:
        # Conservative fallback: trust similarity score
        return similarity_score >= 0.70


def build_catalog_embeddings(catalog: list) -> list:
    """Pre-compute embeddings for all retailer catalog items."""
    print("[Normalizer] Computing catalog embeddings...")
    embedded = []
    for product in catalog:
        name = product.get("name", "")
        vec  = embed(name)
        embedded.append({
            **product,
            "embedding": vec,
            "normalized_name": _normalize_name(name),
        })
    print(f"[Normalizer] {len(embedded)} catalog embeddings ready.")
    return embedded


def match_product(competitor_product_name: str,
                  competitor_price: float,
                  competitor_name: str,
                  catalog_embedded: list,
                  retailer_id: int,
                  use_llm: bool = True) -> dict | None:
    """
    Find the best matching retailer catalog item for a competitor product.
    Returns a ProductMatch dict or None if no good match found.
    """
    comp_norm = _normalize_name(competitor_product_name)
    comp_vec  = embed(competitor_product_name)

    best_match  = None
    best_score  = 0.0
    best_item   = None

    for item in catalog_embedded:
        # Fast pre-filter: token overlap
        overlap = _token_overlap(competitor_product_name, item["name"])
        if overlap < 0.10:
            continue

        # Exact match shortcut
        if overlap > 0.80 or item["normalized_name"] in comp_norm or comp_norm in item["normalized_name"]:
            return _make_match(item, competitor_name, competitor_product_name,
                               competitor_price, 1.0, "exact")

        # Embedding similarity
        sim = cosine_similarity(comp_vec, item["embedding"])
        if sim > best_score:
            best_score = sim
            best_item  = item

    if best_item is None:
        return None

    # High-confidence embedding match
    if best_score >= EXACT_THRESHOLD:
        return _make_match(best_item, competitor_name, competitor_product_name,
                           competitor_price, best_score, "embedding")

    # Gray zone — ask LLM
    if LLM_LOWER_THRESHOLD <= best_score < EXACT_THRESHOLD and use_llm and is_available():
        is_match = _llm_judge_match(best_item["name"], competitor_product_name, best_score)
        if is_match:
            return _make_match(best_item, competitor_name, competitor_product_name,
                               competitor_price, best_score, "llm")

    return None


def _make_match(catalog_item: dict, competitor_name: str,
                competitor_product_name: str, competitor_price: float,
                similarity: float, method: str) -> dict:
    return {
        "retailer_sku":             catalog_item.get("sku", ""),
        "retailer_product_name":    catalog_item.get("name", ""),
        "competitor_name":          competitor_name,
        "competitor_product_name":  competitor_product_name,
        "competitor_price":         competitor_price,
        "similarity_score":         round(similarity, 4),
        "match_method":             method,
    }


def run_normalizer(state: AgentState, retailer_id: int) -> AgentState:
    """
    Matches all scraped competitor products to the retailer catalog.
    Saves confirmed mappings to DB. Updates state.product_matches.
    """
    print("\n[Normalizer] Matching competitor products to catalog...")

    scraped     = state.scraped_records
    catalog     = state.retailer_profile.catalog
    if not scraped or not catalog:
        print("[Normalizer] No data to match.")
        return state

    # Load cached mappings from previous cycles
    cached_raw = db.get_product_mappings(retailer_id)
    cached_keys = {
        (m["competitor_name"], m["competitor_product_name"]): m
        for m in cached_raw
    }

    # Build embeddings for catalog
    catalog_embedded = build_catalog_embeddings(catalog)

    matches = []
    new_matches = 0
    cached_hits = 0

    for record in scraped:
        comp_name    = record.get("competitor_name", "")
        comp_product = record.get("product_name_raw", "")
        comp_price   = float(record.get("price", 0))

        cache_key = (comp_name, comp_product)

        # Use cached mapping if available
        if cache_key in cached_keys:
            cached = cached_keys[cache_key]
            matches.append({
                **cached,
                "competitor_price": comp_price,   # update price
            })
            cached_hits += 1
            continue

        # Fresh match
        match = match_product(
            comp_product, comp_price, comp_name,
            catalog_embedded, retailer_id
        )
        if match:
            matches.append(match)
            db.save_product_mapping(retailer_id, match)
            new_matches += 1

    state.product_matches = matches
    print(f"[Normalizer] {len(matches)} matches total | "
          f"{new_matches} new | {cached_hits} from cache")

    # Report unmatched products
    matched_comp_products = {m["competitor_product_name"] for m in matches}
    total_scraped = len(scraped)
    unmatched = total_scraped - len(matched_comp_products)
    if unmatched > 0:
        print(f"[Normalizer] {unmatched} competitor products could not be matched to catalog.")

    return state