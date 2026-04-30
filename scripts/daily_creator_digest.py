"""
Pull Xiaohongshu creator「笔记基础数据」表并导出到桌面 CSV（便于每日复盘 / 推送摘要）。

依赖：已在创作者中心登录（check-login 通过）。

用法:

  python scripts/daily_creator_digest.py
  python scripts/daily_creator_digest.py --account myacc --page-size 20
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _desktop() -> Path:
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    d = Path(profile) / "Desktop"
    return d if d.is_dir() else Path.home() / "Desktop"


def main() -> None:
    parser = argparse.ArgumentParser(description="导出创作者 content-data 到桌面 CSV")
    parser.add_argument("--account", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--page-num", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=10)
    parser.add_argument(
        "--csv-file",
        default=None,
        help="默认：桌面 xhs_creator_digest_YYYYMMDD.csv",
    )
    args = parser.parse_args()

    csv_path = args.csv_file
    if not csv_path:
        csv_path = str(_desktop() / f"xhs_creator_digest_{datetime.now().strftime('%Y%m%d')}.csv")

    cmd = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "cdp_publish.py"),
    ]
    if args.account:
        cmd.extend(["--account", args.account])
    cmd.extend(
        [
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--reuse-existing-tab",
            "content-data",
            "--page-num",
            str(args.page_num),
            "--page-size",
            str(args.page_size),
            "--csv-file",
            csv_path,
        ]
    )

    print(f"[digest] Writing: {csv_path}")
    proc = subprocess.run(cmd, cwd=SCRIPT_DIR)
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
