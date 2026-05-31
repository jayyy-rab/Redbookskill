"""
直接用 Playwright 连接 Chrome，完成 fill 流程（绕过 SELENIUM_WS 问题）
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from playwright.async_api import async_playwright

WS_URL = "ws://localhost:9222/devtools/browser/4a852610-d7d6-4283-af01-5b452c171c85"
IMAGE = r"C:\Users\lyh17\.agents\skills\redbookskills\scripts\test_cover.jpg"
TITLE = "今天测试一下小红书自动发布~"
CONTENT = "治愈系生活方式，感受每一天的美好 ☀️ #自动发布测试"

async def do_fill():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(WS_URL)
        page = await browser.new_page()
        await page.goto('https://www.xiaohongshu.com', timeout=15000)
        await page.wait_for_timeout(2000)

        # 点击发布按钮
        print("点击发布按钮...")
        publish_btn = page.locator('text=发布')
        if await publish_btn.count() > 0:
            await publish_btn.first.click()
        else:
            # 尝试找相机图标或其他发布入口
            btn = page.locator('[class*="publish"]').first
            if await btn.count() > 0:
                await btn.click()
        await page.wait_for_timeout(3000)
        print("当前URL:", page.url)

        # 检查弹窗
        dialog = page.locator('[class*="dialog"]').first
        if await dialog.count() > 0 and await dialog.is_visible():
            print("检测到弹窗")
            # 找上传图片入口
            upload_area = page.locator('[class*="upload"]').first
            if await upload_area.count() > 0:
                print("上传区找到，准备上传图片...")
                await upload_area.set_input_files(IMAGE)
                await page.wait_for_timeout(2000)
                print("图片上传成功")
            else:
                print("未找到上传区，尝试截图调试...")
                await page.screenshot(path='debug_fill.png')
                print("截图保存: debug_fill.png")
        
        # 填充标题和内容
        title_input = page.locator('input[placeholder*="标题"], [class*="title"] input').first
        if await title_input.count() > 0 and await title_input.is_visible():
            await title_input.fill(TITLE)
            print("标题填充成功")

        content_area = page.locator('textarea, [class*="content"], [class*="desc"]').first
        if await content_area.count() > 0 and await content_area.is_visible():
            await content_area.fill(CONTENT)
            print("内容填充成功")

        await page.wait_for_timeout(1000)
        print("Fill 完成，当前URL:", page.url)
        await browser.close()

asyncio.run(do_fill())
