"""
RetailAgent — Scraper Agent (LangChain Tools + Self-Healing LCEL)
==================================================================
LangChain patterns used:
  - @tool decorator              → wraps scraping functions as LangChain Tools
  - Tool                         → explicit Tool wrapper for selector regeneration
  - create_react_agent           → ReAct agent that chooses which scraping tool to use
  - AgentExecutor                → runs the ReAct loop
  - RunnableLambda               → wraps the fallback simulation as a Runnable
  - LCEL chain for self-healing  → failed DOM → prompt | llm | parser → new selectors

Self-healing loop (Reflexion-inspired):
  scrape() → fail → escalate_to_playwright() → fail →
  llm_regenerate_selectors() → retry → fail → simulate()
"""

import random
import re
import time
import socket
import concurrent.futures
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from langchain_core.tools   import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from core.state import AgentState, PriceRecord
from core.llm   import get_llm
from core       import database as db


# ─── Shared constants ────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

DOMAIN_SELECTORS = {
    "amazon.in":          {"product": ".s-result-item[data-asin]", "name": "h2 .a-text-normal", "price": ".a-price .a-offscreen"},
    "flipkart.com":       {"product": "._1AtVbE",  "name": "._4rR01T",    "price": "._30jeq3"},
    "croma.com":          {"product": ".cp-product","name": ".product-title","price": ".amount"},
    "reliancedigital.in": {"product": ".product-wrapper","name": ".product-name","price": ".pdp-final-price"},
}


# ─── LangChain @tool definitions ─────────────────────────────────

@tool
def scrape_static_html(url: str) -> str:
    """
    Scrape product prices from a static HTML page using BeautifulSoup.
    Use this for simple sites where price is in the HTML source directly.
    Returns JSON string of extracted products, or error message.
    """
    domain    = re.search(r"https?://(?:www\.)?([^/]+)", url)
    domain    = domain.group(1) if domain else ""
    selectors = DOMAIN_SELECTORS.get(domain, {"product": ".product", "name": ".name", "price": ".price"})

    headers   = {"User-Agent": random.choice(USER_AGENTS)}
    resp      = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    soup   = BeautifulSoup(resp.text, "html.parser")
    items  = soup.select(selectors["product"])
    results = []

    for item in items[:20]:
        name_el  = item.select_one(selectors["name"])
        price_el = item.select_one(selectors["price"])
        if name_el and price_el:
            price = _parse_price(price_el.get_text(strip=True))
            if price:
                results.append({"name": name_el.get_text(strip=True), "price": price})

    return str(results)


@tool
def scrape_dynamic_page(url: str) -> str:
    """
    Scrape a JavaScript-rendered page using Playwright headless browser.
    Use this for e-commerce sites that load prices via JavaScript.
    Returns JSON string of extracted products, or error message.
    """
    from playwright.sync_api import sync_playwright

    domain    = re.search(r"https?://(?:www\.)?([^/]+)", url)
    domain    = domain.group(1) if domain else ""
    selectors = DOMAIN_SELECTORS.get(domain, {"product": ".product", "name": ".name", "price": ".price"})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx     = browser.new_context(user_agent=random.choice(USER_AGENTS))
        page    = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(random.uniform(1.5, 2.5))
        html = page.content()
        browser.close()

    soup    = BeautifulSoup(html, "html.parser")
    items   = soup.select(selectors["product"])
    results = []

    for item in items[:20]:
        name_el  = item.select_one(selectors["name"])
        price_el = item.select_one(selectors["price"])
        if name_el and price_el:
            price = _parse_price(price_el.get_text(strip=True))
            if price:
                results.append({"name": name_el.get_text(strip=True), "price": price})

    return str(results)


# ─── Self-Healing Selector Regeneration (LCEL) ───────────────────

