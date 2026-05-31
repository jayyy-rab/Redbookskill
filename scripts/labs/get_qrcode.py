import asyncio
from playwright.async_api import async_playwright
import base64, os

QR_SAVE_PATH = os.path.join(os.path.dirname(__file__), 'login_qrcode.png')

async def extract_qr():
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
        
        # Find the QR code img inside .qrcode
        qr_img = page.locator('.qrcode img').first
        src = await qr_img.get_attribute('src')
        print(f'QR src length: {len(src) if src else 0}')
        
        if src and src.startswith('data:image'):
            # Extract base64 data
            b64_data = src.split(',')[1]
            img_bytes = base64.b64decode(b64_data)
            with open(QR_SAVE_PATH, 'wb') as f:
                f.write(img_bytes)
            print(f'Saved QR to: {QR_SAVE_PATH}')
            print(f'File size: {os.path.getsize(QR_SAVE_PATH)} bytes')
        else:
            print('No data:image URL found, checking page...')
            # Fallback: get inner HTML of qrcode
            qr = page.locator('.qrcode').first
            html = await qr.inner_html()
            print('QR HTML:', html[:300])
        
        await browser.close()

asyncio.run(extract_qr())
