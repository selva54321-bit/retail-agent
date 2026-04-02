import json
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import re

def main():
    print("Launching Chromium...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://www.amazon.in/s?k=LG+81.28+cm+32+inch+Full+HD+LED+Smart+WebOS+TV", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        html = page.content()
        browser.close()

    print(f"Captured {len(html)} chars. Parsing...")
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('[data-component-type="s-search-result"][data-asin][data-asin!=""]')
    print(f"Found {len(cards)} cards.")
    
    if not cards:
        return
        
    card = cards[0]
    
    print("\n--- FIRST CARD HTML ---")
    print(card.prettify()[:1500])
    print("-----------------------\n")
    
    # Check title extraction
    spans = card.select("h2 a span")
    print(f"h2 a spans: {[s.get_text(strip=True) for s in spans]}")
    
    anchors = card.select("h2 a")
    print(f"h2 a texts: {[a.get_text(strip=True) for a in anchors]}")
    
    if spans:
        best = max((sp.get_text(separator=" ", strip=True) for sp in spans), key=len, default="")
        print(f"Best span string: '{best}'")
        print(f"Best span length: {len(best)}")
    else:
        print("No spans found via 'h2 a span'")

    # Check price extraction
    price_els = card.select("span.a-price:not(.a-text-price) span.a-offscreen")
    print(f"Price elements: {[e.get_text(strip=True) for e in price_els]}")

if __name__ == "__main__":
    main()
