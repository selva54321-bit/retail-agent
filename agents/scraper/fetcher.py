"""
agents/scraper/fetcher.py
==========================
Sub-agent 2 — Fetcher

Responsibility:
  Take the full rendered HTML from the Navigator.
  Strip nav, header, footer, sidebar, scripts, styles.
  Isolate the main product listing section.
  Return a focused, clean HTML slice for the Extractor.

Why this step exists:
  Amazon's full rendered HTML is 500–800 KB.
  Sending 800 KB to BeautifulSoup or an LLM wastes time and tokens.
  The fetcher reduces this to the 20–50 product card elements
  that actually matter — typically 30–80 KB.

Site-specific section selectors (in priority order):
  Amazon    → #search, #results, .s-search-results
  Flipkart  → [class*="search-"], [class*="product-grid"]
  Generic   → main, #main, [role="main"]
"""

from bs4 import BeautifulSoup, Tag
from agents.scraper.state import ScraperSubState


# ─────────────────────────────────────────────────────────────────
#  SITE-SPECIFIC SECTION SELECTORS
#  First match wins — each is the container holding all product cards.
# ─────────────────────────────────────────────────────────────────

SECTION_SELECTORS = [
    # Amazon India search results
    "#search",
    ".s-search-results",
    "[data-component-type='s-search-results']",

    # Flipkart search results
    "._1YokD2._3Mn1Gg",
    "[class*='search-result']",
    "[class*='product-grid']",

    # Poorvika (Next.js)
    "div[class*='productfifteen']",
    "div[class*='horizontal-scroll']",

    # Croma / Magento category
    ".category-products",
    ".products.wrapper",
    "ol.products",
    "ul.products-grid",

    # Reliance Digital
    "#product-list",
    "[class*='product-list']",

    # Tata Cliq
    "[class*='ResultContainer']",
    "[class*='SearchPage']",

    # Generic fallback
    "main",
    "#main",
    '[role="main"]',
    ".main-content",
]

# Tags to strip completely (they add noise, never contain product data)
STRIP_TAGS = [
    "script", "style", "noscript", "svg", "iframe",
    "nav", "footer", "header", "aside",
    "meta", "link", "head",
]

# Attribute-based noise patterns to remove
NOISE_SELECTORS = [
    "[class*='breadcrumb']",
    "[class*='pagination']",
    "[class*='ad-slot']",
    "[class*='banner']",
    "[class*='newsletter']",
    "[id*='cookie']",
    "[id*='consent']",
    "[class*='filter']",
    "[class*='sort-']",
]


# ─────────────────────────────────────────────────────────────────
#  FETCHER NODE
# ─────────────────────────────────────────────────────────────────

def run_fetcher(state: ScraperSubState) -> dict:
    """
    LangGraph sub-graph node: Fetcher.

    Slices the full page HTML down to just the product listing section.
    Returns partial state update: {dom_section}.
    """
    html = state.get("page_html", "")

    if not html:
        print(f"    [Fetcher] ✗ No HTML to process (navigator failed?)")
        return {
            "dom_section": "",
            "errors":      state.get("errors", []) + ["Fetcher: empty HTML from navigator"],
        }

    print(f"    [Fetcher] Processing {len(html):,} chars of HTML...")

    focused = _extract_product_section(html)

    if focused:
        print(f"    [Fetcher] ✓ Focused section: {len(focused):,} chars")
    else:
        # Fallback: strip noise from full HTML and pass the rest
        focused = _strip_noise(html)
        print(f"    [Fetcher] ↕ No section found — stripped noise: {len(focused):,} chars")

    return {"dom_section": focused}


def _extract_product_section(html: str) -> str:
    """
    Try each SECTION_SELECTORS in order.
    Return the innerHTML of the first matching element.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove noise tags first
    for tag in STRIP_TAGS:
        for el in soup.find_all(tag):
            el.decompose()

    for sel in SECTION_SELECTORS:
        try:
            section = soup.select_one(sel)
            if section and len(section.get_text(strip=True)) > 100:
                # Remove inline noise from within the section
                _clean_element(section)
                return str(section)
        except Exception:
            continue

    return ""


def _strip_noise(html: str) -> str:
    """
    Full-page fallback: strip tags and noise selectors, return cleaned HTML.
    Caps output at 150 KB to avoid sending too much to the extractor.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in STRIP_TAGS:
        for el in soup.find_all(tag):
            el.decompose()

    for sel in NOISE_SELECTORS:
        try:
            for el in soup.select(sel):
                el.decompose()
        except Exception:
            pass

    result = str(soup.body or soup)
    return result[:800_000]   # Increased to 800 KB cap to avoid truncating products


def _clean_element(el: Tag) -> None:
    """Remove noise selectors from within a section element."""
    for sel in NOISE_SELECTORS:
        try:
            for child in el.select(sel):
                child.decompose()
        except Exception:
            pass