import asyncio
from playwright.async_api import async_playwright
import base64, os, json

QR_OUT = os.path.join(os.path.dirname(__file__), 'qr_data.json')

async def serve_qr():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        page = await browser.new_page()
        await page.goto('https://www.xiaohongshu.com')
        await page.wait_for_timeout(2000)

        # 点击登录触发二维码
        try:
            await page.click('text=登录', timeout=5000)
            await page.wait_for_timeout(3000)
        except:
            pass

        # 提取 QR 图片 src
        qr_img = page.locator('.qrcode img').first
        src = await qr_img.get_attribute('src')

        qr_data_url = src if src.startswith('data:image') else None

        if qr_data_url:
            # 同时保存到文件
            with open(QR_OUT, 'w') as f:
                json.dump({'data_url': qr_data_url}, f)
            print(f'QR saved to {QR_OUT}')

        print('DATA_URL:', qr_data_url[:80] if qr_data_url else 'NONE')
        await browser.close()
        return qr_data_url

asyncio.run(serve_qr())
