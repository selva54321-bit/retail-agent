from playwright.sync_api import sync_playwright
import time
import random

def main():
    print("Launching headed browser to bypass Amazon bot detection...")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print("Going to Amazon.in...")
        page.goto("https://www.amazon.in", wait_until="domcontentloaded")
        time.sleep(3)
        
        print("Typing search query...")
        box = page.locator("#twotabsearchtextbox")
        box.fill("")
        box.type("LG 81.28 cm 32 inch Full HD LED Smart WebOS TV", delay=100)
        box.press("Enter")
        
        print("Waiting for results...")
        page.wait_for_selector('[data-component-type="s-search-result"]', timeout=15000)
        time.sleep(2)
        
        html = page.content()
        with open("amazon_raw.html", "w", encoding="utf-8") as f:
            f.write(html)
            
        print(f"Saved {len(html)} bytes to amazon_raw.html")
        browser.close()

if __name__ == "__main__":
    main()
