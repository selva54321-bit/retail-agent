import sys
from bs4 import BeautifulSoup
from agents.scraper.fetcher import _extract_product_section
import agents.scraper.extractor

def main():
    try:
        with open("amazon_raw.html", "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"Failed to read file: {e}")
        return
        
    focused = _extract_product_section(html)
    soup = BeautifulSoup(focused, "html.parser")
    cards = soup.select('[data-component-type="s-search-result"][data-asin][data-asin!=""]')
    
    scraped_name = "LG 81.28 cm 32 inch Full HD LED Smart WebOS TV"
    
    print("\n--- Card Diagnosis with h2 text ---")
    for i, card in enumerate(cards[:5]):
        h2s = card.find_all("h2")
        if h2s:
            name = max((h.get_text(separator=" ", strip=True) for h in h2s), key=len, default="")
        else:
            name = ""
            
        print(f"\nCard {i+1}:")
        print(f"  Name extracted: '{name}'")
        
        # Test Price
        price = None
        for sel_str in ["span.a-price:not(.a-text-price) span.a-offscreen", "span.a-price-whole"]:
            for el in card.select(sel_str):
                text = el.get_text(strip=True)
                price = agents.scraper.extractor._parse_price(text)
                if price:
                    break
            if price:
                break
        
        print(f"  Price extracted: {price}")
        if name and price:
            score = agents.scraper.extractor._match_score(name, scraped_name)
            print(f"  Match score: {score:.3f}")

if __name__ == "__main__":
    main()
