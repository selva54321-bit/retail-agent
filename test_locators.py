import time
from playwright.sync_api import sync_playwright

def test_site(url, domain, selectors):
    print(f"\n--- Testing {domain} ---")
    with sync_playwright() as pw:
        # headless=True for faster test unless bot protection triggers
        browser = pw.chromium.launch(headless=False, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        page = ctx.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,eot,mp4,webm}", lambda route: route.abort())
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            found = False
            for sel in selectors:
                try:
                    loc = page.locator(sel).first
                    loc.wait_for(state="visible", timeout=3000)
                    if loc.is_enabled():
                        print(f"  [SUCCESS] Found search box with selector: {sel}")
                        loc.fill("test")
                        found = True
                        break
                except Exception:
                    pass
            if not found:
                print(f"  [FAIL] Could not find search box with any selector")
        except Exception as e:
            print(f"  [ERROR] {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    sites = [
        ("https://www.flipkart.com", "flipkart.com", ["input.Pke_EE", "input[title='Search for Products, Brands and More']", "input[class*='search']", "input[type='text']", "input[placeholder*='Search' i]"]),
        ("https://www.reliancedigital.in", "reliancedigital.in", ["input[id='suggestionBoxEle']", "input[class*='search' i]", "#suggestionBoxEle", "input#suggestionBoxEle"]),
        ("https://www.croma.com", "croma.com", ["input#headerSearchInput", "input[placeholder*='Search' i]", "#searchV2", "input#searchV2"]),
    ]
    for url, domain, sels in sites:
        test_site(url, domain, sels)