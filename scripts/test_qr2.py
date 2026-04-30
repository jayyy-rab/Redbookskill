import asyncio
from playwright.async_api import async_playwright

async def get_qr():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = await browser.new_page()
        await page.goto('https://www.xiaohongshu.com')
        await page.wait_for_timeout(2000)
        
        # Click login button
        try:
            await page.click('text=登录', timeout=5000)
            await page.wait_for_timeout(3000)
        except:
            pass
        
        print('URL after login click:', page.url)
        
        # Get the QR code image
        qr_img = page.locator('.qrcode-img img')
        count = await qr_img.count()
        print(f'.qrcode-img img count: {count}')
        
        if count > 0:
            src = await qr_img.first.get_attribute('src')
            print(f'src: {src}')
            # Also check data-url or other attrs
            all_attrs = await qr_img.first.get_attributes()
            print(f'all attrs: {all_attrs}')
        else:
            # Try other selectors
            for sel in ['.qrcode img', '.qrcode-img', 'img[class*="qr"]']:
                els = await page.locator(sel).all()
                if els:
                    print(f'{sel}:')
                    for el in els[:3]:
                        attrs = await el.get_attributes()
                        print(f'  {attrs}')
        
        await browser.close()

asyncio.run(get_qr())
