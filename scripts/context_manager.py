"""
CDP browser-context manager for one Chrome process / multiple isolated sessions.

This module manages:
- create/reuse BrowserContext by context_key
- create/reuse page target inside that context
- resolve page websocket url for downstream page-level automation
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

import requests
import websockets.sync.client as ws_client

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
CONTEXT_STATE_FILE = os.path.join(REPO_ROOT, "tmp", "browser_contexts.json")


def _is_local_host(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _state_key(host: str, port: int, context_key: str) -> str:
    return f"{host}:{int(port)}:{context_key.strip()}"


def _load_state() -> dict[str, Any]:
    if not os.path.exists(CONTEXT_STATE_FILE):
        return {"entries": {}}
    try:
        with open(CONTEXT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"entries": {}}
    if not isinstance(data, dict):
        return {"entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def _save_state(payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONTEXT_STATE_FILE), exist_ok=True)
    with open(CONTEXT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _get_browser_ws_url(host: str, port: int) -> str:
    resp = requests.get(
        f"http://{host}:{int(port)}/json/version",
        timeout=5,
        proxies={"http": None, "https": None} if _is_local_host(host) else None,
    )
    resp.raise_for_status()
    data = resp.json()
    ws_url = str(data.get("webSocketDebuggerUrl") or "").strip()
    if not ws_url:
        raise RuntimeError("browser websocket debugger url missing")
    return ws_url


def _browser_send(ws, msg_id: int, method: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> tuple[int, dict[str, Any]]:
    payload: dict[str, Any] = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params
    ws.send(json.dumps(payload))
    deadline = time.monotonic() + max(0.5, float(timeout))
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0:
            raise RuntimeError(f"timeout waiting for browser CDP response: {method}")
        raw = ws.recv(timeout=max(0.1, remain))
        data = json.loads(raw)
        if data.get("id") == msg_id:
            if "error" in data:
                raise RuntimeError(f"browser CDP error {method}: {data['error']}")
            return msg_id + 1, data.get("result", {})


def _resolve_target_ws_url(host: str, port: int, target_id: str, retries: int = 20) -> str:
    for _ in range(max(1, retries)):
        resp = requests.get(
            f"http://{host}:{int(port)}/json/list",
            timeout=5,
            proxies={"http": None, "https": None} if _is_local_host(host) else None,
        )
        resp.raise_for_status()
        items = resp.json()
        if isinstance(items, list):
            for t in items:
                if str(t.get("id")) == str(target_id):
                    ws_url = str(t.get("webSocketDebuggerUrl") or "").strip()
                    if ws_url:
                        return ws_url
        time.sleep(0.2)
    raise RuntimeError(f"target websocket url not found for targetId={target_id}")


def ensure_context_target(
    *,
    host: str,
    port: int,
    context_key: str,
    initial_url: str,
    reuse_existing_tab: bool = False,
    target_url_prefix: str = "",
) -> dict[str, str]:
    """
    Ensure one page target exists under the BrowserContext identified by context_key.
    Return dict: {browserContextId, targetId, webSocketDebuggerUrl}.
    """
    key = (context_key or "").strip()
    if not key:
        raise RuntimeError("context_key is required")

    browser_ws = _get_browser_ws_url(host, port)
    ws = ws_client.connect(browser_ws)
    msg_id = 1
    try:
        msg_id, targets_result = _browser_send(ws, msg_id, "Target.getTargets")
        target_infos = targets_result.get("targetInfos") if isinstance(targets_result, dict) else []
        if not isinstance(target_infos, list):
            target_infos = []

        state = _load_state()
        entries = state.setdefault("entries", {})
        k = _state_key(host, port, key)
        entry = entries.get(k, {}) if isinstance(entries.get(k), dict) else {}
        browser_context_id = str(entry.get("browserContextId") or "").strip()

        # Recover context id if already exists in current target list.
        if not browser_context_id:
            for info in target_infos:
                if str(info.get("type")) != "page":
                    continue
                ctx = str(info.get("browserContextId") or "").strip()
                if ctx and str(info.get("title") or "").strip().endswith(f"[ctx:{key}]"):
                    browser_context_id = ctx
                    break

        if not browser_context_id:
            msg_id, create_ctx = _browser_send(ws, msg_id, "Target.createBrowserContext")
            browser_context_id = str(create_ctx.get("browserContextId") or "").strip()
            if not browser_context_id:
                raise RuntimeError("failed to create browser context")

        def _pick_target_id() -> str:
            nonlocal msg_id, target_infos
            msg_id, latest = _browser_send(ws, msg_id, "Target.getTargets")
            infos = latest.get("targetInfos") if isinstance(latest, dict) else []
            if not isinstance(infos, list):
                infos = []
            target_infos = infos
            pages = [
                t for t in infos
                if str(t.get("type")) == "page"
                and str(t.get("browserContextId") or "") == browser_context_id
            ]
            if target_url_prefix:
                for t in pages:
                    if str(t.get("url") or "").startswith(target_url_prefix):
                        return str(t.get("targetId") or "")
            if reuse_existing_tab and pages:
                return str(pages[0].get("targetId") or "")
            return ""

        target_id = _pick_target_id()
        if not target_id:
            create_params: dict[str, Any] = {
                "url": initial_url,
                "browserContextId": browser_context_id,
            }
            msg_id, create_target = _browser_send(ws, msg_id, "Target.createTarget", create_params)
            target_id = str(create_target.get("targetId") or "").strip()
            if not target_id:
                raise RuntimeError("failed to create target in browser context")
        ws_url = _resolve_target_ws_url(host, port, target_id)

        entries[k] = {
            "context_key": key,
            "browserContextId": browser_context_id,
            "targetId": target_id,
            "updated_at": datetime.now().isoformat(),
        }
        _save_state(state)
        return {
            "browserContextId": browser_context_id,
            "targetId": target_id,
            "webSocketDebuggerUrl": ws_url,
        }
    finally:
        try:
            ws.close()
        except Exception:
            pass
