from bs4 import BeautifulSoup

with open('poorvika_test_search.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

cards = soup.select("div.search-list_search_grid_four__aa4uY > div > div")
print("Found cards in grid:", len(cards))

if cards:
    c = cards[0]
    print(f"Card classes: {' '.join(c.get('class', []))}")
    print("\nh3 tags:")
    h3s = c.find_all("h3")
    for h in h3s:
        print(f"  [{' '.join(h.get('class', []))}] {h.text}")
        
    print("\nb tags (prices usually):")
    bs = c.find_all("b")
    for b in bs:
        print(f"  [{' '.join(b.get('class', []))}] {b.text}")
        
    print("\nspan tags:")
    spans = c.find_all("span")
    for s in spans[:10]:
        print(f"  [{' '.join(s.get('class', []))}] {s.text}")
