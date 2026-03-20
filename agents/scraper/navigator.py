"""
agents/scraper/navigator.py
============================
Sub-agent 1 — Navigator

Responsibility:
  Open the search URL in a Playwright browser.
  Wait for product content to render.
  Return: full rendered HTML + optional screenshot PNG bytes.

This agent does NOT extract any data — it only renders the page
and passes the raw output to the Fetcher.

Wait strategy (in order):
  1. DOMContentLoaded  → page structure is ready
  2. wait_for_selector → site-specific product card selector appears
  3. wait_for_function → ₹ symbol appears anywhere in the body text
  4. Scroll × 2       → trigger lazy-loaded product cards
"""

import random
import time
from typing import Optional

from agents.scraper.state import ScraperSubState


# ─────────────────────────────────────────────────────────────────
#  BROWSER CONSTANTS
# ─────────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# Site-specific selectors that signal "products are ready"
PRODUCT_READY_SELECTORS = [
    '[data-component-type="s-search-result"][data-asin]',  # Amazon
    'div[data-id]',                                         # Flipkart
    'li.product-item',                                      # Magento (Croma)
    "div[class*='productfifteen_card']",                    # Poorvika
    '[class*="ProductModule"]',                             # Tata Cliq
    '[class*="product-card"]',                              # Generic SPAs
]

STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver',           {get: () => undefined});
    Object.defineProperty(navigator, 'plugins',             {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
    Object.defineProperty(navigator, 'deviceMemory',        {get: () => 8});
    window.chrome = {runtime: {}};
"""


# ─────────────────────────────────────────────────────────────────
#  NAVIGATOR NODE
# ─────────────────────────────────────────────────────────────────

def run_navigator(state: ScraperSubState) -> dict:
    """
    LangGraph sub-graph node: Navigator.

    Opens the search URL in Playwright, waits for products to render,
    captures the full HTML and a screenshot, then passes them to the Fetcher.

    Returns partial state update: {page_html, screenshot_png, nav_success}.
    """
    url    = state["url"]
    method = state.get("scrape_method", "dynamic")
    name   = state["competitor_name"]
    pname  = state.get("catalog_product_name", "")

    print(f"    [Navigator] Loading: {url[:70]}")

    stealth = (method == "anti_bot")

    try:
        html, screenshot = _playwright_load(url, stealth=stealth)
        print(f"    [Navigator] ✓ Page rendered ({len(html):,} chars)")
        return {
            "page_html":      html,
            "screenshot_png": screenshot,
            "nav_success":    True,
        }
    except Exception as e:
        err = str(e)
        print(f"    [Navigator] ✗ {err[:80]}")
        return {
            "page_html":      "",
            "screenshot_png": None,
            "nav_success":    False,
            "errors":         state.get("errors", []) + [f"Navigator: {err}"],
        }


def _playwright_load(url: str, stealth: bool) -> tuple[str, Optional[bytes]]:
    """
    Core Playwright loading logic.
    Returns (rendered_html, screenshot_png_bytes).
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
            viewport={"width": 1440, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            extra_http_headers={
                "Accept-Language":           "en-IN,en;q=0.9",
                "Accept":                    "text/html,application/xhtml+xml,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
            },
        )

        if stealth:
            ctx.add_init_script(STEALTH_SCRIPT)

        page = ctx.new_page()

        # Block images/fonts to speed up loading — product text still renders
        page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,eot,mp4,webm}",
            lambda route: route.abort()
        )

        # ── Step 1: Load the page ──────────────────────────────
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # ── Step 2: Wait for product card selector ─────────────
        for sel in PRODUCT_READY_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=7000)
                print(f"    [Navigator] Product cards ready ({sel})")
                break
            except Exception:
                continue

        # ── Step 3: Wait for ₹ in page text ────────────────────
        try:
            page.wait_for_function(
                "() => document.body.innerText.includes('₹')",
                timeout=10000,
            )
        except Exception:
            pass   # proceed — fetcher/extractor will handle empty result

        # ── Step 4: Scroll to trigger lazy-loaded products ─────
        scroll_count = 3 if stealth else 2
        for _ in range(scroll_count):
            page.evaluate(f"window.scrollBy(0, {700 + random.randint(0, 300)})")
            time.sleep(random.uniform(0.8, 1.5) if stealth else 0.6)

        # Scroll back to top — extractor wants the first (most relevant) results
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.5)

        # ── Capture ─────────────────────────────────────────────
        html       = page.content()
        screenshot = page.screenshot(
            type="png",
            full_page=False,
            clip={"x": 0, "y": 0, "width": 1440, "height": 900},
        )

        browser.close()

    return html, screenshot