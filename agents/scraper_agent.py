"""
RetailAgent — Scraper Agent (JS-Based DOM Extraction)
======================================================
The approach: after Playwright renders a search results page, we run
JavaScript inside the browser to find ALL elements containing '₹',
walk up the DOM to identify the product card, then pull the product
name from the nearest h2/h3/a inside that card.

This is robust across Amazon, Flipkart, Croma, Reliance, and local
sites because it does NOT depend on class names — it depends only on
the presence of ₹ text which every Indian e-commerce site uses.

Three wait strategies to handle bot-check pages:
  1. Wait for DOMContentLoaded + wait for first ₹ symbol (up to 12s)
  2. Slow scroll to trigger lazy loading, then re-check
  3. Stealth context (hides webdriver flag, randomises fingerprint)

Only top N results are returned (configurable, default 3) since the
search URL already encodes the exact product name — the top results
are the right product.

LangChain patterns:
  - @tool  → scrape_static_html / scrape_dynamic_page as LangChain Tools
  - LCEL   → DOM snapshot → prompt | llm → fallback selector chain
"""

import json
import random
import re
import time
import socket
import concurrent.futures
from datetime  import datetime
from typing    import Optional

import requests
from bs4       import BeautifulSoup

from langchain_core.tools          import tool
from langchain_core.prompts        import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from core.state import AgentState
from core.llm   import get_llm
from core       import database as db


# ─────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────

TOP_N_RESULTS = 3   # take top N results from each search page

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

REQUEST_HEADERS = {
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9,ta;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Cache-Control":   "max-age=0",
}


# ─────────────────────────────────────────────────────────────────
#  JAVASCRIPT INJECTED INTO THE BROWSER
#  Finds all ₹ price elements, walks up to product card, pulls name.
#  Returns array of {name, price, original_price} objects.
# ─────────────────────────────────────────────────────────────────

EXTRACT_JS = """
() => {
  const results = [];
  const seen    = new Set();

  // ── Helpers ────────────────────────────────────────────────
  function parsePrice(text) {
    if (!text) return null;
    const clean = text.replace(/[^0-9.]/g, '');
    const val   = parseFloat(clean);
    return (!isNaN(val) && val > 10) ? val : null;
  }

  function getText(el) {
    return el ? el.innerText.trim() : '';
  }

  // Walk up from a price element to find its product card container.
  // The card is an ancestor that also contains a product name (h2/h3/a).
  function findCard(priceEl) {
    let el = priceEl.parentElement;
    for (let i = 0; i < 12 && el; i++) {
      const h = el.querySelector('h2, h3');
      if (h && getText(h).length > 5) return el;
      // Also accept anchor text that looks like a product name
      const a = el.querySelector('a[title], a[aria-label]');
      if (a) {
        const t = (a.getAttribute('title') || a.getAttribute('aria-label') || '').trim();
        if (t.length > 10) return el;
      }
      el = el.parentElement;
    }
    return null;
  }

  // Extract the best product name from a card element.
  function extractName(card) {
    // Priority: h2 > h3 > a[title] > a[aria-label] > longest anchor text
    const h2 = card.querySelector('h2');
    if (h2) {
      const span = h2.querySelector('span, a');
      const t = getText(span || h2);
      if (t.length > 5) return t;
    }
    const h3 = card.querySelector('h3');
    if (h3) {
      const t = getText(h3);
      if (t.length > 5) return t;
    }
    const atitle = card.querySelector('a[title]');
    if (atitle) {
      const t = (atitle.getAttribute('title') || '').trim();
      if (t.length > 5) return t;
    }
    const alabel = card.querySelector('a[aria-label]');
    if (alabel) {
      const t = (alabel.getAttribute('aria-label') || '').trim();
      if (t.length > 5) return t;
    }
    // Fallback: longest anchor text in card
    let bestA = '', bestLen = 0;
    card.querySelectorAll('a').forEach(a => {
      const t = getText(a);
      if (t.length > bestLen) { bestLen = t.length; bestA = t; }
    });
    return bestA;
  }

  // Find the struck-through original price in the same card.
  function extractOriginal(card) {
    const selectors = [
      'span.a-text-strike',          // Amazon
      'del', 's',                    // generic
      '[class*="original"]',
      '[class*="strike"]',
      '[class*="was-price"]',
      '[class*="old-price"]',
      '[class*="mrp"]',
    ];
    for (const sel of selectors) {
      const el = card.querySelector(sel);
      if (el) {
        const p = parsePrice(getText(el));
        if (p) return p;
      }
    }
    return null;
  }

  // ── Main scan ──────────────────────────────────────────────
  // Find ALL text nodes containing ₹ across the page.
  const walker = document.createTreeWalker(
    document.body,
    NodeFilter.SHOW_TEXT,
    { acceptNode: n => n.textContent.includes('₹') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT }
  );

  const priceNodes = [];
  let node;
  while ((node = walker.nextNode()) !== null) {
    priceNodes.push(node.parentElement);
  }

  for (const priceEl of priceNodes) {
    const priceText = getText(priceEl);
    const price     = parsePrice(priceText);
    if (!price) continue;

    // Skip nav, header, footer, breadcrumbs, cart totals
    const tag = (priceEl.tagName || '').toLowerCase();
    if (['script','style','noscript'].includes(tag)) continue;

    // Find the product card this price belongs to
    const card = findCard(priceEl);
    if (!card) continue;

    const name = extractName(card);
    if (!name || name.length < 5) continue;

    // Dedup by name
    const key = name.substring(0, 40).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);

    const original = extractOriginal(card);

    results.push({
      name:           name.substring(0, 200),
      price:          price,
      original_price: (original && original > price) ? original : null,
    });

    if (results.length >= """ + str(TOP_N_RESULTS) + """) break;
  }

  return results;
}
"""


