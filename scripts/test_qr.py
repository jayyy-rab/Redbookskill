import asyncio
from playwright.async_api import async_playwright

async def get_qr():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = await browser.new_page()
        await page.goto('https://www.xiaohongshu.com')
        await page.wait_for_timeout(2000)
        
        print('Page URL:', page.url)
        
        # Try to click login
        try:
            await page.click('text=登录', timeout=3000)
            await page.wait_for_timeout(2000)
            print('Clicked login')
        except Exception as e:
            print('No login button:', e)
        
        # Get page structure for QR
        html = await page.content()
        # Look for QR-related elements
        import re
        qr_classes = re.findall(r'class="[^"]*qr[^"]*"', html, re.IGNORECASE)
        print('QR classes found:', qr_classes[:10])
        
        # Check canvas elements
        canvases = await page.locator('canvas').all()
        print('Canvas count:', len(canvases))
        
        # Check for specific login QR elements
        for sel in ['.qrcode', '.login-qrcode', '[class*="qr-code"]', '[class*="qrcode"]']:
            els = await page.locator(sel).all()
            if els:
                print(f'{sel}: found {len(els)} elements')
                for el in els[:2]:
                    try:
                        outer = await el.get_attribute('outerHTML')
                        print(f'  HTML snippet: {outer[:200]}')
                    except:
                        pass
        
        await browser.close()

asyncio.run(get_qr())
