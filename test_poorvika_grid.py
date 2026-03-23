from bs4 import BeautifulSoup

with open('poorvika_iphone.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

# Find where iPhones are listed
iphones = soup.find_all(string=lambda t: t and 'iPhone' in t)
print(f"Found {len(iphones)} iPhone text nodes")

# Let's find the cards for these iPhones
containers = {}
for ip in iphones[:10]:
    p = ip.parent
    depth = 0
    while p and p.name != 'body' and depth < 8:
        cls = " ".join(p.get("class", []))
        if "card" in cls or "product" in cls or "grid" in cls or "item" in cls:
            tag_cls = f"{p.name}.{cls}"
            containers[tag_cls] = containers.get(tag_cls, 0) + 1
        p = p.parent
        depth += 1

print("\nCommon card/grid classes:")
for k, v in sorted(containers.items(), key=lambda item: item[1], reverse=True)[:10]:
    print(f"{v:3d}  {k}")
