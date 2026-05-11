"""
Batch login status checker for multiple Xiaohongshu accounts.

Usage:
  python scripts/batch_check_login.py
  python scripts/batch_check_login.py --accounts acc_a acc_b
  python scripts/batch_check_login.py --open-login-for-failed
  python scripts/batch_check_login.py --json-out tmp/login_audit.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from account_manager import get_default_account, list_accounts
from cdp_publish import CDPError, XiaohongshuPublisher
from chrome_launcher import ensure_chrome


def _resolve_targets(explicit_accounts: list[str] | None) -> list[dict[str, Any]]:
    all_accounts = list_accounts()
    by_name = {acc["name"]: acc for acc in all_accounts}
    if explicit_accounts:
        targets: list[dict[str, Any]] = []
        for name in explicit_accounts:
            if name not in by_name:
                raise SystemExit(f"Unknown account: {name}")
            targets.append(by_name[name])
        return targets
    return all_accounts


def _port_for_account(account_info: dict[str, Any]) -> int:
    raw = account_info.get("port")
    if isinstance(raw, int) and raw > 0:
        return raw
    return 9222


def _check_one(account_name: str, port: int) -> tuple[bool, str]:
    if not ensure_chrome(port=port, headless=False, account=account_name):
        return False, "chrome_start_failed"

    publisher = XiaohongshuPublisher(
        host="127.0.0.1",
        port=port,
        account_name=account_name,
    )
    try:
        publisher.connect(reuse_existing_tab=True)
        ok = publisher.check_login()
        return (ok, "ok" if ok else "not_logged_in")
    except CDPError as exc:
        return False, f"cdp_error:{exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"error:{exc}"
    finally:
        try:
            publisher.disconnect()
        except Exception:
            pass


def _open_login_page(account_name: str, port: int) -> None:
    if not ensure_chrome(port=port, headless=False, account=account_name):
        return
    publisher = XiaohongshuPublisher(
        host="127.0.0.1",
        port=port,
        account_name=account_name,
    )
    try:
        publisher.connect(reuse_existing_tab=True)
        publisher.open_login_page()
    finally:
        try:
            publisher.disconnect()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch check login status for accounts.")
    parser.add_argument("--accounts", nargs="+", default=None, help="Account names to check.")
    parser.add_argument(
        "--open-login-for-failed",
        action="store_true",
        help="Open login page for accounts that are not logged in.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="Optional output JSON report path.",
    )
    args = parser.parse_args()

    targets = _resolve_targets(args.accounts)
    default_account = get_default_account()

    report: dict[str, Any] = {
        "checked_at": datetime.now().isoformat(),
        "default_account": default_account,
        "total": len(targets),
        "logged_in": 0,
        "failed": 0,
        "accounts": [],
    }

    print(f"[batch-check] checking {len(targets)} account(s)...")
    for acc in targets:
        name = str(acc["name"])
        port = _port_for_account(acc)
        ok, reason = _check_one(name, port)
        if ok:
            report["logged_in"] += 1
            status = "logged_in"
        else:
            report["failed"] += 1
            status = "not_logged_in"
        report["accounts"].append(
            {
                "name": name,
                "port": port,
                "status": status,
                "reason": reason,
                "is_default": bool(acc.get("is_default")),
            }
        )
        print(f"[batch-check] {name:<20} port={port:<5} status={status} reason={reason}")
        if (not ok) and args.open_login_for_failed:
            print(f"[batch-check] opening login page for {name}...")
            _open_login_page(name, port)

    if args.json_out:
        out_path = os.path.abspath(args.json_out)
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[batch-check] json report -> {out_path}")

    print(
        f"[batch-check] done: total={report['total']} "
        f"logged_in={report['logged_in']} failed={report['failed']}"
    )


if __name__ == "__main__":
    main()

