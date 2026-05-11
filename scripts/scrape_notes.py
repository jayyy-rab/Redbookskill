"""
Xiaohongshu keyword note scraper via CDP.

Uses an existing Chrome instance started with remote debugging
(for example http://127.0.0.1:9222), opens search pages, scrolls,
extracts note cards, and exports CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
import websockets.sync.client as ws_client


DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222
DEFAULT_SCROLL_COUNT = 5
DEFAULT_SCROLL_PAUSE = 2.0
DEFAULT_OUTPUT = Path("tmp/xhs_notes.csv")


class CDPClient:
    """Minimal CDP client over a single page WebSocket."""

    def __init__(self, ws_url: str) -> None:
        self.ws = ws_client.connect(ws_url, open_timeout=10)
        self._msg_id = 0

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._msg_id += 1
        message = {"id": self._msg_id, "method": method, "params": params or {}}
        self.ws.send(json.dumps(message, ensure_ascii=False))
        while True:
            raw = self.ws.recv()
            data = json.loads(raw)
            if data.get("id") == self._msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error in {method}: {data['error']}")
                return data.get("result", {})

    def evaluate(self, expression: str, return_by_value: bool = True) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": return_by_value,
                "awaitPromise": True,
            },
        )
        return result.get("result", {}).get("value")


def get_page_ws_url(host: str, port: int) -> str:
    resp = requests.get(f"http://{host}:{port}/json/list", timeout=5)
    resp.raise_for_status()
    targets = resp.json()
    if not targets:
        raise RuntimeError("No CDP targets found. Open a browser tab first.")

    for target in targets:
        url = str(target.get("url", ""))
        if "xiaohongshu.com" in url:
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                return str(ws_url)

    ws_url = targets[0].get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("No websocket debugger url in CDP targets.")
    return str(ws_url)


def scrape_keyword(
    cdp: CDPClient,
    keyword: str,
    scroll_count: int,
    scroll_pause: float,
) -> list[dict[str, str]]:
    encoded_kw = urllib.parse.quote(keyword)
    search_url = f"https://www.xiaohongshu.com/search_result?keyword={encoded_kw}&type=51"
    cdp.call("Page.navigate", {"url": search_url})
    time.sleep(4)

    rows: list[dict[str, str]] = []
    for i in range(scroll_count):
        cdp.evaluate(f"window.scrollBy(0, {800 + i * 100});")
        time.sleep(scroll_pause)
        batch = cdp.evaluate(
            """
            (() => {
              const items = document.querySelectorAll(
                '.note-item, .feeds-page .note, .search-trending-content .note-item, [class*="note"]'
              );
              return Array.from(items).map((el) => {
                const titleEl = el.querySelector('.title, .note-content .title, [class*="title"], .desc');
                const authorEl = el.querySelector('.author, .nickname, [class*="author"], [class*="user"]');
                const likeEl = el.querySelector('[class*="like"] span, [class*="like"] em, .count');
                const collectEl = el.querySelector('[class*="collect"] span, [class*="collect"] em');
                const commentEl = el.querySelector('[class*="comment"] span, [class*="comment"] em');
                const linkEl = el.querySelector('a');
                return {
                  title: titleEl ? titleEl.innerText.trim() : '',
                  author: authorEl ? authorEl.innerText.trim() : '',
                  like: likeEl ? likeEl.innerText.trim() : '',
                  collect: collectEl ? collectEl.innerText.trim() : '',
                  comment: commentEl ? commentEl.innerText.trim() : '',
                  link: linkEl ? linkEl.href : ''
                };
              }).filter((item) => item.title && item.title.length > 5);
            })()
            """
        )
        if isinstance(batch, list):
            for item in batch:
                if isinstance(item, dict):
                    rows.append(
                        {
                            "keyword": keyword,
                            "title": str(item.get("title", "")),
                            "author": str(item.get("author", "")),
                            "like": str(item.get("like", "")),
                            "collect": str(item.get("collect", "")),
                            "comment": str(item.get("comment", "")),
                            "link": str(item.get("link", "")),
                        }
                    )
    return rows


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for row in rows:
        key = row.get("title", "").strip()[:100]
        if key and key not in seen_keys:
            seen_keys.add(key)
            deduped.append(row)
    return deduped


def write_csv(rows: list[dict[str, str]], output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    fields = ["keyword", "title", "author", "like", "collect", "comment", "link"]
    with output_file.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Xiaohongshu notes by keywords via CDP.")
    parser.add_argument("--host", default=DEFAULT_CDP_HOST, help="CDP host, default 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_CDP_PORT, help="CDP port, default 9222")
    parser.add_argument(
        "--keywords",
        required=True,
        help='Comma-separated keywords, e.g. "AI副业,AI产品经理,大学生搞钱"',
    )
    parser.add_argument("--scroll-count", type=int, default=DEFAULT_SCROLL_COUNT)
    parser.add_argument("--scroll-pause", type=float, default=DEFAULT_SCROLL_PAUSE)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output CSV path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if not keywords:
        raise ValueError("No valid keywords provided.")

    ws_url = get_page_ws_url(args.host, args.port)
    print(f"[scrape_notes] Using CDP target: {ws_url}")

    client = CDPClient(ws_url)
    try:
        all_rows: list[dict[str, str]] = []
        for kw in keywords:
            print(f"[scrape_notes] Scraping keyword: {kw}")
            all_rows.extend(
                scrape_keyword(
                    cdp=client,
                    keyword=kw,
                    scroll_count=args.scroll_count,
                    scroll_pause=args.scroll_pause,
                )
            )
            time.sleep(1)

        deduped = dedupe_rows(all_rows)
        out = Path(args.output).expanduser()
        write_csv(deduped, out)
        print(f"[scrape_notes] Rows: {len(deduped)}")
        print(f"[scrape_notes] Saved to: {out}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
