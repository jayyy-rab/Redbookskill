"""
小红书关键词笔记数据抓取
使用已登录的浏览器 CDP 会话（localhost:9222）
采集：标题、作者、点赞数、收藏数、评论数
"""

import json
import time
import csv
import urllib.request
import urllib.parse

CDP_URL = "http://localhost:9222"
KEYWORDS = ["AI 副业", "AI 产品经理", "大学生搞钱"]
OUTPUT_FILE = "C:/Users/lyh17/.openclaw/workspace/xhs_notes.csv"
SCROLL_COUNT = 5   # 每个关键词滚动的页数
SCROLL_PAUSE = 2.0  # 秒

def cdp(cmd, params=None):
    payload = json.dumps({"id": 1, "method": cmd, "params": params or {}).encode()
    req = urllib.request.Request(
        f"{CDP_URL}/json/runtime evaluate",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def js(js_code):
    result = cdp("Runtime.evaluate", {"expression": js_code, "returnByValue": True})
    return result.get("result", {}).get("value")

def scroll_and_get_cards():
    cards = []
    for i in range(SCROLL_COUNT):
        js(f"window.scrollBy(0, {800 + i*100})")
        time.sleep(SCROLL_PAUSE)
        batch = js("""
            (() => {
                const items = document.querySelectorAll('.note-item, .feeds-page .note, .search-trending-content .note-item, [class*='note']');
                return Array.from(items).map(el => {
                    const titleEl = el.querySelector('.title, .note-content .title, [class*='title'], .desc');
                    const authorEl = el.querySelector('.author, .nickname, [class*='author'], [class*='user']');
                    const likeEl = el.querySelector('[class*="like"] span, [class*="like"] em, .count');
                    const collectEl = el.querySelector('[class*="collect"] span, [class*="collect"] em');
                    const commentEl = el.querySelector('[class*="comment"] span, [class*="comment"] em');
                    return {
                        title: titleEl ? titleEl.innerText.trim() : '',
                        author: authorEl ? authorEl.innerText.trim() : '',
                        like: likeEl ? likeEl.innerText.trim() : '',
                        collect: collectEl ? collectEl.innerText.trim() : '',
                        comment: commentEl ? commentEl.innerText.trim() : '',
                        link: el.querySelector('a') ? el.querySelector('a').href : ''
                    };
                }).filter(item => item.title && item.title.length > 5);
            })()
        """)
        if batch:
            cards.extend(batch)
    return cards

def get_cdp_target():
    req = urllib.request.urlopen(f"{CDP_URL}/json/list", timeout=5)
    targets = json.loads(req.read())
    for t in targets:
        if "xiaohongshu" in t.get("url", "") or "xhs" in t.get("url", ""):
            return t
    return targets[0] if targets else None

def main():
    target = get_cdp_target()
    if not target:
        print("ERROR: No CDP target found. Open Xiaohongshu first.")
        return

    ws_url = target.get("webSocketDebuggerUrl", "")
    print(f"Using target: {target.get('title', 'unknown')}")

    all_data = []
    seen_titles = set()

    for kw in KEYWORDS:
        print(f"\n=== Searching: {kw} ===")
        encoded_kw = urllib.parse.quote(kw)
        # 访问小红书搜索页
        cdp("Page.navigate", {"url": f"https://www.xiaohongshu.com/search result?keyword={encoded_kw}&type=51"})
        time.sleep(4)

        cards = scroll_and_get_cards()
        print(f"  Found {len(cards)} cards")

        for card in cards:
            title = card.get("title", "")[:200]
            if title and title not in seen_titles:
                seen_titles.add(title)
                all_data.append({
                    "关键词": kw,
                    "标题": title,
                    "作者": card.get("author", ""),
                    "点赞": card.get("like", ""),
                    "收藏": card.get("collect", ""),
                    "评论": card.get("comment", ""),
                    "链接": card.get("link", "")
                })

        time.sleep(2)

    # 去重
    unique = []
    seen = set()
    for row in all_data:
        key = row["标题"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(row)

    print(f"\nTotal (deduped): {len(unique)} rows")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["关键词", "标题", "作者", "点赞", "收藏", "评论", "链接"])
        writer.writeheader()
        writer.writerows(unique)

    print(f"Saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()