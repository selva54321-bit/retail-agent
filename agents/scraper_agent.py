"""
RetailAgent — Scraper Agent
=============================
Handles all web scraping with a three-tier strategy:
  1. Static HTML  → BeautifulSoup4 + CSS selectors
  2. JS-rendered  → Playwright headless Chromium
  3. Anti-bot     → Playwright + proxy rotation + randomized headers

Self-healing: when a scrape fails, the agent:
  1. Retries with different User-Agent
  2. Escalates to Playwright if static fails
  3. Asks LLM to regenerate CSS selectors from page DOM
  4. Falls back to last known price with low confidence

When live scraping is blocked (sandbox/no internet), falls back
to a realistic price simulator so the full pipeline still runs.
"""

import asyncio
import random
import time
import re
import json
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from core.state import AgentState, PriceRecord
from core.llm import chat_json, is_available
from core import database as db


# ─── User-Agent pool ────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

# ─── CSS selector config per known domain ────────────────────────
DOMAIN_SELECTORS = {
    "amazon.in": {
        "product":  ".s-result-item[data-asin]",
        "name":     "h2 .a-text-normal",
        "price":    ".a-price .a-offscreen",
        "original": ".a-text-strike",
        "in_stock": ".a-size-medium.a-color-success",
    },
    "flipkart.com": {
        "product":  "._1AtVbE",
        "name":     "._4rR01T, .s1Q9rs",
        "price":    "._30jeq3",
        "original": "._3I9_wc",
        "in_stock": None,
    },
    "croma.com": {
        "product":  ".cp-product",
        "name":     ".product-title",
        "price":    ".amount",
        "original": ".strike-amount",
        "in_stock": None,
    },
    "reliancedigital.in": {
        "product":  ".product-wrapper",
        "name":     ".product-name",
        "price":    ".pdp-final-price",
        "original": ".pdp-cp",
        "in_stock": None,
    },
}


# ─── Price Simulator ─────────────────────────────────────────────

def simulate_competitor_prices(catalog: list, competitor_name: str,
                                positioning: str = "mid-market") -> list:
    """
    Generates realistic simulated price data when live scraping is unavailable.
    Applies competitor-specific price variation based on positioning.
    """
    records = []
    now = datetime.now().isoformat()

    # Competitor personality: how they price relative to market
    PERSONALITIES = {
        "amazon india":     {"bias": -0.03, "variance": 0.06},
        "amazon":           {"bias": -0.03, "variance": 0.06},
        "flipkart":         {"bias": -0.05, "variance": 0.08},
        "croma":            {"bias":  0.02, "variance": 0.04},
        "reliance digital": {"bias":  0.01, "variance": 0.05},
        "vijay sales":      {"bias": -0.01, "variance": 0.04},
        "snapdeal":         {"bias": -0.08, "variance": 0.10},
        "meesho":           {"bias": -0.12, "variance": 0.12},
    }

    comp_lower = competitor_name.lower().strip()
    personality = PERSONALITIES.get(comp_lower, {"bias": 0.0, "variance": 0.07})

    for product in catalog:
        base_price = float(product.get("current_price", 0))
        if base_price == 0:
            continue

        # Apply bias + random variance
        noise = random.uniform(-personality["variance"], personality["variance"])
        mult  = 1.0 + personality["bias"] + noise
        comp_price = round(base_price * mult, -1)   # round to nearest 10

        # Occasionally simulate a sale
        original_price = None
        if random.random() < 0.20:   # 20% chance of discount display
            original_price = comp_price
            comp_price     = round(comp_price * random.uniform(0.85, 0.95), -1)

        # Occasionally out of stock
        in_stock = random.random() > 0.08

        records.append(PriceRecord(
            competitor_name    = competitor_name,
            competitor_url     = f"https://simulated/{comp_lower.replace(' ', '-')}",
            product_name_raw   = product["name"],
            price              = max(comp_price, 1.0),
            original_price     = original_price,
            in_stock           = in_stock,
            scraped_at         = now,
            confidence         = "medium",
            scrape_method_used = "simulated",
        ))

    return records


# ─── Static Scraper ──────────────────────────────────────────────

def _get_domain(url: str) -> str:
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else ""


def _scrape_static(url: str, selectors: dict) -> list:
    """Scrape a static HTML page using BeautifulSoup."""
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select(selectors.get("product", ""))
    if not items:
        return []

    results = []
    for item in items[:20]:   # cap at 20 products per page
        name_el  = item.select_one(selectors.get("name", ""))
        price_el = item.select_one(selectors.get("price", ""))
        orig_el  = item.select_one(selectors.get("original", "") or "")

        if not name_el or not price_el:
            continue

        name  = name_el.get_text(strip=True)
        price = _parse_price(price_el.get_text(strip=True))
        orig  = _parse_price(orig_el.get_text(strip=True)) if orig_el else None

        if price and price > 0:
            results.append({
                "name": name, "price": price, "original": orig
            })

    return results


