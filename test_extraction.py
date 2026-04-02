import sys
from bs4 import BeautifulSoup
from agents.scraper.state import ScraperSubState
from agents.scraper.fetcher import _extract_product_section, _strip_noise
from agents.scraper.extractor import _bs4_extract
import importlib

# Reload extractor in case it's cached
import agents.scraper.extractor
importlib.reload(agents.scraper.extractor)
from agents.scraper.extractor import _bs4_extract

def main():
    try:
        with open("amazon_raw.html", "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"Failed to read file: {e}")
        return
        
    print(f"Loaded {len(html)} chars of raw HTML.")
    
    # 1. Simulate Fetcher
    focused = _extract_product_section(html)
    if focused:
        print(f"✓ Fetcher: Focused section is {len(focused)} chars.")
    else:
        focused = _strip_noise(html)
        print(f"↕ Fetcher: Stripped noise to {len(focused)} chars.")
        
    # 2. Simulate Extractor internally
    soup = BeautifulSoup(focused, "html.parser")
    sels = agents.scraper.extractor.SITE_SELECTORS.get("amazon.in")
    
    cards = soup.select(sels["card"])
    print(f"✓ Extractor matched {len(cards)} raw product cards via '{sels['card']}'")
    
    if not cards:
        print("No cards found! Dumping focused HTML prefix...")
        print(focused[:1000])
        return

    print("\n--- Card Diagnosis ---")
    scraped_name = "LG 81.28 cm 32 inch Full HD LED Smart WebOS TV"
    
    records = []
    
    for i, card in enumerate(cards[:3]):
        # Test my new name extraction logic
        spans = card.select("h2 a span")
        if spans:
            name = max((sp.get_text(separator=" ", strip=True) for sp in spans), key=len, default="")
        else:
            name = ""
            anchors = card.select("h2 a")
            if anchors:
                for anchor in anchors:
                    t = anchor.get("title") or anchor.get_text(separator=" ", strip=True)
                    if t and len(t) >= 10:
                        name = t
                        break
        
        print(f"\nCard {i+1}:")
        print(f"  Name extracted: '{name}'")
        
        if not name or len(name) < 10:
            print("  -> Dropped: Name empty or < 10 chars")
            continue
            
        print(f"  price selectors: {sels['price']}")
        price = None
        for sel_str in sels["price"].split(","):
            sel_str = sel_str.strip()
            for el in card.select(sel_str):
                text = el.get_text(strip=True)
                print(f"  -> Found price element text (sel='{sel_str}'): '{text}'")
                price = agents.scraper.extractor._parse_price(text)
                if price:
                    break
            if price:
                break
        
        print(f"  Price extracted: {price}")
        if not price:
            print("  -> Dropped: No valid price")
            continue
            
        score = agents.scraper.extractor._match_score(name, scraped_name)
        print(f"  Match score: {score:.3f}")

    print("\n--- Running actual _bs4_extract ---")
    results = _bs4_extract(focused, "amazon.in", scraped_name)
    print(f"\nFinal extracted results: {len(results)}")
    for r in results:
        print(r)

if __name__ == "__main__":
    main()
