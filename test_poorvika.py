import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        page = await b.new_page()
        
        # Test Samsung
        url1 = "https://www.poorvika.com/Samsung+43-inch+Crystal+4K+UHD+UA43DU8570/s?q=Samsung+43-inch+Crystal+4K+UHD+UA43DU8570"
        print(f"Loading {url1}")
        res1 = await page.goto(url1)
        print(f"Samsung Response: {res1.status}")
        try:
            await page.wait_for_selector('div[class*="productfifteen"]', timeout=5000)
            print("Samsung Cards found!")
        except Exception:
            print("Samsung Cards NOT found!")
            
        print("-" * 40)
        
        # Test LG
        url2 = "https://www.poorvika.com/LG+32-inch+Smart+HD+TV+LM576/s?q=LG+32-inch+Smart+HD+TV+LM576"
        print(f"Loading {url2}")
        res2 = await page.goto(url2)
        print(f"LG Response: {res2.status}")
        try:
            await page.wait_for_selector('div[class*="productfifteen"]', timeout=5000)
            print("LG Cards found!")
        except Exception:
            print("LG Cards NOT found!")
            
        await b.close()

asyncio.run(run())