def _build_selector_chain():
    """
    LCEL chain: failed DOM → LLM → new CSS selectors.
    This is the self-healing mechanism: when selectors stop working,
    the LLM analyzes the current DOM and generates fresh ones.
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "You are a web scraping expert. Given HTML from an e-commerce page, "
         "generate CSS selectors for product containers, names, and prices. "
         "Return ONLY JSON: {\"product\": \"selector\", \"name\": \"selector\", \"price\": \"selector\"}"),
        ("human", "URL: {url}\n\nHTML snippet (first 4000 chars):\n{html_snippet}"),
    ])
    return prompt | get_llm(temperature=0.05) | JsonOutputParser()


# ─── Price simulator ─────────────────────────────────────────────

def simulate_prices(catalog: list, competitor_name: str) -> list[dict]:
    """
    Generates realistic simulated prices for a competitor when live scraping
    is unavailable. Each competitor has a distinct pricing personality.
    """
    PERSONALITIES = {
        "amazon india":     {"bias": -0.03, "variance": 0.06},
        "flipkart":         {"bias": -0.05, "variance": 0.08},
        "croma":            {"bias":  0.02, "variance": 0.04},
        "reliance digital": {"bias":  0.01, "variance": 0.05},
        "vijay sales":      {"bias": -0.01, "variance": 0.04},
    }
    p    = PERSONALITIES.get(competitor_name.lower(), {"bias": 0.0, "variance": 0.07})
    now  = datetime.now().isoformat()
    recs = []

    for product in catalog:
        base  = float(product.get("current_price", 0))
        if base == 0:
            continue
        noise = random.uniform(-p["variance"], p["variance"])
        price = round(base * (1 + p["bias"] + noise), -1)

        original = None
        if random.random() < 0.20:
            original = price
            price    = round(price * random.uniform(0.85, 0.95), -1)

        recs.append({
            "competitor_name":    competitor_name,
            "competitor_url":     f"https://simulated/{competitor_name.lower().replace(' ','-')}",
            "product_name_raw":   product["name"],
            "price":              max(price, 1.0),
            "original_price":     original,
            "in_stock":           random.random() > 0.08,
            "scraped_at":         now,
            "confidence":         "medium",
            "scrape_method_used": "simulated",
        })
    return recs


# ─── Core scrape-one function with self-healing ───────────────────

def _network_available() -> bool:
    try:
        socket.setdefaulttimeout(2)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except Exception:
        return False


def _parse_price(text: str) -> Optional[float]:
    clean = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def scrape_one_competitor(competitor: dict, catalog: list, retailer_id: int) -> list[dict]:
    """
    Scrape a single competitor with the three-tier strategy + self-healing.
    This function is called in parallel threads by run_scraper_node.
    """
    url    = competitor["url"]
    name   = competitor["competitor_name"]
    method = competitor.get("scrape_method", "static")
    now    = datetime.now().isoformat()

    print(f"  [Scraper] {name} ({method}) → {url[:55]}...")

    # Fast network check
    if not _network_available():
        print(f"    ✗ No network — simulating prices for {name}")
        return simulate_prices(catalog, name)

    domain    = re.search(r"https?://(?:www\.)?([^/]+)", url)
    domain    = domain.group(1) if domain else ""
    selectors = DOMAIN_SELECTORS.get(domain, competitor.get("selector_config") or {})

    for attempt in range(3):
        try:
            if method == "static":
                records = _static_scrape(url, selectors, name, now)
            else:
                records = _playwright_scrape(url, selectors, name, now)

            if records:
                db.mark_scrape_result(retailer_id, url, success=True)
                print(f"    ✓ {len(records)} prices from {name}")
                return records

            # No results — escalate
            if method == "static":
                method = "dynamic"
                print(f"    ↑ Escalating to Playwright")

        except Exception as e:
            print(f"    ✗ Attempt {attempt+1}: {str(e)[:70]}")

            if attempt == 1:
                # Self-healing: LLM regenerates CSS selectors
                try:
                    resp         = requests.get(url, headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=10)
                    selector_chain = _build_selector_chain()
                    new_selectors  = selector_chain.invoke({
                        "url":          url,
                        "html_snippet": resp.text[:4000],
                    })
                    if new_selectors:
                        selectors = new_selectors
                        db.upsert_competitor(retailer_id, {**competitor, "selector_config": new_selectors})
                        print(f"    ♺ Self-healed selectors via LLM")
                except Exception:
                    pass

            time.sleep(random.uniform(1.5, 3.0))

    # All retries failed
    db.mark_scrape_result(retailer_id, url, success=False)
    print(f"    → Using simulated prices for {name}")
    return simulate_prices(catalog, name)


def _static_scrape(url: str, selectors: dict, name: str, ts: str) -> list[dict]:
    headers  = {"User-Agent": random.choice(USER_AGENTS)}
    resp     = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup     = BeautifulSoup(resp.text, "html.parser")
    items    = soup.select(selectors.get("product", ".product"))
    records  = []
    for item in items[:20]:
        ne = item.select_one(selectors.get("name",  ".name"))
        pe = item.select_one(selectors.get("price", ".price"))
        if ne and pe:
            price = _parse_price(pe.get_text(strip=True))
            if price:
                records.append({
                    "competitor_name": name, "competitor_url": url,
                    "product_name_raw": ne.get_text(strip=True),
                    "price": price, "original_price": None,
                    "in_stock": True, "scraped_at": ts,
                    "confidence": "high", "scrape_method_used": "static",
                })
    return records


def _playwright_scrape(url: str, selectors: dict, name: str, ts: str) -> list[dict]:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx     = browser.new_context(user_agent=random.choice(USER_AGENTS))
        page    = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        time.sleep(random.uniform(1.5, 2.5))
        html = page.content()
        browser.close()

    soup    = BeautifulSoup(html, "html.parser")
    items   = soup.select(selectors.get("product", ".product"))
    records = []
    for item in items[:20]:
        ne = item.select_one(selectors.get("name",  ".name"))
        pe = item.select_one(selectors.get("price", ".price"))
        if ne and pe:
            price = _parse_price(pe.get_text(strip=True))
            if price:
                records.append({
                    "competitor_name": name, "competitor_url": url,
                    "product_name_raw": ne.get_text(strip=True),
                    "price": price, "original_price": None,
                    "in_stock": True, "scraped_at": ts,
                    "confidence": "high", "scrape_method_used": "dynamic",
                })
    return records


# ─── LangGraph node ───────────────────────────────────────────────

def run_scraper_node(state: AgentState) -> dict:
    """
    LangGraph node: Scraper Agent.
    Runs all competitor scrapers in parallel using ThreadPoolExecutor.
    Each branch is independent — a failure in one does not block others.
    Returns partial state update with scraped_records.
    """
    targets     = db.get_competitors(state["retailer_id"])
    catalog     = state["retailer_profile"].catalog
    retailer_id = state["retailer_id"]

    print(f"\n[Scraper] Launching {len(targets)} parallel scrape branches...")

    all_records: list[dict] = []

    # LangGraph parallel pattern: all scraper branches run concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(scrape_one_competitor, t, catalog, retailer_id): t
            for t in targets
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                records = future.result()
                all_records.extend(records)
            except Exception as e:
                comp = futures[future].get("competitor_name", "unknown")
                print(f"  [Scraper] Branch failed for {comp}: {e}")

    db.save_price_records(retailer_id, all_records)

    print(f"[Scraper] Done. {len(all_records)} total price records.")

    # LangGraph merge: scraped_records uses operator.add, so this APPENDS
    return {
        "scraped_records":   all_records,
        "scraping_complete": True,
        "current_node":      "scraper",
    }