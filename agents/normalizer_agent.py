"""
RetailAgent — Normalizer Agent (LangChain Embeddings + ChromaDB)
=================================================================
LangChain patterns used:
  - OllamaEmbeddings            → embed product names via nomic-embed-text
  - Chroma (LangChain wrapper)  → vector store for catalog embeddings
  - similarity_search_with_score→ cosine similarity search against catalog
  - LCEL chain                  → LLM gray-zone judgment chain
  - Document                    → LangChain Document wrapping catalog items

Flow:
  1. Catalog items → OllamaEmbeddings → Chroma vector store
  2. Each scraped product → embed → similarity_search_with_score
  3. Score > 0.85 → auto-match
  4. Score 0.55-0.85 → LCEL LLM judgment chain
  5. Score < 0.55 → no match, flag for review
  6. All confirmed matches cached in SQLite ProductMappings table
"""

import re
from langchain_community.vectorstores import Chroma
from langchain_core.documents          import Document
from langchain_core.prompts            import ChatPromptTemplate
from langchain_core.output_parsers     import JsonOutputParser

from core.state import AgentState
from core.llm   import get_llm, get_embeddings, embed_query, cosine_similarity
from core       import database as db


# Similarity thresholds
AUTO_MATCH_THRESHOLD = 0.85
LLM_LOWER_THRESHOLD  = 0.55


# ─── LLM judgment chain (LCEL) ───────────────────────────────────