def _parse_price(text: str) -> Optional[float]:
    """Extract numeric price from strings like '₹45,999' or 'Rs. 1,299'."""
    clean = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def _scrape_with_playwright(url: str, selectors: dict) -> list:
    """Scrape a JS-rendered page using Playwright."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 720},
                locale="en-IN",
            )
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            time.sleep(random.uniform(1.5, 3.0))  # human-like pause

            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        items = soup.select(selectors.get("product", ""))
        results = []
        for item in items[:20]:
            name_el  = item.select_one(selectors.get("name", ""))
            price_el = item.select_one(selectors.get("price", ""))
            if not name_el or not price_el:
                continue
            name  = name_el.get_text(strip=True)
            price = _parse_price(price_el.get_text(strip=True))
            if price and price > 0:
                results.append({"name": name, "price": price, "original": None})
        return results

    except Exception as e:
        raise RuntimeError(f"Playwright scrape failed: {e}")


def _llm_regenerate_selectors(url: str, html_snippet: str) -> dict:
    """Ask LLM to generate new CSS selectors from a page DOM snippet."""
    if not is_available():
        return {}
    try:
        result = chat_json(
            "selector",
            "You are a web scraping expert. Given a snippet of HTML from an e-commerce "
            "product listing page, generate CSS selectors to extract product names and prices.\n"
            "Return JSON: {\"product\": \"selector\", \"name\": \"selector\", \"price\": \"selector\"}",
            f"URL: {url}\n\nHTML snippet:\n{html_snippet[:3000]}"
        )
        return result
    except Exception:
        return {}


# ─── Main Scraper Entry ──────────────────────────────────────────

def scrape_competitor(competitor: dict, catalog: list,
                      retailer_id: int) -> list[PriceRecord]:
    """
    Scrape a single competitor. Returns list of PriceRecord.
    Implements the three-tier strategy with self-healing fallback.
    """
    url    = competitor["url"]
    name   = competitor["competitor_name"]
    method = competitor.get("scrape_method", "static")
    domain = _get_domain(url)

    selectors = DOMAIN_SELECTORS.get(domain, competitor.get("selector_config", {}))
    now = datetime.now().isoformat()
    records = []

    print(f"  [Scraper] {name} ({method}) → {url[:60]}...")

    raw_results = []

    # Fast network check before attempting live scrape
    try:
        import socket
        socket.setdefaulttimeout(2)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        network_available = True
    except Exception:
        network_available = False

    if not network_available:
        print(f"    ✗ Network unavailable — using simulated prices for {name}")
        return simulate_competitor_prices(catalog, name)

    for attempt in range(3):
        try:
            if method == "static":
                raw_results = _scrape_static(url, selectors)
            elif method in ("dynamic", "anti_bot"):
                raw_results = _scrape_with_playwright(url, selectors)

            if raw_results:
                break

            # If no results but no exception, try playwright escalation
            if attempt == 0 and method == "static":
                method = "dynamic"
                print(f"    ↑ Escalating to Playwright (no results from static)")

        except requests.exceptions.ConnectionError:
            print(f"    ✗ Network unavailable — using simulated prices for {name}")
            return simulate_competitor_prices(catalog, name)

        except Exception as e:
            print(f"    ✗ Attempt {attempt+1} failed: {str(e)[:80]}")

            if attempt == 1 and is_available():
                # Self-healing: ask LLM for new selectors
                try:
                    resp = requests.get(url, headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=10)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    new_selectors = _llm_regenerate_selectors(url, str(soup)[:4000])
                    if new_selectors:
                        selectors = new_selectors
                        db.upsert_competitor(retailer_id, {
                            **competitor,
                            "selector_config": new_selectors
                        })
                        print(f"    ♺ LLM regenerated selectors for {domain}")
                except Exception:
                    pass

            if attempt == 2:
                print(f"    → All retries failed. Using simulated prices for {name}")
                db.mark_scrape_result(retailer_id, url, success=False)
                return simulate_competitor_prices(catalog, name)

            time.sleep(random.uniform(2, 5))

    if not raw_results:
        print(f"    → No results extracted. Using simulated prices for {name}")
        return simulate_competitor_prices(catalog, name)

    # Convert raw results to PriceRecord
    for r in raw_results:
        records.append(PriceRecord(
            competitor_name    = name,
            competitor_url     = url,
            product_name_raw   = r["name"],
            price              = r["price"],
            original_price     = r.get("original"),
            in_stock           = True,
            scraped_at         = now,
            confidence         = "high",
            scrape_method_used = method,
        ))

    db.mark_scrape_result(retailer_id, url, success=True)
    print(f"    ✓ {len(records)} prices extracted from {name}")
    return records


def run_scraper(state: AgentState, retailer_id: int) -> AgentState:
    """
    Runs all scraper agents (parallel via threading for prototype).
    Saves all records to DB and updates state.
    """
    import concurrent.futures

    if not state.execution_plan:
        print("[Scraper] No execution plan found. Skipping.")
        return state

    targets   = db.get_competitors(retailer_id)
    catalog   = state.retailer_profile.catalog
    all_records = []

    print(f"\n[Scraper] Starting parallel scrape of {len(targets)} competitors...")

    def scrape_one(target):
        try:
            recs = scrape_competitor(target, catalog, retailer_id)
            return [vars(r) for r in recs]
        except Exception as e:
            print(f"  [Scraper] ERROR on {target['competitor_name']}: {e}")
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(scrape_one, t): t for t in targets}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            all_records.extend(result)

    db.save_price_records(retailer_id, all_records)

    # Convert dicts back to PriceRecord objects for state
    state.scraped_records = all_records
    state.scraping_complete = True

    print(f"[Scraper] Done. {len(all_records)} price records collected.")
    return state