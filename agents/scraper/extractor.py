"""
agents/scraper/extractor.py
============================
Sub-agent 3 — Extractor

Responsibility:
  Take the focused DOM section from the Fetcher.
  Find the product cards that match what was searched.
  Extract: product name, current price, original/struck-through price.
  Return top N results sorted by name-match confidence.

Two extraction paths (in order):
  Path A — BeautifulSoup (fast, deterministic)
    Site-specific card/name/price selectors for Amazon and Flipkart.
    Falls through to the generic ₹ scan if known selectors return nothing.

  Path B — Vision LLM on screenshot (fallback)
    If BeautifulSoup returns 0 results, send the screenshot PNG to the
    local vision model (llama3.2:1b). Works for any site regardless of
    DOM structure changes.

Product match verification:
  Every extracted result is scored against the searched product name
  using token overlap + sequence similarity + brand match.
  Results below MIN_CONF are dropped.
"""

import re
import base64
from difflib import SequenceMatcher
from typing  import Optional

from bs4 import BeautifulSoup
from langchain_core.messages import HumanMessage

from agents.scraper.state import ScraperSubState
from core.llm import get_vision_llm, call_with_retry


# ─────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────

TOP_N       = 3        # max results per search URL
MIN_PRICE   = 100      # ₹100 — filters out EMI / % values
MAX_PRICE   = 500_000  # ₹5 lakh
MIN_CONF    = 0.15     # drop results below this match confidence


# ─────────────────────────────────────────────────────────────────
#  SITE-SPECIFIC CARD SELECTORS
# ─────────────────────────────────────────────────────────────────

SITE_SELECTORS = {
    "amazon.in": {
        "card":     '[data-component-type="s-search-result"][data-asin][data-asin!=""]',
        "name":     "h2 a span, h2 span",
        "price":    "span.a-price:not(.a-text-price) span.a-offscreen",
        "original": "span.a-text-price span.a-offscreen, span.a-text-strike",
    },
    "flipkart.com": {
        "card":     "div[data-id]",
        "name":     "div.KzDlHZ, div._4rR01T, a.s1Q9rs, a.wjcEIp, div.RG5Slk",
        "price":    "div.Nx9bqj, div._30jeq3, div.hl05eU div.Nx9bqj, div.hZ3P6w, div.DeU9vF",
        "original": "div.yRaY8j, div._3I9_wc, div.kRYCnD, div.gxR4EY",
    },
    "poorvika.com": {
        "card":     "div[class*='productfifteen_card'], div[class*='product-cardlist_card']",
        "name":     "h3, b",
        "price":    "span[class*='productfifteen_pricedata'], span.whitespace-nowrap",
        "original": "",
    },
    "croma.com": {
        "card":     "li.product-item, div[class*='product-card']",
        "name":     "a.product-item-link, h3.product-title a, h2.product-title a",
        "price":    "span.amount, span[data-testid='new-price'], span.pdpPrice",
        "original": "span[data-testid='old-price'], span.old-price",
    },
}

# Generic fallback selectors for any unknown site
GENERIC = {
    "card":     "li.product-item, li.product, div.product-item-info, div[class*='product-card']",
    "name":     "a.product-item-link, h2.product-name a, h3, h2",
    "price":    "ins span.woocommerce-Price-amount bdi, span.price bdi, span.amount, span.pdpPrice",
    "original": "del span.woocommerce-Price-amount bdi, span.old-price bdi",
}


# ─────────────────────────────────────────────────────────────────
#  VISION LLM PROMPT
# ─────────────────────────────────────────────────────────────────

VISION_PROMPT = """You are reading a screenshot of an Indian e-commerce search results page.
The search was for: "{product_name}"

Look at the TOP 3 product listings visible on screen.
For each product extract:
  1. The exact product title/name shown
  2. The current selling price in ₹ (the prominent price, NOT struck-through)
  3. The original/MRP price only if a struck-through price is shown

Return ONLY valid JSON — no markdown, no explanation:
[
  {{"name": "product title here", "price": 45990, "original_price": 52000}},
  {{"name": "second product",      "price": 38999, "original_price": null}}
]

Rules:
- Prices as plain numbers only (no ₹ symbol)
- Only include listings where you can clearly see both name AND price
- If fewer than 3 are visible, return fewer items
"""


# ─────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────

def _get_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    clean = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        v = float(clean)
        return v if MIN_PRICE <= v <= MAX_PRICE else None
    except ValueError:
        return None


def _match_score(scraped: str, searched: str) -> float:
    """Token overlap + sequence similarity + brand bonus → 0.0–1.0."""
    if not scraped or not searched:
        return 0.0
    STOP = {"the","a","an","and","or","for","with","in","on","of",
            "to","at","from"}
    def tok(s):
        return {t for t in re.split(r'\W+', s.lower()) if t and t not in STOP and len(t) > 1}
    st, ht = tok(scraped), tok(searched)
    if not st or not ht:
        return 0.0
    overlap = len(st & ht) / max(len(st), len(ht))
    seq     = SequenceMatcher(None, scraped.lower(), searched.lower()).ratio()
    first   = lambda s: re.split(r'\W+', s.lower().strip())[0] if s.strip() else ""
    brand   = 0.2 if first(scraped) == first(searched) else 0.0
    return round(min(overlap * 0.5 + seq * 0.3 + brand, 1.0), 3)


