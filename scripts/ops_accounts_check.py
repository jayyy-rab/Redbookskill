"""
依次检测各已配置账号在创作者中心是否仍登录（同一 Chrome CDP 会话下切换账号请自行配合 chrome_launcher）。

用法:

  python scripts/ops_accounts_check.py
  python scripts/ops_accounts_check.py --port 9222
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from account_manager import list_accounts  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="多账号登录状态探测（check-login）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    args = parser.parse_args()

    accounts = list_accounts()
    if not accounts:
        print("No accounts in config.")
        sys.exit(1)

    failures = 0
    for row in accounts:
        name = row.get("name") or row.get("account") or ""
        if not name:
            continue
        cmd = [
            sys.executable,
            os.path.join(SCRIPT_DIR, "cdp_publish.py"),
            "--account",
            name,
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--reuse-existing-tab",
            "check-login",
        ]
        print(f"\n[ops] Account: {name}")
        proc = subprocess.run(cmd, cwd=SCRIPT_DIR)
        if proc.returncode != 0:
            failures += 1

    print(f"\n[ops] Done. Non-zero accounts: {failures}/{len(accounts)}")
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
