import sys
import importlib
from bs4 import BeautifulSoup
from agents.scraper.fetcher import _extract_product_section
import agents.scraper.extractor
importlib.reload(agents.scraper.extractor)

def main():
    try:
        with open("amazon_raw.html", "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        print(f"Failed to read file: {e}")
        return
        
    focused = _extract_product_section(html)
    print(f"Focused section length: {len(focused)}")
    
    scraped_name = "LG 81.28 cm 32 inch Full HD LED Smart WebOS TV"
    results = agents.scraper.extractor._bs4_extract(focused, "amazon.in", scraped_name)
    
    print(f"\nFinal extracted results from _bs4_extract: {len(results)}")
    for r in results:
        print(r)

if __name__ == "__main__":
    main()