def _build_records(raw: list, state: ScraperSubState, method: str) -> list[dict]:
    """Validate, score, and sort raw extracted results."""
    out = []
    for r in raw:
        name  = str(r.get("name", "")).strip()
        price = r.get("price")
        if not name or not price:
            continue
        p = _parse_price(str(price))
        if not p:
            continue
        conf = _match_score(name, state.get("catalog_product_name", ""))
        if conf < MIN_CONF:
            print(f"    [Extractor] ⚠ Low match ({conf:.2f}): '{name[:40]}'")
            continue
        orig = r.get("original_price")
        try:
            orig = float(orig) if orig and float(orig) > p else None
        except Exception:
            orig = None
        out.append({
            "name":           name,
            "price":          p,
            "original_price": orig,
            "confidence":     conf,
            "method":         method,
        })
    out.sort(key=lambda x: x["confidence"], reverse=True)
    return out[:TOP_N]


# ─────────────────────────────────────────────────────────────────
#  PATH A — BeautifulSoup extraction
# ─────────────────────────────────────────────────────────────────

def _bs4_extract(html: str, domain: str) -> list[dict]:
    """
    Try site-specific selectors, then generic fallback.
    Returns raw list of {name, price, original_price}.
    """
    soup = BeautifulSoup(html, "html.parser")
    sels = SITE_SELECTORS.get(domain, GENERIC)

    cards = soup.select(sels["card"])
    if not cards:
        cards = soup.select(GENERIC["card"])

    raw = []
    seen = set()

    for card in cards[:40]:
        # Name
        name_el = card.select_one(sels["name"]) or card.select_one(GENERIC["name"])
        if not name_el:
            continue
        name = (name_el.get("title") or
                name_el.get("aria-label") or
                name_el.get_text(separator=" ", strip=True))
        if not name or len(name) < 6:
            continue
        key = name[:40].lower()
        if key in seen:
            continue
        seen.add(key)

        # Price — skip elements inside del/strike
        price = None
        for sel in [sels["price"], GENERIC["price"]]:
            for el in card.select(sel):
                # Skip if inside a struck-through ancestor
                if any(p.name in ("del", "s") or
                       (p.get("class") and any("strike" in c or "original" in c
                                               for c in p.get("class", [])))
                       for p in el.parents):
                    continue
                price = _parse_price(el.get_text(strip=True))
                if price:
                    break
            if price:
                break

        if not price:
            continue

        # Original price
        orig = None
        orig_el = (card.select_one(sels.get("original", "")) or
                   card.select_one(GENERIC["original"]))
        if orig_el:
            orig = _parse_price(orig_el.get_text(strip=True))

        raw.append({"name": name, "price": price, "original_price": orig})

    return raw


# ─────────────────────────────────────────────────────────────────
#  PATH B — Vision LLM on screenshot
# ─────────────────────────────────────────────────────────────────

def _vision_extract(screenshot_png: bytes,
                    catalog_product_name: str) -> list[dict]:
    """
    Send the screenshot to the local vision model (llama3.2:1b via Ollama).
    Returns raw list of {name, price, original_price}.
    """
    import json as _json

    vision = get_vision_llm()
    if vision is None:
        raise RuntimeError("No vision model available")

    b64  = base64.b64encode(screenshot_png).decode("utf-8")
    msg  = HumanMessage(content=[
        {"type": "image_url",
         "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {"type": "text",
         "text": VISION_PROMPT.format(product_name=catalog_product_name)},
    ])

    def _invoke():
        resp = vision.invoke([msg])
        text = resp.content.strip()
        # Strip markdown fences if model wraps output
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return _json.loads(text)

    return call_with_retry(_invoke, max_retries=2)


# ─────────────────────────────────────────────────────────────────
#  EXTRACTOR NODE
# ─────────────────────────────────────────────────────────────────

def run_extractor(state: ScraperSubState) -> dict:
    """
    LangGraph sub-graph node: Extractor.

    Path A: BeautifulSoup on the focused DOM section.
    Path B: Vision LLM on the screenshot (fallback if A returns 0).

    Returns partial state update: {products}.
    """
    dom        = state.get("dom_section", "")
    screenshot = state.get("screenshot_png")
    url        = state.get("url", "")
    pname      = state.get("catalog_product_name", "")
    domain     = _get_domain(url)

    print(f"    [Extractor] Extracting from {domain} for '{pname[:40]}'")

    # ── Path A: BeautifulSoup ─────────────────────────────────
    if dom:
        raw     = _bs4_extract(dom, domain)
        records = _build_records(raw, state, method="bs4")
        if records:
            best = records[0]
            print(f"    [Extractor] ✓ BS4: ₹{best['price']:,.0f}  "
                  f"conf={best['confidence']:.2f}  '{best['name'][:45]}'")
            return {"products": records}
        print(f"    [Extractor] ↕ BS4 returned 0 valid results — trying vision")

    # ── Path B: Vision LLM ───────────────────────────────────
    if screenshot:
        try:
            raw     = _vision_extract(screenshot, pname)
            records = _build_records(raw, state, method="vision")
            if records:
                best = records[0]
                print(f"    [Extractor] ✓ Vision: ₹{best['price']:,.0f}  "
                      f"conf={best['confidence']:.2f}  '{best['name'][:45]}'")
                return {"products": records}
            print(f"    [Extractor] ✗ Vision returned 0 valid results")
        except Exception as e:
            print(f"    [Extractor] ✗ Vision failed: {str(e)[:70]}")
            return {
                "products": [],
                "errors": state.get("errors", []) + [f"Extractor vision: {e}"],
            }
    else:
        print(f"    [Extractor] ✗ No screenshot available for vision fallback")

    return {"products": []}