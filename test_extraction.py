from bs4 import BeautifulSoup
from agents.scraper.extractor import _bs4_extract, SITE_SELECTORS

with open('poorvika.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')
sel = SITE_SELECTORS.get('poorvika.com')
cards = soup.select(sel['card'])
print(f"Poorvika cards found: {len(cards)}")

# Test extraction
results = _bs4_extract(html, "poorvika.com")
print(f"Extractor returned {len(results)} items")
for i, item in enumerate(results[:5]):
    print(f"  {i+1}. {item['name'][:60]}")
    print(f"     Price: {item.get('price')} | Orig: {item.get('original_price')}")
