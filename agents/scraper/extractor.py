"""
agents/scraper/extractor.py
============================
Sub-agent 3 — Extractor

Responsibility:
  Take the focused DOM section from the Fetcher.
  Scan ALL product cards (up to 20), extract name + price for each.
  Score every result against the searched product name.
  Return top N results sorted by confidence.

Extraction:
  BeautifulSoup with site-specific selectors for Amazon, Flipkart,
  Croma, Poorvika. Falls back to generic Magento/WooCommerce selectors
  for unknown sites.

Name extraction fix:
  select_one("h2 a span") returns the FIRST span — often a short brand
  badge ("XIAOMI", "Samsung"). _best_name_from_card() collects ALL spans
  inside h2 a and returns the LONGEST — which is always the full title.

Match scoring:
  Token overlap (50%) + sequence similarity (30%) + brand match (20%).
  Results below MIN_CONF are dropped with a debug log.
"""

import re
from difflib import SequenceMatcher
from typing  import Optional

from bs4 import BeautifulSoup

from agents.scraper.state import ScraperSubState


# ─────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────

TOP_N     = 3        # max results returned per search URL
MIN_PRICE = 100      # ₹100 — filters out EMI / percentage values
MAX_PRICE = 500_000  # ₹5 lakh
MIN_CONF  = 0.15     # drop results below this name-match confidence


# ─────────────────────────────────────────────────────────────────
#  SITE-SPECIFIC SELECTORS
# ─────────────────────────────────────────────────────────────────

SITE_SELECTORS: dict[str, dict] = {
    "amazon.in": {
        "card":     '[data-component-type="s-search-result"][data-asin][data-asin!=""]',
        "price":    "span.a-price:not(.a-text-price) span.a-offscreen",
        "original": "span.a-text-price span.a-offscreen, span.a-text-strike",
    },
    "flipkart.com": {
        "card":     "div[data-id]",
        "name":     "div.KzDlHZ, div._4rR01T, a.s1Q9rs, a.wjcEIp, div.RG5Slk",
        "price":    "div.Nx9bqj, div._30jeq3, div.hl05eU div.Nx9bqj, div.hZ3P6w, div.DeU9vF",
        "original": "div.yRaY8j, div._3I9_wc, div.kRYCnD, div.gxR4EY",
    },
    "croma.com": {
        "card":     "li.product-item, div[class*='product-card']",
        "name":     "a.product-item-link, h3.product-title a, h2.product-title a",
        "price":    "span.amount, span[data-testid='new-price'], span.pdpPrice",
        "original": "span[data-testid='old-price'], span.old-price",
    },
    "poorvika.com": {
        "card":     "div[class*='productfifteen_card'], div[class*='product-cardlist_card']",
        "name":     "h3, b",
        "price":    "span[class*='productfifteen_pricedata'], span.whitespace-nowrap",
        "original": "",
    },
    "sathya.in": {
        "card":     "div.art",
        "name":     ".art-name",
        "price":    ".art-price--val, .art-price",
        "original": ".art-price--old",
    },
    "reliancedigital.in": {
        "card":     "div.details-container",
        "name":     "p[class*='name']",
        "price":    "span.price, span.TextWeb__Text-sc-1cyx778-0",
        "original": "del, span[class*='old-price']",
    },
    "darlingretail.com": {
        "card":     "div.product-item",
        "name":     "a.product-item__title, a.product-item-meta__title",
        "price":    "span.price:not(.price--compare)",
        "original": "span.price--compare",
    },
    "vasanthandco.in": {
        "card":     "div.product",
        "name":     ".product-title, .product-name",
        "price":    ".new-price, .product-price ins, .product-price",
        "original": ".old-price, .product-price del",
    },
}

GENERIC: dict[str, str] = {
    "card":     "li.product-item, li.product, div.product-item-info, div[class*='product-card']",
    "name":     "a.product-item-link, h2.product-name a, h3, h2",
    "price":    "ins span.woocommerce-Price-amount bdi, span.price bdi, span.amount, span.pdpPrice",
    "original": "del span.woocommerce-Price-amount bdi, span.old-price bdi",
}


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
    """Token Jaccard overlap + sequence similarity + brand bonus → 0.0–1.0."""
    if not scraped or not searched:
        return 0.0
    STOP = {"the","a","an","and","or","for","with","in","on","of","to","at","from"}
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


# ─────────────────────────────────────────────────────────────────
#  NAME EXTRACTION — longest span wins
# ─────────────────────────────────────────────────────────────────