def _build_judgment_chain():
    """
    LCEL chain for gray-zone product matching.
    prompt | llm | json_parser
    Returns: {"is_same_product": bool, "confidence": float, "reason": str}
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a product matching expert for retail. "
         "Determine if two product descriptions refer to the same physical product. "
         "Consider model numbers, sizes, and specifications carefully. "
         "Return ONLY JSON: {\"is_same_product\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"brief\"}"),
        ("human",
         "Product A (retailer catalog): {product_a}\n"
         "Product B (competitor page):  {product_b}\n"
         "Embedding similarity score:   {similarity:.2f}\n\n"
         "Are these the same product?"),
    ])
    return prompt | get_llm(temperature=0.05) | JsonOutputParser()


# ─── Chroma vector store builder ─────────────────────────────────

def _build_catalog_vectorstore(catalog: list, retailer_id: int) -> Chroma:
    """
    Embed the retailer's catalog into a Chroma vector store.
    Uses get_embeddings() from core/llm.py — automatically picks
    the active backend (Ollama nomic-embed-text or Gemini embedding-001).

    LangChain pattern:
        Documents → get_embeddings() → Chroma.from_documents()
    """
    docs = [
        Document(
            page_content=item["name"],
            metadata={"sku": item["sku"], "name": item["name"],
                      "current_price": item.get("current_price", 0),
                      "cost": item.get("cost", 0)},
        )
        for item in catalog
    ]

    try:
        vectorstore = Chroma.from_documents(
            documents       = docs,
            embedding       = get_embeddings(),   # ← provider-aware, from core/llm.py
            collection_name = f"catalog_{retailer_id}",
        )
        print(f"  [Normalizer] Chroma vectorstore built ({len(docs)} items).")
        return vectorstore

    except Exception as e:
        print(f"  [Normalizer] Chroma/embeddings unavailable ({e}), using fallback.")
        return None


def _normalize(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _token_overlap(a: str, b: str) -> float:
    ta = set(_normalize(a).split())
    tb = set(_normalize(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _fallback_match(comp_name: str, comp_price: float, comp_competitor: str,
                    catalog: list) -> dict | None:
    """
    Hash-embedding fallback when Chroma/Ollama unavailable.
    Uses cosine_similarity on local hash vectors.
    """
    comp_vec = embed_query(comp_name)
    best_sim, best_item = 0.0, None

    for item in catalog:
        if _token_overlap(comp_name, item["name"]) < 0.10:
            continue
        item_vec = embed_query(item["name"])
        sim = cosine_similarity(comp_vec, item_vec)
        if sim > best_sim:
            best_sim, best_item = sim, item

    if best_item and best_sim >= AUTO_MATCH_THRESHOLD:
        return {
            "retailer_sku":            best_item["sku"],
            "retailer_product_name":   best_item["name"],
            "competitor_name":         comp_competitor,
            "competitor_product_name": comp_name,
            "competitor_price":        comp_price,
            "similarity_score":        round(best_sim, 4),
            "match_method":            "fallback_embedding",
        }
    return None


# ─── LangGraph node ───────────────────────────────────────────────

def run_normalizer_node(state: AgentState) -> dict:
    """
    LangGraph node: Normalizer Agent.
    Matches competitor product names to the retailer catalog.
    Uses Chroma similarity search + LLM judgment for gray-zone cases.
    Returns partial state update with product_matches.
    """
    scraped     = state["scraped_records"]
    catalog     = state["retailer_profile"].catalog
    retailer_id = state["retailer_id"]

    print(f"\n[Normalizer] Matching {len(scraped)} scraped products to catalog...")

    if not scraped or not catalog:
        return {"product_matches": [], "current_node": "normalizer"}

    # Load cached mappings — skip re-embedding for known pairs
    cached_raw   = db.get_product_mappings(retailer_id)
    cache_lookup = {
        (m["competitor_name"], m["competitor_product_name"]): m
        for m in cached_raw
    }

    # Build vectorstore
    vectorstore  = _build_catalog_vectorstore(catalog, retailer_id)
    judgment_chain = _build_judgment_chain()
    matches      = []
    new_ct, cached_ct = 0, 0

    for record in scraped:
        comp_name    = record.get("competitor_name", "")
        comp_product = record.get("product_name_raw", "")
        comp_price   = float(record.get("price", 0))
        cache_key    = (comp_name, comp_product)

        # ── Cache hit ──────────────────────────────────────────
        if cache_key in cache_lookup:
            cached = dict(cache_lookup[cache_key])
            cached["competitor_price"] = comp_price
            matches.append(cached)
            cached_ct += 1
            continue

        # ── Chroma similarity search ────────────────────────────
        match = None

        if vectorstore:
            try:
                results = vectorstore.similarity_search_with_score(comp_product, k=1)
                if results:
                    doc, distance = results[0]
                    # Chroma returns L2 distance; convert to similarity
                    similarity = 1.0 / (1.0 + distance)

                    if similarity >= AUTO_MATCH_THRESHOLD:
                        match = {
                            "retailer_sku":            doc.metadata["sku"],
                            "retailer_product_name":   doc.metadata["name"],
                            "competitor_name":         comp_name,
                            "competitor_product_name": comp_product,
                            "competitor_price":        comp_price,
                            "similarity_score":        round(similarity, 4),
                            "match_method":            "chroma_embedding",
                        }
                    elif similarity >= LLM_LOWER_THRESHOLD:
                        # Gray zone — LLM judgment
                        try:
                            judgment = judgment_chain.invoke({
                                "product_a":  doc.metadata["name"],
                                "product_b":  comp_product,
                                "similarity": similarity,
                            })
                            if judgment.get("is_same_product"):
                                match = {
                                    "retailer_sku":            doc.metadata["sku"],
                                    "retailer_product_name":   doc.metadata["name"],
                                    "competitor_name":         comp_name,
                                    "competitor_product_name": comp_product,
                                    "competitor_price":        comp_price,
                                    "similarity_score":        round(similarity, 4),
                                    "match_method":            "llm_judgment",
                                }
                        except Exception:
                            pass
            except Exception as e:
                print(f"  [Normalizer] Chroma search failed: {e}")
                vectorstore = None

        # ── Fallback path ──────────────────────────────────────
        if match is None and vectorstore is None:
            match = _fallback_match(comp_product, comp_price, comp_name, catalog)

        if match:
            matches.append(match)
            db.save_product_mapping(retailer_id, match)
            new_ct += 1

    print(f"[Normalizer] {len(matches)} matches | {new_ct} new | {cached_ct} cached")
    unmatched = len(scraped) - len({m["competitor_product_name"] for m in matches})
    if unmatched > 0:
        print(f"  {unmatched} products could not be matched (flagged for review)")

    return {
        "product_matches": matches,
        "current_node":    "normalizer",
    }