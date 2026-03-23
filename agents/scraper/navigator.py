"""
agents/scraper/navigator.py
============================
Sub-agent 1 — Navigator

Opens the site in a HEADED browser, finds the search box using the
Playwright Locator API, types the product name, submits, waits for
the results page to fully render, then returns the HTML.

Flow:
  1. goto(base_url)                   — open homepage
  2. _find_search_box()               — returns a Locator (not ElementHandle)
  3. locator.click() → .fill() → Enter — clear box, type, submit
  4. _wait_for_results()              — wait for product cards + ₹
  5. _scroll_to_load()                — trigger lazy cards
  6. scrollTo(0,0)                    — back to top for extractor
  7. page.content()                   — return the search results page HTML

Important: page.content() at step 7 captures the fully-rendered RESULTS
page — not the homepage. The browser has already navigated to the results
URL after Enter was pressed and waited for product cards to appear.
This HTML is what Fetcher slices and Extractor parses.

Bug fix: Uses page.locator(sel).first instead of page.wait_for_selector()
which returned an ElementHandle. Only Locator objects support .fill(),
.triple_click(), etc.
"""

import random
import time

from agents.scraper.state import ScraperSubState


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver',           {get: () => undefined});
    Object.defineProperty(navigator, 'plugins',             {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
    Object.defineProperty(navigator, 'deviceMemory',        {get: () => 8});
    window.chrome = {runtime: {}};
"""

# ─────────────────────────────────────────────────────────────────
#  SEARCH BOX LOCATOR SELECTORS  (tried in order, first visible wins)
#  IMPORTANT: These are CSS selectors passed to page.locator()
#  NOT to wait_for_selector() — locator() returns a Locator object
#  which has .click(), .fill(), .triple_click() etc.
# ─────────────────────────────────────────────────────────────────

SEARCH_BOX_SELECTORS: dict[str, list[str]] = {
    "amazon.in": [
        "#twotabsearchtextbox",
        "input[name='field-keywords']",
        "input[type='text'][id*='search']",
        "input[placeholder*='Search']",
    ],
    "flipkart.com": [
        "input.Pke_EE",
        "input[title='Search for Products, Brands and More']",
        "input[class*='search']",
        "input[placeholder*='Search' i]",
        "input[type='text']",
    ],
    "poorvika.com": [
        "input[placeholder*='Search' i]",
        "input[class*='search' i]",
        "input[type='search']",
        "input[name='q']",
    ],
    "croma.com": [
        "input#headerSearchInput",
        "input[placeholder*='Search' i]",
        "input[class*='search']",
    ],
    "sangeetha.com": [
        "input[placeholder*='Search' i]",
        "input[name='q']",
        "input[type='search']",
    ],
    "girias.com": [
        "input#search",
        "input[name='q']",
        "input[placeholder*='Search' i]",
    ],
    "vasanthandco.com": [
        "input#search",
        "input[name='q']",
        "input[placeholder*='Search' i]",
    ],
}

GENERIC_SEARCH_SELECTORS = [
    "input[type='search']",
    "input[name='q']",
    "input[name='query']",
    "input[name='keyword']",
    "input[name='search']",
    "input[id*='search' i]",
    "input[class*='search' i]",
    "input[placeholder*='Search' i]",
    "input[placeholder*='search' i]",
]

RESULT_READY_SELECTORS = [
    '[data-component-type="s-search-result"][data-asin]',   # Amazon
    'div[data-id]',                                          # Flipkart
    'li.product-item',                                       # Magento
    '[class*="productfifteen"]',                             # Poorvika
    '[class*="ProductModule"]',                              # Tata Cliq
    '[class*="product-card"]',                               # Generic SPA
]


def run_navigator(state: ScraperSubState) -> dict:
    """LangGraph sub-graph node: Navigator."""
    base_url = state["url"]
    pname    = state.get("catalog_product_name", "")
    comp     = state["competitor_name"]

    print(f"    [Navigator] 🌐 {comp}")
    print(f"    [Navigator] Searching: '{pname[:55]}'")

    try:
        html = _interactive_search(base_url, pname)
        print(f"    [Navigator] ✓ Results HTML captured ({len(html):,} chars)")
        return {"page_html": html, "nav_success": True}
    except Exception as e:
        err = str(e)
        print(f"    [Navigator] ✗ {err[:100]}")
        return {
            "page_html":   "",
            "nav_success": False,
            "errors":      state.get("errors", []) + [f"Navigator: {err}"],
        }


def _get_domain(url: str) -> str:
    import re
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else ""


def _interactive_search(base_url: str, product_name: str) -> str:
    """
    Opens the site in a headed browser, uses the search box to search
    for product_name, waits for results, returns the results page HTML.
    """
    from playwright.sync_api import sync_playwright

    domain = _get_domain(base_url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,       # Headed — visible window for verification
            slow_mo=50,           # 50ms between actions — humanlike pacing
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
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
        ctx.add_init_script(STEALTH_SCRIPT)

        page = ctx.new_page()

        # Block images/fonts — page renders faster, search box appears sooner
        page.route(
            "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,eot,mp4,webm}",
            lambda route: route.abort()
        )

        # ── Step 1: Open the homepage ─────────────────────────────
        print(f"    [Navigator] Opening {base_url} ...")
        page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        # Let the page fully settle before looking for search box
        time.sleep(random.uniform(2.0, 3.0))

        # ── Step 2: Find the search box (Locator API) ─────────────
        search_locator = _find_search_box(page, domain)
        if search_locator is None:
            browser.close()
            raise RuntimeError(f"Search box not found on {base_url}")

        print(f"    [Navigator] ✓ Search box located — typing...")

        # ── Step 3: Click, clear, type the product name ───────────
        # Use Locator methods — these work reliably unlike ElementHandle
        search_locator.click()
        time.sleep(0.3)
        search_locator.fill("")          # clear any existing text
        time.sleep(0.2)
        # Type character by character with random delays — humanlike
        search_locator.type(product_name, delay=random.randint(50, 100))
        time.sleep(random.uniform(0.5, 1.0))

        # ── Step 4: Submit the search ─────────────────────────────
        print(f"    [Navigator] Submitting search...")
        search_locator.press("Enter")

        # ── Step 5: Wait for results page to load ─────────────────
        _wait_for_results(page, domain)

        # ── Step 6: Scroll to trigger lazy-loaded cards ───────────
        _scroll_to_load(page)

        # ── Step 7: Back to top — extractor wants first results ───
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.5)

        # ── Capture the results page HTML ─────────────────────────
        # At this point the browser is on the SEARCH RESULTS page,
        # not the homepage. page.content() returns the full rendered
        # HTML of the results — this is what Fetcher and Extractor process.
        html = page.content()
        browser.close()

    return html


def _find_search_box(page, domain: str):
    """
    Find the search input using the Locator API.
    Returns a Locator (not ElementHandle) — locators support .fill(),
    .type(), .press() etc. directly.

    Tries site-specific selectors first, then generic fallbacks.
    Each selector gets an 8-second window to appear on the page.
    """
    site_sels = SEARCH_BOX_SELECTORS.get(domain, [])
    all_sels  = site_sels + GENERIC_SEARCH_SELECTORS

    for sel in all_sels:
        try:
            # Use locator() not wait_for_selector()
            # locator() is lazy — check visibility before using
            loc = page.locator(sel).first
            # wait_for checks both existence and visibility
            loc.wait_for(state="visible", timeout=8000)
            if loc.is_enabled():
                print(f"    [Navigator] ✓ Search box: {sel}")
                return loc
        except Exception:
            continue

    return None


def _wait_for_results(page, domain: str) -> None:
    """Wait for search result cards to appear after pressing Enter."""
    # First wait for the URL to change (search navigation)
    try:
        page.wait_for_url(
            lambda url: url != page.url if hasattr(page, '_original_url') else True,
            timeout=5000
        )
    except Exception:
        pass

    # Wait for product card selectors
    for sel in RESULT_READY_SELECTORS:
        try:
            page.wait_for_selector(sel, timeout=12000)
            print(f"    [Navigator] ✓ Product cards ready ({sel})")
            return
        except Exception:
            continue

    # Final fallback: wait for ₹ symbol in page text
    try:
        page.wait_for_function(
            "() => document.body.innerText.includes('₹')",
            timeout=12000,
        )
        print(f"    [Navigator] ✓ ₹ prices detected")
    except Exception:
        print(f"    [Navigator] ⚠ Results wait timed out — using what loaded")


def _scroll_to_load(page) -> None:
    """Scroll down 3 times to trigger lazy-loaded product cards."""
    for _ in range(3):
        page.evaluate(f"window.scrollBy(0, {700 + random.randint(0, 400)})")
        time.sleep(random.uniform(0.8, 1.3))