# ─────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────

def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    clean = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        val = float(clean)
        return val if val > 0 else None
    except ValueError:
        return None


def _get_domain(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


def _network_available() -> bool:
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False


def _records_from_js_results(js_results: list, competitor_name: str,
                              url: str, ts: str,
                              catalog_sku: str, catalog_product_name: str) -> list[dict]:
    """Convert raw JS extraction results to price record dicts."""
    records = []
    for r in js_results:
        name  = str(r.get("name", "")).strip()
        price = r.get("price")
        if not name or not price:
            continue
        records.append({
            "competitor_name":     competitor_name,
            "competitor_url":      url,
            "product_name_raw":    name,
            "price":               float(price),
            "original_price":      r.get("original_price"),
            "in_stock":            True,
            "scraped_at":          ts,
            "confidence":          "high",
            "scrape_method_used":  "js_extract",
            "catalog_sku":         catalog_sku,
            "catalog_product_name": catalog_product_name,
        })
    return records


# ─────────────────────────────────────────────────────────────────
#  STATIC SCRAPER (Tier 1 — BeautifulSoup fallback for simple sites)
#  Used for Magento/WooCommerce sites that don't need JS.
# ─────────────────────────────────────────────────────────────────

def _static_scrape(url: str, competitor_name: str, ts: str,
                   catalog_sku: str, catalog_product_name: str) -> list[dict]:
    """
    Static scrape using BeautifulSoup.
    Looks for ₹ in text nodes, then walks up to find product name.
    Same logic as the JS version but in Python.
    """
    headers = {**REQUEST_HEADERS, "User-Agent": random.choice(USER_AGENTS)}
    resp    = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()

    soup    = BeautifulSoup(resp.text, "html.parser")
    records = []
    seen    = set()

    # Find all text containing ₹
    for el in soup.find_all(string=re.compile(r'₹')):
        price_text = el.strip()
        price      = _parse_price(price_text)
        if not price:
            continue

        # Walk up to find a container with a product name
        parent = el.parent
        card   = None
        for _ in range(10):
            if not parent:
                break
            if parent.find(['h2', 'h3']):
                card = parent
                break
            parent = parent.parent

        if not card:
            continue

        name_el = card.find(['h2', 'h3'])
        name    = name_el.get_text(separator=" ", strip=True) if name_el else ""

        if not name or len(name) < 5:
            continue

        key = name[:40].lower()
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "competitor_name":     competitor_name,
            "competitor_url":      url,
            "product_name_raw":    name,
            "price":               price,
            "original_price":      None,
            "in_stock":            True,
            "scraped_at":          ts,
            "confidence":          "medium",
            "scrape_method_used":  "static_rupee_scan",
            "catalog_sku":         catalog_sku,
            "catalog_product_name": catalog_product_name,
        })

        if len(records) >= TOP_N_RESULTS:
            break

    return records


# ─────────────────────────────────────────────────────────────────
#  PLAYWRIGHT SCRAPER (Tier 2/3 — JS extraction)
# ─────────────────────────────────────────────────────────────────

