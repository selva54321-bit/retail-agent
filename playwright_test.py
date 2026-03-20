"""Fetch Poorvika and Girias search pages to identify CSS selectors."""
from playwright.sync_api import sync_playwright
import time

URLS = {
    "poorvika": "https://www.poorvika.com/catalogsearch/result/?q=Samsung+43+inch+TV",
    "girias":   "https://www.girias.com/catalogsearch/result/?q=Samsung+43+inch+TV",
}

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    for name, url in URLS.items():
        print(f"Fetching {name}...")
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)
            # Scroll to trigger lazy load
            page.evaluate("window.scrollBy(0, 800)")
            time.sleep(1)
            html = page.content()
            with open(f"{name}.html", "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  Saved {name}.html ({len(html):,} chars)")
            page.close()
        except Exception as e:
            print(f"  Failed: {e}")
    browser.close()
