import json
import asyncio
from playwright.async_api import async_playwright

SCRIPTS_DIR = r"C:\Users\lyh17\.agents\skills\redbookskills\scripts"

async def get_cookies():
    # Connect to the existing Edge browser
    ws_url = "ws://localhost:9222/devtools/browser/f1f404e6-be3f-4bbd-b45a-e65288f25b08"
    print(f"Connecting to: {ws_url}")
    
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        print(f"Connected! {len(browser.contexts)} context(s)")
        
        context = browser.contexts[0]
        page = await context.new_page()
        
        # Go to Xiaohongshu
        print("Navigating to xiaohongshu.com...")
        await page.goto("https://www.xiaohongshu.com", wait_until="networkidle", timeout=30000)
        print(f"Page URL: {page.url}")
        
        # Wait a bit for cookies to load
        await asyncio.sleep(2)
        
        # Get all cookies
        cookies = await context.cookies()
        print(f"Got {len(cookies)} cookies")
        
        # Show cookie names
        for c in cookies:
            print(f"  {c['name']} = {c['value'][:30]}...")
        
        # Format for API use
        api_cookies = {}
        for c in cookies:
            api_cookies[c["name"]] = c["value"]
        
        # Save full cookies
        with open(f"{SCRIPTS_DIR}\\cookies_for_api.json", "w") as f:
            json.dump(api_cookies, f, ensure_ascii=False, indent=2)
        print(f"Saved {len(cookies)} cookies to cookies_for_api.json")
        
        # Also save in web format
        web_cookies = []
        for c in cookies:
            web_cookies.append({
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c["path"]
            })
        with open(f"{SCRIPTS_DIR}\\cookies.json", "w") as f:
            json.dump(web_cookies, f, ensure_ascii=False, indent=2)
        print("Also saved to cookies.json")
        
        await browser.close()
        return cookies

if __name__ == "__main__":
    asyncio.run(get_cookies())