def _playwright_scrape(url: str, competitor_name: str, ts: str,
                       stealth: bool, catalog_sku: str,
                       catalog_product_name: str) -> list[dict]:
    """
    Load the page with Playwright, wait for ₹ to appear in the DOM,
    then run EXTRACT_JS to pull product names + prices.
    Returns list of price record dicts (top N results).
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language":          "en-IN,en;q=0.9",
                "Accept":                   "text/html,application/xhtml+xml,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
        )

        # Mask webdriver in stealth mode
        if stealth:
            ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                window.chrome = {runtime: {}};
            """)

        page = ctx.new_page()

        # Block images/fonts/media to load faster
        page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,mp4,webm}",
                   lambda route: route.abort())

        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # ── Wait for ₹ to appear (up to 15 s) ──────────────────
        # This is the key wait: we wait until actual price data renders,
        # not just until the DOM is built.
        try:
            page.wait_for_function(
                "() => document.body.innerText.includes('₹')",
                timeout=15000,
            )
        except Exception:
            # ₹ didn't appear — try scrolling to trigger lazy load
            pass

        # Scroll down to trigger any lazy-loaded product cards
        if stealth:
            for _ in range(3):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                time.sleep(random.uniform(0.8, 1.5))
        else:
            page.evaluate("window.scrollBy(0, 600)")
            time.sleep(random.uniform(1.5, 2.5))

        # ── Wait again after scroll ──────────────────────────────
        try:
            page.wait_for_function(
                "() => document.body.innerText.includes('₹')",
                timeout=8000,
            )
        except Exception:
            pass

        # ── Run JS extraction ────────────────────────────────────
        try:
            js_results = page.evaluate(EXTRACT_JS)
        except Exception as e:
            browser.close()
            raise RuntimeError(f"JS extraction failed: {e}")

        browser.close()

    if not js_results:
        return []

    return _records_from_js_results(
        js_results, competitor_name, url, ts,
        catalog_sku, catalog_product_name
    )


# ─────────────────────────────────────────────────────────────────
#  LANGCHAIN @TOOL WRAPPERS
# ─────────────────────────────────────────────────────────────────

@tool
def scrape_static_html(url: str) -> str:
    """
    Scrape product prices from a static HTML page.
    Scans for ₹ symbols and extracts product names + prices.
    Returns JSON list or error.
    """
    try:
        records = _static_scrape(url, "unknown", datetime.now().isoformat(), "", "")
        return json.dumps(records[:3])
    except Exception as e:
        return f"Error: {e}"


@tool
def scrape_dynamic_page(url: str) -> str:
    """
    Scrape a JS-rendered e-commerce page using Playwright.
    Waits for ₹ symbols to appear in the DOM, then extracts top 3 results.
    Returns JSON list or error.
    """
    try:
        records = _playwright_scrape(url, "unknown", datetime.now().isoformat(),
                                     stealth=False, catalog_sku="",
                                     catalog_product_name="")
        return json.dumps(records[:3])
    except Exception as e:
        return f"Error: {e}"


# ─────────────────────────────────────────────────────────────────
#  CORE: scrape one (competitor × product) target
# ─────────────────────────────────────────────────────────────────

