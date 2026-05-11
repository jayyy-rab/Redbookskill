"""
One-time runner initialization for customer delivery.

Purpose:
- Bind one fixed local browser profile/account for automation
- Bind one fixed CDP port
- Optionally set browser executable path
- Verify CDP connectivity + current creator login status
- Write a stable runner config for later commands
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from account_manager import add_account, account_exists, get_profile_dir, set_default_account
from chrome_launcher import ensure_chrome
from cdp_publish import XiaohongshuPublisher


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CONFIG_DIR = REPO_ROOT / "config"
ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"
BROWSER_FILE = CONFIG_DIR / "browser.json"
RUNNER_FILE = CONFIG_DIR / "runner.json"


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return data if isinstance(data, dict) else default


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _set_account_port(account_name: str, port: int) -> None:
    data = _load_json(
        ACCOUNTS_FILE,
        {
            "default_account": "default",
            "accounts": {},
        },
    )
    accounts = data.setdefault("accounts", {})
    row = accounts.setdefault(
        account_name,
        {
            "alias": account_name,
            "profile_dir": get_profile_dir(account_name),
            "created_at": datetime.now().isoformat(),
        },
    )
    row["port"] = int(port)
    if not row.get("profile_dir"):
        row["profile_dir"] = get_profile_dir(account_name)
    _save_json(ACCOUNTS_FILE, data)


def _set_browser_path(browser_path: str) -> None:
    path = browser_path.strip()
    if not path:
        return
    if not os.path.isfile(path):
        raise SystemExit(f"browser path not found: {path}")
    _save_json(BROWSER_FILE, {"browser_path": path})


def _check_login(host: str, port: int, account: str) -> bool:
    publisher = XiaohongshuPublisher(host=host, port=port, account_name=account)
    try:
        publisher.connect(reuse_existing_tab=True)
        return bool(publisher.check_login())
    finally:
        try:
            publisher.disconnect()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize fixed browser runner for customer machine"
    )
    parser.add_argument("--account", default="runner", help="Fixed local account/profile name")
    parser.add_argument("--alias", default="Runner Profile", help="Display alias when account is newly created")
    parser.add_argument("--port", type=int, default=9322, help="Fixed CDP port")
    parser.add_argument("--host", default="127.0.0.1", help="CDP host (default local)")
    parser.add_argument(
        "--browser-path",
        default="",
        help="Optional browser executable path to write into config/browser.json",
    )
    parser.add_argument(
        "--set-default",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Set this account as default in config/accounts.json",
    )
    parser.add_argument(
        "--no-login-check",
        action="store_true",
        help="Skip creator login status check",
    )
    args = parser.parse_args()

    account = (args.account or "").strip() or "runner"
    port = int(args.port)
    host = (args.host or "127.0.0.1").strip()

    if not account_exists(account):
        ok = add_account(account, args.alias)
        if not ok:
            raise SystemExit(f"failed to create account '{account}'")
        print(f"[init] created account: {account}")
    else:
        print(f"[init] account exists: {account}")

    _set_account_port(account, port)
    print(f"[init] bound account port: {account} -> {port}")

    if args.set_default:
        if set_default_account(account):
            print(f"[init] set default account: {account}")
        else:
            raise SystemExit(f"failed to set default account: {account}")

    if args.browser_path:
        _set_browser_path(args.browser_path)
        print(f"[init] browser path set: {args.browser_path}")

    profile_dir = get_profile_dir(account)
    _save_json(
        RUNNER_FILE,
        {
            "runner_account": account,
            "runner_port": port,
            "runner_host": host,
            "profile_dir": profile_dir,
            "context_mode_default": True,
            "updated_at": datetime.now().isoformat(),
        },
    )
    print(f"[init] runner config written: {RUNNER_FILE}")

    if not ensure_chrome(port=port, headless=False, account=account):
        raise SystemExit("failed to launch Chrome on configured port")
    print("[init] chrome ready")

    if not args.no_login_check:
        logged_in = _check_login(host=host, port=port, account=account)
        print(f"[init] creator_login={'yes' if logged_in else 'no'}")
        if not logged_in:
            print("[init] please scan login in opened Chrome, then rerun this command.")

    print("[init] done")
    print(
        "next: python scripts/publish_pipeline.py --account "
        f"{account} --host {host} --port {port} --context-key xhs:main --title-file ... --content-file ... --images ... --preview"
    )


if __name__ == "__main__":
    main()

