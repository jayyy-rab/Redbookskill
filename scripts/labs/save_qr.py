import asyncio, json, base64, os
from playwright.async_api import async_playwright

async def save_qr():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = await browser.new_page()
        await page.goto('https://www.xiaohongshu.com')
        await page.wait_for_timeout(2000)
        try:
            await page.click('text=登录', timeout=5000)
            await page.wait_for_timeout(3000)
        except: pass
        src = await page.locator('.qrcode img').first.get_attribute('src')
        if src and src.startswith('data:image'):
            b64 = src.split(',')[1]
            img_bytes = base64.b64decode(b64)
            out = os.path.join(os.path.dirname(__file__), 'login_qrcode.png')
            with open(out, 'wb') as f: f.write(img_bytes)
            print(f'QR PNG saved: {out} ({len(img_bytes)} bytes)')
        await browser.close()

asyncio.run(save_qr())