def scrape_one_competitor(competitor: dict, catalog: list,
                          retailer_id: int) -> list[dict]:
    """
    Scrape a single (competitor × product) search URL.

    Flow:
      1. Static scan (fast, no browser) — works for Magento/WooCommerce sites
      2. Playwright + JS extraction — waits for ₹ in DOM, then runs EXTRACT_JS
      3. Playwright stealth + scroll — for anti-bot protected sites

    Each step only runs if the previous returned 0 results.
    Returns [] on hard failure (blocked, CAPTCHA, DNS error).
    """
    url           = competitor["url"]
    name          = competitor["competitor_name"]
    method        = competitor.get("scrape_method", "dynamic")
    catalog_sku   = competitor.get("catalog_sku", "")
    catalog_pname = competitor.get("catalog_product_name", "")
    ts            = datetime.now().isoformat()

    short_name = f"{name} | {catalog_pname[:30]}" if catalog_pname else name
    print(f"  [Scraper] {short_name} → {url[:65]}")

    if not _network_available():
        print(f"    ✗ No network — skipping")
        return []

    # ── Tier 1: Static scan ───────────────────────────────────
    if method == "static":
        try:
            records = _static_scrape(url, name, ts, catalog_sku, catalog_pname)
            if records:
                print(f"    ✓ {len(records)} result(s) (static) — "
                      f"₹{records[0]['price']:,.0f}")
                db.mark_scrape_result(retailer_id, url, success=True)
                return records
            print(f"    ↑ Static: no ₹ found — escalating to Playwright")
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", 0)
            if code in (403, 429, 503):
                print(f"    ✗ HTTP {code} — blocked")
                db.mark_scrape_result(retailer_id, url, success=False)
                return []
            print(f"    ↑ Static HTTP error ({code}) — escalating")
        except Exception as e:
            print(f"    ↑ Static error ({str(e)[:60]}) — escalating")

    # ── Tier 2: Playwright (normal) ───────────────────────────
    try:
        records = _playwright_scrape(url, name, ts, stealth=False,
                                     catalog_sku=catalog_sku,
                                     catalog_product_name=catalog_pname)
        if records:
            print(f"    ✓ {len(records)} result(s) (playwright) — "
                  f"₹{records[0]['price']:,.0f}")
            db.mark_scrape_result(retailer_id, url, success=True)
            return records
        print(f"    ↕ Playwright: ₹ not found — escalating to stealth")
    except Exception as e:
        err = str(e)
        if any(k in err.lower() for k in ("403", "blocked", "captcha", "access denied")):
            print(f"    ✗ Blocked: {err[:70]}")
            db.mark_scrape_result(retailer_id, url, success=False)
            return []
        print(f"    ↕ Playwright error ({err[:60]}) — escalating to stealth")

    # ── Tier 3: Playwright stealth ────────────────────────────
    try:
        records = _playwright_scrape(url, name, ts, stealth=True,
                                     catalog_sku=catalog_sku,
                                     catalog_product_name=catalog_pname)
        if records:
            print(f"    ✓ {len(records)} result(s) (stealth) — "
                  f"₹{records[0]['price']:,.0f}")
            db.mark_scrape_result(retailer_id, url, success=True)
            return records
        print(f"    ✗ Stealth: still no ₹ found — page may be bot-protected")
    except Exception as e:
        err = str(e)
        if any(k in err.lower() for k in ("403", "blocked", "captcha", "err_name_not_resolved")):
            print(f"    ✗ {err[:70]}")
        else:
            print(f"    ✗ Stealth error: {err[:70]}")

    db.mark_scrape_result(retailer_id, url, success=False)
    print(f"    ✗ All tiers failed for {name} — no data this cycle")
    return []


# ─────────────────────────────────────────────────────────────────
#  LANGGRAPH NODE
# ─────────────────────────────────────────────────────────────────

def run_scraper_node(state: AgentState) -> dict:
    """
    LangGraph node: Scraper Agent.
    Runs all (competitor × product) targets in parallel (max 4 threads).
    Records are pre-tagged with catalog_sku — normalizer uses directly.
    """
    targets     = db.get_competitors(state["retailer_id"])
    catalog     = state["retailer_profile"].catalog
    retailer_id = state["retailer_id"]

    if not targets:
        print("\n[Scraper] No targets registered.")
        return {"scraped_records": [], "scraping_complete": True,
                "current_node": "scraper"}

    n_products = len({t.get("catalog_sku","") for t in targets
                      if t.get("catalog_sku")})
    n_comps    = len({t.get("competitor_name","") for t in targets})
    print(f"\n[Scraper] {len(targets)} targets "
          f"({n_products} products × {n_comps} competitors) — launching...")

    all_records: list[dict] = []
    failed:      list[str]  = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(scrape_one_competitor, t, catalog, retailer_id): t
            for t in targets
        }
        for future in concurrent.futures.as_completed(futures):
            t = futures[future]
            try:
                records = future.result()
                all_records.extend(records)
                if not records:
                    failed.append(
                        f"{t['competitor_name']}|{t.get('catalog_sku','?')}"
                    )
            except Exception as e:
                failed.append(t["competitor_name"])
                print(f"  [Scraper] Unhandled: {t['competitor_name']}: {e}")

    db.save_price_records(retailer_id, all_records)

    ok = len(targets) - len(failed)
    print(f"\n[Scraper] Done — {len(all_records)} price records "
          f"from {ok}/{len(targets)} targets")
    if failed:
        print(f"  No data for {len(failed)} target(s)")

    return {
        "scraped_records":   all_records,
        "scraping_complete": True,
        "current_node":      "scraper",
        "errors": ([f"Scraper: {len(failed)} targets no data"] if failed else []),
    }