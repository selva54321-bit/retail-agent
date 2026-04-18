"""
agents/scraper/auto_dom_tester.py
==================================
Developer utility to auto-heal or auto-discover CSS selectors for a given domain.

1. Uses Playwright to load a URL
2. Minifies the HTML
3. Uses Gemini to predict the CSS selectors (card, name, price, original)
4. Validates the selectors using BeautifulSoup natively against the HTML chunk.
5. If valid, updates the `competitor_registry.selector_config` in the DB.
"""
import sys
import json
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from core.llm import get_llm
from agents.scraper.extractor import _parse_price, _best_name_from_card
from core.database import get_conn

class SelectorHypothesis(BaseModel):
    card: str = Field(description="CSS selector for the outermost product card container")
    name: str = Field(description="CSS selector for the product title/name inside the card, e.g. 'h2 a' or '.title'")
    price: str = Field(description="CSS selector for the current numerical price inside the card. If there are multiple prices inside a parent, select the specific container.")
    original: str = Field(description="CSS selector for the struck-through original price (if any, else empty string)")

def clean_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "svg", "path", "head", "noscript", "meta", "link", "footer", "header", "nav"]):
        tag.decompose()
    return soup.prettify()

async def fetch_page(url: str) -> str:
    print(f"[DOM Tester] Fetching {url} via Playwright...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="networkidle")
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight/3)")
                await page.wait_for_timeout(1000)
            html = await page.content()
            return html
        finally:
            await browser.close()

def _test_selectors(html: str, selectors: dict) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(selectors.get("card", ""))
    if not cards: return []

    results = []
    for card in cards[:20]:
        name_sel = selectors.get("name", "")
        # Emulate _best_name_from_card extracting logic
        name = ""
        h2s = card.find_all("h2")
        if h2s:
            best = " ".join(h2.get_text(separator=" ", strip=True) for h2 in h2s)
            best = " ".join(best.split())
            if len(best) >= 10: name = best
        if not name and name_sel:
            el = card.select_one(name_sel)
            if el:
                t = (el.get("title") or el.get("aria-label") or el.get_text(separator=" ", strip=True))
                if t and len(t) >= 10: name = t
        if not name:
            continue
        
        price = None
        price_sel = selectors.get("price", "")
        if price_sel:
            for el in card.select(price_sel):
                if any(p.name in ("del", "s") or (p.get("class") and any("strike" in c for c in p.get("class", []))) for p in el.parents):
                    continue
                v = _parse_price(el.get_text(strip=True))
                if v:
                    price = v
                    break
        
        if not price:
            continue
            
        orig = None
        orig_sel = selectors.get("original", "")
        if orig_sel:
            el = card.select_one(orig_sel)
            if el: orig = _parse_price(el.get_text(strip=True))
            
        results.append({"name": name, "price": price, "original": orig})
    return results

def auto_discover_from_html(html: str, url: str, retailer_id: int = None, update_db: bool = False):
    import re
    domain_m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    domain = domain_m.group(1) if domain_m else url

    cleaned = clean_html(html)
    html_chunk = cleaned[:60000]
    print(f"[DOM Tester] Analyzing {len(html_chunk)} chars of DOM for {domain} with Gemini...")

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an expert web scraper. Analyze the following minified HTML. Identify the optimal CSS selectors to extract the product card list items, title/name, current price, and struck-through original price. Output MUST be precise CSS selectors (e.g., 'div.a-section.a-spacing-small'). Avoid generic structural classes like 'flex' or 'container'."),
        ("human", "Find selectors for: {url}\n\nHTML Snippet:\n{html}")
    ])
    
    llm = get_llm(temperature=0.0)
    structured_llm = llm.with_structured_output(SelectorHypothesis)
    chain = prompt | structured_llm
    
    try:
        hypothesis = chain.invoke({"url": url, "html": html_chunk})
        sels = hypothesis.model_dump()
        print(f"\n[DOM Tester] LLM Suggested Selectors:\n{json.dumps(sels, indent=2)}")
        
        print("\n[DOM Tester] Validating selectors locally...")
        items = _test_selectors(html, sels)
        
        if items:
            print(f"✅ Success! Extracted {len(items)} valid products.")
            for it in items[:3]:
                print(f"   - {it['name'][:50]} | ₹{it['price']} (was {it['original']})")
                
            if update_db and retailer_id is not None:
                conn = get_conn()
                conn.execute(
                    "UPDATE competitor_registry SET selector_config = ? WHERE retailer_id=? AND url LIKE ?",
                    (json.dumps(sels), retailer_id, f"%{domain}%")
                )
                conn.commit()
                conn.close()
                print(f"[DOM Tester] Saved updated selectors to database for domain {domain}!")
            return sels, items
        else:
            print("❌ Validation failed. The selectors didn't cleanly extract products. DOM may be too complex.")
            return None, []
    except Exception as e:
        print(f"[DOM Tester] Failed during evaluation: {e}")
        return None, []

async def auto_discover_selectors(url: str, retailer_id: int = None, update_db: bool = False):
    html = await fetch_page(url)
    sels, items = auto_discover_from_html(html, url, retailer_id, update_db)
    return sels

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m agents.scraper.auto_dom_tester <URL>")
        sys.exit(1)
        
    url = sys.argv[1]
    asyncio.run(auto_discover_selectors(url, update_db=False))
