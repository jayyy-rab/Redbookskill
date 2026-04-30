#!/usr/bin/env python3
"""
Start one Chrome (with remote debugging) per Xiaohongshu account profile.

Why: Automation (`publish_pipeline` / Picset) often restarts Chrome on ONE port per run,
so users may never see «two accounts» at once. Logging in separately is easier when
multiple Chrome instances are already bound to different `--remote-debugging-port`s
(and different `--user-data-dir`s from account_manager).

Prerequisites in config/accounts.json:
  • Each account you care about MUST have distinct positive integer "port".

Examples (repo root):
  python scripts/start_multi_chrome_accounts.py --accounts acc_a acc_b

  python scripts/start_multi_chrome_accounts.py

Windows: multiple windows may fold under one taskbar Chrome icon → click it and pick
another window / use Alt+Tab.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from account_manager import list_accounts  # noqa: E402
from chrome_launcher import is_port_open, launch_chrome  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="为配置了独立 CDP 端口的账号各启动一个 Chrome 窗口（便于双开登录）",
    )
    parser.add_argument(
        "--accounts",
        nargs="*",
        default=None,
        help="账号名列表；不传则启动所有在 accounts.json 里配置了 port 字段的账号。",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头启动（不推荐用于首次扫码登录小红书/Picset）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印将要使用的账号/端口组合，不真实启动浏览器。",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    want: set[str] | None = None
    if args.accounts:
        want = {a.strip() for a in args.accounts if a.strip()}

    rows = list_accounts()

    launches: list[tuple[str, int]] = []
    skipped: list[tuple[str, str]] = []

    for row in sorted(
        rows,
        key=lambda r: (
            (r.get("port") is None) or not isinstance(r.get("port"), int),
            int(r["port"]) if isinstance(r.get("port"), int) else 0,
            str(r.get("name") or ""),
        ),
    ):
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        if want is not None and name not in want:
            continue
        raw_port = row.get("port")
        if not isinstance(raw_port, int) or raw_port <= 0:
            skipped.append(
                (
                    name,
                    "未配置独立 CDP port。请编辑 config/accounts.json，或运行："
                    ' python scripts/account_manager.py update 账号名 --port 端口',
                ),
            )
            continue
        port = int(raw_port)

        launches.append((name, port))

    print(
        "[start-multi-chrome] 计划启动："
        + (", ".join(f"{n}→{p}" for n, p in launches) if launches else "（无可启动条目）"),
        flush=True,
    )
    if skipped:
        for n, reason in skipped:
            print(f"[start-multi-chrome] 跳过 {n}: {reason}", flush=True)

    if args.dry_run:
        return

    if not launches:
        print("[start-multi-chrome] 无事可做：请至少在 accounts.json 给测试账号写上互不相同 port。", flush=True)
        sys.exit(0)

    for name, port in launches:
        if is_port_open(port):
            print(
                f"[start-multi-chrome] 跳过 {name}（端口 {port} 已被监听，大概率已有浏览器）。",
                flush=True,
            )
            continue
        print(f"[start-multi-chrome] 启动 {name} ←→ 调试端口 {port} …", flush=True)
        proc = launch_chrome(port=port, headless=bool(args.headless), account=name)
        if proc is None and is_port_open(port):
            print(f"[start-multi-chrome] 端口 {port} 已由其它进程监听。", flush=True)
        elif proc is None:
            print(
                f"[start-multi-chrome] Warning: 未检测到新进程但端口仍处于关闭—请检查报错。 account={name} port={port}",
                flush=True,
            )

    print(
        "[start-multi-chrome] 已完成启动尝试。请到各 Chrome 窗口内分别登录 xiaohongshu.com / Picset；"
        "任务栏可能被折叠为多窗口共用图标。",
        flush=True,
    )


if __name__ == "__main__":
    main()
