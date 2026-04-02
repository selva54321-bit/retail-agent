import requests
from bs4 import BeautifulSoup

def main():
    print("Fetching...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    r = requests.get("https://www.amazon.in/s?k=LG+32+inch+TV", headers=headers)
    html = r.text
    print(f"Captured {len(html)} chars.")
    
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('[data-component-type="s-search-result"][data-asin][data-asin!=""]')
    print(f"Found {len(cards)} cards.")
    
    if not cards:
        return
        
    card = cards[0]
    
    # Check title extraction
    spans = card.select("h2 a span")
    print(f"h2 a spans: {[s.get_text(strip=True) for s in spans]}")
    
    anchors = card.select("h2 a")
    print(f"h2 a texts: {[a.get_text(strip=True) for a in anchors]}")
    
    if spans:
        best = max((sp.get_text(separator=" ", strip=True) for sp in spans), key=len, default="")
        print(f"Best span string: '{best}'")
    else:
        print("No spans found via 'h2 a span'")

    # Check price extraction
    price_els = card.select("span.a-price:not(.a-text-price) span.a-offscreen")
    print(f"Price elements: {[e.get_text(strip=True) for e in price_els]}")
    
    # Fallback price checks
    print(f"All a-offscreen: {[e.get_text(strip=True) for e in card.select('span.a-offscreen')]}")

if __name__ == "__main__":
    main()
