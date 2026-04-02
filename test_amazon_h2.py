import sys
from bs4 import BeautifulSoup
from agents.scraper.fetcher import _extract_product_section

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
    
    if not cards:
        print("No cards found.")
        return
        
    card = cards[0]
    
    with open("amazon_h2_dump.txt", "w", encoding="utf-8") as f:
        f.write("--- h2 tags inside card ---\n")
        h2s = card.find_all("h2")
        for i, h2 in enumerate(h2s):
            f.write(f"h2 {i}:\n")
            f.write(h2.prettify())
            f.write("\n------------------\n")

if __name__ == "__main__":
    main()
