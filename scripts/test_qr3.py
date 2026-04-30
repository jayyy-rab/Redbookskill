import asyncio
from playwright.async_api import async_playwright

async def get_qr():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = await browser.new_page()
        await page.goto('https://www.xiaohongshu.com')
        await page.wait_for_timeout(2000)
        
        try:
            await page.click('text=登录', timeout=5000)
            await page.wait_for_timeout(3000)
        except:
            pass
        
        print('URL:', page.url)
        
        # Get outer HTML of qrcode element
        qr = page.locator('.qrcode')
        if await qr.count() > 0:
            html = await qr.first.inner_html()
            print('QR HTML:', html[:500])
        
        # Check img inside qrcode
        imgs = await page.locator('.qrcode img').all()
        print('imgs in .qrcode:', len(imgs))
        for img in imgs:
            src = await img.get_attribute('src')
            print(' img src:', src)
        
        # Check qrcode-img
        qr_img = page.locator('.qrcode-img')
        if await qr_img.count() > 0:
            html = await qr_img.first.inner_html()
            print('qrcode-img HTML:', html[:300])
        
        await browser.close()

asyncio.run(get_qr())
