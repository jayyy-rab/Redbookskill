"""
Shared runtime configuration helpers.

Purpose:
- Resolve runtime host/port/account consistently for local CDP mode.
- Centralize safe reads for config JSON files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CONFIG_DIR = REPO_ROOT / "config"
RUNNER_FILE = CONFIG_DIR / "runner.json"
ACCOUNTS_FILE = CONFIG_DIR / "accounts.json"


def _is_local_host(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    if isinstance(payload, dict):
        return payload
    return dict(default)


def read_runner_config() -> dict[str, Any]:
    return _read_json(RUNNER_FILE, {})


def read_accounts_config() -> dict[str, Any]:
    return _read_json(ACCOUNTS_FILE, {"default_account": "default", "accounts": {}})


def resolve_account_port(account_name: str | None, fallback: int = 9222) -> int:
    """
    Resolve account-specific CDP port from config/accounts.json.
    """
    cfg = read_accounts_config()
    accounts = cfg.get("accounts", {})
    if not isinstance(accounts, dict):
        return int(fallback)

    name = (account_name or "").strip() or str(cfg.get("default_account") or "default")
    entry = accounts.get(name, {})
    if not isinstance(entry, dict):
        return int(fallback)

    raw = entry.get("port")
    try:
        val = int(raw)
        if val > 0:
            return val
    except Exception:
        pass
    return int(fallback)


def resolve_runtime_target(
    *,
    host: str,
    port: int,
    account: str | None,
    port_explicit: bool,
    account_explicit: bool,
) -> tuple[str | None, int, dict[str, Any]]:
    """
    Resolve runtime account/port with runner + account config fallback.

    Returns:
        (account, port, meta)
    """
    resolved_host = (host or "127.0.0.1").strip() or "127.0.0.1"
    resolved_port = int(port)
    resolved_account = account
    meta: dict[str, Any] = {
        "host": resolved_host,
        "port_source": "arg",
        "account_source": "arg" if account_explicit else "input",
    }

    if not _is_local_host(resolved_host):
        return resolved_account, resolved_port, meta

    runner = read_runner_config()
    runner_account = str(runner.get("runner_account") or "").strip()
    runner_port = runner.get("runner_port")

    if not account_explicit and not (resolved_account or "").strip() and runner_account:
        resolved_account = runner_account
        meta["account_source"] = "runner"
    elif not account_explicit and runner_account and not (resolved_account or "").strip():
        resolved_account = runner_account
        meta["account_source"] = "runner"

    if not port_explicit:
        try:
            if runner_port:
                rp = int(runner_port)
                if rp > 0:
                    resolved_port = rp
                    meta["port_source"] = "runner"
        except Exception:
            pass

        if meta["port_source"] != "runner":
            resolved_port = resolve_account_port(resolved_account, fallback=resolved_port)
            meta["port_source"] = "account_or_default"

    return resolved_account, int(resolved_port), meta