def _best_name_from_card(card, sels: dict) -> str:
    """
    Extract the full product title from a card.

    On Amazon, select_one("h2 a span") returns the FIRST span which is
    often a short brand badge ("XIAOMI", "Samsung"). The actual title is
    always in the LONGEST span inside h2 a.

    Priority:
      1. Longest span inside h2 anchor
      2. Anchor title/aria-label attribute
      3. Site-specific name selector
      4. a[title] anywhere in card
    """
    # Step 1 — all spans inside h2 anchor, pick longest
    h2 = card.find("h2")
    if h2:
        anchor = h2.find("a")
        if anchor:
            best = max(
                (sp.get_text(separator=" ", strip=True) for sp in anchor.find_all("span")),
                key=len, default=""
            )
            if len(best) >= 10:
                return best
            t = anchor.get("title") or anchor.get_text(separator=" ", strip=True)
            if t and len(t) >= 10:
                return t

    # Step 2 — site-specific name selector
    name_sel = sels.get("name", "")
    if name_sel:
        el = card.select_one(name_sel)
        if not el:
            el = card.select_one(GENERIC["name"])
        if el:
            t = (el.get("title") or el.get("aria-label") or
                 el.get_text(separator=" ", strip=True))
            if t and len(t) >= 10:
                return t

    # Step 3 — a[title] anywhere
    for a in card.select("a[title]"):
        t = a.get("title", "").strip()
        if len(t) >= 10:
            return t

    return ""


# ─────────────────────────────────────────────────────────────────
#  BS4 EXTRACTION
# ─────────────────────────────────────────────────────────────────

def _bs4_extract(html: str, domain: str, searched_name: str) -> list[dict]:
    """
    Scan up to 20 product cards, score each against searched_name,
    return top N sorted by confidence.
    """
    soup = BeautifulSoup(html, "html.parser")
    sels = SITE_SELECTORS.get(domain, GENERIC)

    cards = soup.select(sels["card"])
    if not cards:
        cards = soup.select(GENERIC["card"])

    candidates = []
    seen       = set()

    for card in cards[:20]:
        name = _best_name_from_card(card, sels)
        if not name or len(name) < 10:
            continue
        key = name[:50].lower()
        if key in seen:
            continue
        seen.add(key)

        # Price — skip strike-through ancestors
        price = None
        for sel_str in [sels.get("price", ""), GENERIC["price"]]:
            if not sel_str:
                continue
            for el in card.select(sel_str):
                if any(p.name in ("del", "s") or
                       (p.get("class") and any(
                           "strike" in c or "original" in c
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

        # Original / struck-through price
        orig = None
        for orig_sel in [sels.get("original", ""), GENERIC["original"]]:
            if not orig_sel:
                continue
            el = card.select_one(orig_sel)
            if el:
                orig = _parse_price(el.get_text(strip=True))
                if orig:
                    break

        score = _match_score(name, searched_name)
        candidates.append({
            "name":           name,
            "price":          price,
            "original_price": orig,
            "_score":         score,
        })

    candidates.sort(key=lambda x: x["_score"], reverse=True)
    return [{k: v for k, v in c.items() if k != "_score"} for c in candidates[:TOP_N]]


# ─────────────────────────────────────────────────────────────────
#  BUILD VALIDATED RECORDS
# ─────────────────────────────────────────────────────────────────

def _build_records(raw: list, state: ScraperSubState, method: str) -> list[dict]:
    """Validate prices, compute confidence scores, filter below MIN_CONF."""
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
#  EXTRACTOR NODE
# ─────────────────────────────────────────────────────────────────

def run_extractor(state: ScraperSubState) -> dict:
    """
    LangGraph sub-graph node: Extractor.
    Runs BeautifulSoup extraction on the focused DOM section.
    Returns partial state update: {products}.
    """
    dom    = state.get("dom_section", "")
    url    = state.get("url", "")
    pname  = state.get("catalog_product_name", "")
    domain = _get_domain(url)

    print(f"    [Extractor] Searching for '{pname[:45]}' on {domain}")

    if not dom:
        print(f"    [Extractor] ✗ No DOM section to parse")
        return {"products": []}

    raw     = _bs4_extract(dom, domain, pname)
    records = _build_records(raw, state, method="bs4")

    if records:
        best = records[0]
        print(f"    [Extractor] ✓ ₹{best['price']:,.0f}  "
              f"conf={best['confidence']:.2f}  '{best['name'][:50]}'")
        return {"products": records}

    # Debug: show best candidate even if below threshold
    if raw:
        top = max(raw, key=lambda x: _match_score(x["name"], pname))
        print(f"    [Extractor] ↕ Best candidate: '{top['name'][:50]}' "
              f"conf={_match_score(top['name'], pname):.2f} — below threshold ({MIN_CONF})")
    else:
        print(f"    [Extractor] ↕ 0 product cards with valid prices found")

    return {"products": []}