from agents.scraper.navigator import run_navigator
from agents.scraper.fetcher import run_fetcher
from agents.scraper.extractor import run_extractor
from agents.scraper.state import ScraperSubState

def test():
    state: ScraperSubState = {
        "url": "https://www.amazon.in/s?k=iphone",
        "competitor_name": "amazon",
        "catalog_sku": "TEST-SKU",
        "catalog_product_name": "Apple iPhone 15",
        "scrape_method": "dynamic",
        "page_html": "",
        "nav_success": False,
        "dom_section": "",
        "products": [],
        "errors": []
    }
    
    print("--- Testing Navigator ---")
    nav_update = run_navigator(state)
    state.update(nav_update)
    print(f"Nav Success: {state['nav_success']}, HTML len: {len(state['page_html'])}")
    
    # Verify screenshot_png is NOT in state anymore
    if "screenshot_png" in state:
        print("FAIL: screenshot_png still in state!")
    else:
        print("PASS: screenshot_png removed from state.")
        
    print("\n--- Testing Fetcher ---")
    fetch_update = run_fetcher(state)
    state.update(fetch_update)
    print(f"DOM Section len: {len(state['dom_section'])}")
    
    print("\n--- Testing Extractor ---")
    ext_update = run_extractor(state)
    state.update(ext_update)
    print(f"Products found: {len(state['products'])}")
    for p in state['products']:
        print(f"  - {p['name']} | ₹{p['price']}")

if __name__ == "__main__":
    test()
