"""
Picset web automation (MVP) via Chrome DevTools Protocol.

Flow:
1) Open Picset page and check login state (best effort).
2) Fill prompt and optionally upload reference images.
3) Click a likely "generate" button.
4) Collect generated image URLs from the page.
5) Download generated images locally.
6) Optionally call publish_pipeline.py to publish to Xiaohongshu.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from chrome_launcher import ensure_chrome  # noqa: E402
from cdp_publish import CDPError, XiaohongshuPublisher  # noqa: E402
from image_downloader import ImageDownloader  # noqa: E402
from run_lock import SingleInstanceError, single_instance  # noqa: E402

PICSET_AUTH_URL = "https://picsetai.com/zh-CN/auth"


def _is_local_host(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _read_text(path: str | None, inline_value: str | None, label: str) -> str:
    if path:
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
    else:
        value = (inline_value or "").strip()

    if not value:
        raise ValueError(f"{label} is empty.")
    return value


def _download_urls(urls: list[str], output_dir: str | None) -> list[str]:
    if not urls:
        return []
    downloader = ImageDownloader(temp_dir=output_dir)
    try:
        return downloader.download_all(urls)
    finally:
        # Keep files for downstream use when caller provided output_dir.
        # For auto temp dir, do not cleanup so generated assets remain accessible.
        pass


def _resolve_existing_files(paths: list[str] | None) -> list[str]:
    resolved: list[str] = []
    for path in paths or []:
        if not path:
            continue
        abs_path = os.path.abspath(path)
        if os.path.isfile(abs_path):
            resolved.append(abs_path)
        else:
            print(f"[picset] Warning: file not found, skipped: {path}")
    return resolved


def _extract_xhs_main_image_urls_from_output(raw_output: str, limit: int) -> list[str]:
    marker = "SEARCH_FEEDS_RESULT:\n"
    if marker not in raw_output:
        return []
    try:
        payload = json.loads(raw_output.split(marker, 1)[1])
    except Exception:
        return []

    urls: list[str] = []
    for feed in payload.get("feeds", []):
        note = feed.get("noteCard", {}) if isinstance(feed, dict) else {}
        cover = note.get("cover", {}) if isinstance(note, dict) else {}
        if isinstance(cover, dict):
            for key in ("urlDefault", "urlPre"):
                val = cover.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    urls.append(val)
                    break
        if len(urls) >= max(1, limit):
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped[: max(1, limit)]


def _fetch_xhs_reference_main_images(
    keyword: str,
    limit: int,
) -> list[str]:
    cmd = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "cdp_publish.py"),
        "--reuse-existing-tab",
        "search-feeds",
        "--keyword",
        keyword,
        "--sort-by",
        "最新",
    ]
    print(f"[picset] Fetching XHS reference covers by keyword: {keyword}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except Exception as exc:
        print(f"[picset] Failed to fetch XHS references: {exc}")
        return []

    if proc.returncode != 0:
        print(f"[picset] XHS fetch returned non-zero code: {proc.returncode}")
        return []

    urls = _extract_xhs_main_image_urls_from_output(proc.stdout, limit=limit)
    print(f"[picset] XHS reference covers fetched: {len(urls)}")
    return urls


def _file_is_image(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            sig = f.read(16)
    except OSError:
        return False
    if sig.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if sig.startswith(b"\xff\xd8\xff"):
        return True
    if sig[:4] == b"RIFF" and sig[8:12] == b"WEBP":
        return True
    if sig.startswith((b"GIF87a", b"GIF89a")):
        return True
    if sig.startswith(b"BM"):
        return True
    if len(sig) >= 12 and sig[4:12] == b"ftypavif":
        return True
    return False


def _parse_png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if data[12:16] != b"IHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def _parse_jpeg_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        i += 2
        if marker in {0xD8, 0xD9}:
            continue
        if i + 2 > len(data):
            return None
        seg_len = int.from_bytes(data[i:i + 2], "big")
        if seg_len < 2 or i + seg_len > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if i + 7 > len(data):
                return None
            height = int.from_bytes(data[i + 3:i + 5], "big")
            width = int.from_bytes(data[i + 5:i + 7], "big")
            return width, height
        i += seg_len
    return None


def _parse_webp_size(data: bytes) -> tuple[int, int] | None:
    """Parse canvas size from WEBP chunks (VP8X / VP8 / VP8L — not only VP8X)."""
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None

    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        cs = int.from_bytes(data[pos + 4 : pos + 8], "little")
        payload_start = pos + 8
        chunk_end = payload_start + cs
        if chunk_end > len(data):
            break
        payload = data[payload_start:chunk_end]

        if chunk_id == b"VP8X" and len(payload) >= 10:
            width = 1 + int.from_bytes(payload[4:7], "little")
            height = 1 + int.from_bytes(payload[7:10], "little")
            return width, height

        # Lossy VP8 bitstream
        if chunk_id == b"VP8 " and len(payload) >= 10:
            if payload[:3] == b"\x9d\x01\x2a":
                w = int.from_bytes(payload[6:8], "little") & 0x3FFF
                h = int.from_bytes(payload[8:10], "little") & 0x3FFF
                if w > 0 and h > 0:
                    return w, h

        # Lossless VP8L
        if chunk_id == b"VP8L" and len(payload) >= 5:
            bits = int.from_bytes(payload[:4], "little")
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            if w > 0 and h > 0:
                return w, h

        pos = chunk_end + (cs % 2)

    return None


def _read_image_size(path: str) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as f:
            data = f.read(65536)
    except OSError:
        return None

    png = _parse_png_size(data)
    if png:
        return png
    jpeg = _parse_jpeg_size(data)
    if jpeg:
        return jpeg
    webp = _parse_webp_size(data)
    if webp:
        return webp
    # For AVIF or unsupported formats, we keep the file if signature passes,
    # but skip strict dimension checks.
    if len(data) >= 12 and data[4:12] == b"ftypavif":
        return (9999, 9999)
    return None


def _validate_downloaded_images(
    paths: list[str],
    min_bytes: int = 50 * 1024,
    min_width: int = 512,
    min_height: int = 512,
) -> list[str]:
    valid: list[str] = []
    for path in paths:
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if size < min_bytes:
            print(f"[picset] Skip low-size image: {path} ({size} bytes)")
            continue
        if not _file_is_image(path):
            print(f"[picset] Skip non-image payload: {path}")
            continue
        dims = _read_image_size(path)
        if not dims:
            # Picset CDN webp occasionally uses layouts our parser skips.
            # Keep medium/large valid blobs so downstream PS postprocess still runs.
            if (
                size >= 60 * 1024
                and path.lower().endswith(".webp")
                and _file_is_image(path)
            ):
                print(
                    f"[picset] Keep webp without decoded dimensions "
                    f"({size // 1024} KB, likely Picset output): {path}"
                )
                valid.append(path)
                continue
            print(f"[picset] Skip image with unknown dimensions: {path}")
            continue
        width, height = dims
        if width < min_width or height < min_height:
            print(f"[picset] Skip low-resolution image: {path} ({width}x{height})")
            continue
        valid.append(path)
    return valid


def _as_json_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _evaluate_js(publisher: XiaohongshuPublisher, js_expression: str) -> Any:
    # Propagate CDPError as-is so callers (e.g. xhs_images_to_picset retries for
    # "Promise was collected") can classify transient DevTools failures.
    return publisher._evaluate(js_expression)


def _picset_session_and_workspace_js() -> str:
    """Shared DOM probe: real login/workstation vs marketing landing (Picset CN)."""
    return """
        (() => {
          const href = (window.location.href || "").toLowerCase();
          const raw = document.body?.innerText || "";
          const t = raw.toLowerCase();

          const visible = (el) => !!(el && el.offsetParent !== null);
          const fileInputs = [...document.querySelectorAll('input[type="file"]')].filter(visible).length;

          const workspaceReady =
            fileInputs >= 1 ||
            (t.includes("参考") && (t.includes("设计") || t.includes("图"))) ||
            t.includes("产品素材");

          const hasPwd = !!document.querySelector(
            'input[type="password"], input[name*="password" i], input[placeholder*="密码"]'
          );
          const hasEmail = !!document.querySelector(
            'input[type="email"], input[name*="email" i], input[placeholder*="邮箱"]'
          );
          const classicForm = !!(hasEmail && hasPwd) || !!(hasPwd && t.includes("登录"));

          const authLikePath =
            href.includes("/auth") || href.includes("/login") || href.includes("/sign");

          const phoneLoginLike =
            !!document.querySelector(
              'input[placeholder*="手机"], input[placeholder*="验证码"], input[type="tel"]'
            ) && (t.includes("验证码") || t.includes("登录"));

          const gateCopy =
            /请先登录|登录后|扫码登录|请登录后|未登录/.test(t) ||
            (/立即登录/.test(t) && !workspaceReady);

          const marketingLocked =
            !workspaceReady &&
            (classicForm || phoneLoginLike || authLikePath || gateCopy);

          const needsClassicLoginOnly = !!(classicForm || (hasPwd && phoneLoginLike));

          return {
            href: window.location.href || "",
            workspaceReady,
            marketingLocked,
            needsClassicLoginOnly,
            fileInputs,
            snippet: t.slice(0, 280),
          };
        })()
    """


def _wait_for_login_if_needed(publisher: XiaohongshuPublisher, timeout_seconds: int) -> None:
    """
    Wait until Picset no longer shows a login / auth gate.

    The workspace upload UI may appear only after clicking 「开始风格复刻」 — so we do not
    require upload slots here; use _require_picset_upload_ui after entering workspace.
    """
    deadline = time.time() + max(15, timeout_seconds)

    while time.time() < deadline:
        state = _evaluate_js(publisher, _picset_session_and_workspace_js())
        if not isinstance(state, dict):
            time.sleep(2)
            continue

        if state.get("workspaceReady"):
            print("[picset] Picset workspace already visible (upload UI).")
            return

        if state.get("marketingLocked") or state.get("needsClassicLoginOnly"):
            print(
                "[picset] Picset login / auth page detected — "
                "please complete login in the browser tab..."
            )
            time.sleep(2)
            continue

        print("[picset] Login wall cleared (or marketing home); continue to workspace entry.")
        return

    raise RuntimeError(
        "Picset：等待登录超时。请在浏览器中完成 Picset 登录后重新运行脚本。"
    )


def _require_picset_upload_ui(
    publisher: XiaohongshuPublisher,
    timeout_seconds: int = 90,
    phase: str = "",
) -> None:
    """After clicking entry CTAs, ensure upload slots actually appeared."""
    deadline = time.time() + max(10, timeout_seconds)
    label = f" ({phase})" if phase else ""
    while time.time() < deadline:
        state = _evaluate_js(publisher, _picset_session_and_workspace_js())
        if isinstance(state, dict) and state.get("workspaceReady"):
            print(f"[picset] Upload UI confirmed{label}.")
            return
        time.sleep(2)

    raise RuntimeError(
        "Picset：进入工作台后仍未看到上传区域。"
        "请在浏览器中确认已登录并停留在「风格复刻」页面，然后重试。"
    )


def _picset_try_set_batch_count(
    publisher: XiaohongshuPublisher,
    batch: int,
) -> dict[str, Any]:
    """
    Attempt to switch Picset UI「生成数量」to N 张 (including **1 张**) via labels / stepper /
    segmented control. Previously skipped N<=1, which left the UI stuck on e.g. 4 张.
    """
    if batch is None:
        return {"ok": False, "skipped": True, "reason": "batch_none", "batch": None}

    bn_int = max(1, min(16, int(batch)))
    bn = json.dumps(bn_int, ensure_ascii=False)
    result = _evaluate_js(
        publisher,
        f"""
        (async () => {{
          const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
          const visible = (el) => !!(el && el.offsetParent !== null);
          const n = {bn};
          let method = '';
          let hit = false;

          const pickOptionFromOpenList = async () => {{
            const optionNodes = [...document.querySelectorAll(
              'li,[role="option"],.ant-select-item-option,.ant-select-item-option-content,div,span'
            )].filter(visible);
            const targetOpt = optionNodes.find((el) => {{
              const t = (el.innerText || el.textContent || '').replace(/\\s+/g, '');
              return t === (String(n) + '张') || t.includes(String(n) + '张');
            }});
            if (targetOpt) {{
              targetOpt.click();
              await sleep(350);
              return true;
            }}
            return false;
          }};

          // 1) Prefer explicit "生成数量" field then open its select dropdown
          const allNodes = [...document.querySelectorAll('label,div,span,p')].filter(visible);
          const qtyLabel = allNodes.find((el) => {{
            const t = (el.innerText || el.textContent || '').replace(/\\s+/g, '');
            return t.includes('生成数量');
          }});
          if (qtyLabel) {{
            try {{
              const wrap = qtyLabel.closest('div,section,form') || qtyLabel.parentElement;
              const sel = wrap?.querySelector?.(
                '.ant-select-selector,.ant-select-selection-item,[role="combobox"],div[class*="select"],input'
              );
              if (sel) {{
                sel.click();
                await sleep(280);
                if (await pickOptionFromOpenList()) {{
                  hit = true;
                  method = 'qty_label_nearby_select';
                }}
              }}
            }} catch (_) {{}}
          }}

          // 1.5) Ant Design select fallback: infer by scope text
          if (!hit) {{
            try {{
              const selects = [...document.querySelectorAll('.ant-select,.ant-select-selector,[role="combobox"]')]
                .filter(visible);
              for (const s of selects) {{
                const wrap = s.closest('div,section,form') || s.parentElement;
                const scope = ((wrap?.innerText || '') + ' ' + (s.innerText || '')).replace(/\\s+/g, '');
                if (
                  !scope.includes('生成数量') &&
                  !scope.includes('1张') &&
                  !scope.includes('2张') &&
                  !scope.includes('4张')
                ) {{
                  continue;
                }}
                s.click();
                await sleep(280);
                if (await pickOptionFromOpenList()) {{
                  hit = true;
                  method = 'ant_select_generate_count';
                  break;
                }}
              }}
            }} catch (_) {{}}
          }}

          // 2) Generic dropdown scan fallback
          if (!hit) {{
          const triggerCandidates = [...document.querySelectorAll('div,button,span,label,[role="combobox"]')]
            .filter(visible);
          const qtyTrigger = triggerCandidates.find((el) => {{
            const t = (el.innerText || el.textContent || '').replace(/\\s+/g, '');
            return t.includes('生成数量') || t.includes(String(n) + '张');
          }});
          if (qtyTrigger) {{
            try {{
              qtyTrigger.click();
              await sleep(280);
              if (await pickOptionFromOpenList()) {{
                hit = true;
                method = 'dropdown_generate_count';
              }}
            }} catch (_) {{}}
          }}
          }}

          const clickable = [...document.querySelectorAll(
            'button,[role=\"button\"],label,[role=\"radio\"],a,span.ant-tag,span,div[class*=\"segment\"]'
          )].filter(visible);

          for (const el of clickable) {{
            const raw = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, '');
            if (!raw) continue;
            if (raw.includes(String(n) + '张') || raw.includes(n + '张详情') || raw === n + '张') {{
              el.click(); hit = true; method = 'label_' + raw.slice(0, 24); break;
            }}
          }}

          if (!hit) {{
            const inputs = [...document.querySelectorAll('input')].filter(visible);
            for (const inp of inputs) {{
              const lbl = (
                inp.getAttribute?.('aria-label') || inp.name || inp.id || inp.placeholder || ''
              );
              let scope =
                lbl +
                ' ' +
                ((inp.closest('section,div,label,form') || {{}})?.innerText || '').slice(0, 400);
              if (/[0-9]+\\s*张|数量|产出|张数/i.test(scope) || inp.type === 'number') {{
                inp.focus();
                inp.value = String(n);
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                hit = true;
                method = 'number_input';
                break;
              }}
            }}
          }}

          await sleep(400);
          return {{ ok: true, adjusted: hit, method: method || (hit ? '' : 'not_found'), batch: n }};
        }})()
        """,
    )
    return result if isinstance(result, dict) else {"ok": False, "adjusted": False}


def _fill_prompt_and_generate(
    publisher: XiaohongshuPublisher,
    prompt: str,
    reference_paths: list[str],
    batch_hint: int | None = None,
) -> dict[str, Any]:
    prompt_literal = _as_json_literal(prompt)
    refs_literal = _as_json_literal(reference_paths)
    if batch_hint is None:
        batch_lit = json.dumps(0)
    else:
        batch_lit = json.dumps(max(1, min(16, int(batch_hint))))

    result = _evaluate_js(
        publisher,
        f"""
        (async () => {{
          const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

          const visible = (el) => !!(el && el.offsetParent !== null);
          const prompt = {prompt_literal};
          const refs = {refs_literal};
          const batchHint = {batch_lit};
          let inputFound = false;
          let generateClicked = false;

          /* Reinforce N 张（含 1 张）right before 写入 prompt（与 _picset_try_set_batch_count 互补）*/
          if (batchHint >= 1) {{
            const clickable = [...document.querySelectorAll('button,[role=\"button\"],label,span,[role=\"radio\"]')]
              .filter(visible);
            for (const el of clickable) {{
              const raw = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, '');
              if (!raw) continue;
              if (raw.includes(String(batchHint) + '张') || raw.includes(batchHint + '张详情')) {{
                el.click();
                await sleep(350);
                break;
              }}
            }}
          }}

          const inputs = [
            ...document.querySelectorAll('textarea'),
            ...document.querySelectorAll('[contenteditable="true"]'),
            ...document.querySelectorAll('input[type="text"]'),
          ].filter(visible);

          let target = null;
          for (const node of inputs) {{
            const ph = (node.getAttribute?.('placeholder') || '').toLowerCase();
            const text = (node.innerText || '').toLowerCase();
            if (
              ph.includes('prompt') ||
              ph.includes('描述') ||
              ph.includes('请输入') ||
              text.includes('prompt')
            ) {{
              target = node;
              break;
            }}
          }}
          if (!target && inputs.length > 0) {{
            target = inputs[0];
          }}

          if (target) {{
            target.focus();
            if (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT') {{
              target.value = prompt;
              target.dispatchEvent(new Event('input', {{ bubbles: true }}));
              target.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }} else {{
              target.innerText = prompt;
              target.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
            inputFound = true;
          }}

          await sleep(500);

          const buttonKeywords = ['开始生成', '立即生成', '生成', 'generate', 'create'];
          const excludeKeywords = ['q ', '版权归谁', '常见问题', 'faq', 'close', '登录', '注册', '立即体验', '免费试用', '开始风格复刻'];
          const buttons = [
            ...document.querySelectorAll('button'),
            ...document.querySelectorAll('[role="button"]'),
            ...document.querySelectorAll('a'),
          ].filter(visible);

          let clickedLabel = '';
          for (const btn of buttons) {{
            const text = (btn.innerText || btn.textContent || '').trim().toLowerCase();
            if (!text) continue;
            if (excludeKeywords.some((k) => text.includes(k))) continue;
            if (buttonKeywords.some((k) => text.includes(k))) {{
              btn.click();
              generateClicked = true;
              clickedLabel = text;
              break;
            }}
          }}

          return {{
            ok: inputFound && generateClicked,
            inputFound,
            generateClicked,
            clickedLabel,
            referenceCount: refs.length
          }};
        }})()
        """,
    )
    if not isinstance(result, dict):
        raise RuntimeError("Unexpected JS result when triggering generation.")
    return result


def _enter_picset_workspace_if_needed(publisher: XiaohongshuPublisher) -> None:
    result = _evaluate_js(
        publisher,
        """
        (() => {
          const href = window.location.href || '';
          const hasPromptInput = !!document.querySelector('textarea, [contenteditable="true"], input[type="text"]');
          if (hasPromptInput && !href.endsWith('/')) {
            return { switched: false, reason: 'already_workspace', href };
          }

          const visible = (el) => !!(el && el.offsetParent !== null);
          const textOf = (el) => (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
          const candidates = [...document.querySelectorAll('a,button,[role="button"]')].filter(visible);
          const priority = ['开始风格复刻', '风格复刻', '立即体验', '免费试用', '开始全品类商品图'];
          for (const key of priority) {
            const node = candidates.find((el) => textOf(el).includes(key));
            if (node) {
              node.click();
              return { switched: true, reason: 'clicked_entry', key, hrefBefore: href };
            }
          }
          return { switched: false, reason: 'entry_not_found', href };
        })()
        """,
    )
    if isinstance(result, dict) and result.get("switched"):
        print(f"[picset] Enter workspace via: {result.get('key')}")
        time.sleep(2.0)


def _find_file_input_node_id(
    publisher: XiaohongshuPublisher,
    target_kind: str = "reference",
) -> int | None:
    target_kind = (target_kind or "reference").strip().lower()
    if target_kind not in {"reference", "product"}:
        target_kind = "reference"

    if target_kind == "reference":
        include_keywords = ["参考设计图", "参考图", "风格", "样式", "style"]
    else:
        include_keywords = ["产品素材图", "产品图", "商品图", "product", "sku"]

    js = """
        (() => {
          const visible = (el) => !!(el && el.offsetParent !== null);
          const allInputs = [...document.querySelectorAll('input[type="file"]')];
          const inputs = allInputs.filter(visible);
          const includeKeywords = __INCLUDE_KEYWORDS__;
          const scoreInput = (el) => {
            const wrap = el.closest('div,section,form');
            const scope = ((wrap?.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('name') || '')).toLowerCase();
            let score = 0;
            for (const kw of includeKeywords) {
              if (scope.includes(String(kw).toLowerCase())) score += 5;
            }
            if (scope.includes('上传') || scope.includes('upload') || scope.includes('image')) score += 1;
            return score;
          };

          const sortedVisible = inputs
            .map((el) => ({ el, score: scoreInput(el) }))
            .sort((a, b) => b.score - a.score);

          const sortedAll = allInputs
            .map((el) => ({ el, score: scoreInput(el) }))
            .sort((a, b) => b.score - a.score);

          const input = (sortedVisible[0]?.score || 0) > 0
                ? sortedVisible[0].el
                : ((sortedAll[0]?.score || 0) > 0
                    ? sortedAll[0].el
                    : (inputs[0] || allInputs[0] || null));
          if (!input) return null;
          input.setAttribute('data-picset-upload-target', __TARGET_KIND__);
          return 1;
        })()
    """
    js = js.replace("__INCLUDE_KEYWORDS__", json.dumps(include_keywords, ensure_ascii=False))
    js = js.replace("__TARGET_KIND__", json.dumps(target_kind, ensure_ascii=False))
    node_id = _evaluate_js(publisher, js)
    if node_id is None:
        return None
    result = publisher._send(
        "DOM.getDocument",
        {"depth": 2, "pierce": True},
    )
    root_node_id = result.get("root", {}).get("nodeId")
    if not root_node_id:
        return None
    query_result = publisher._send(
        "DOM.querySelector",
        {
            "nodeId": root_node_id,
            "selector": f"input[type='file'][data-picset-upload-target='{target_kind}']",
        },
    )
    return query_result.get("nodeId")


def _upload_reference_images_via_cdp(
    publisher: XiaohongshuPublisher,
    reference_paths: list[str],
    target_kind: str = "reference",
) -> bool:
    if not reference_paths:
        return False
    existing = [os.path.abspath(p) for p in reference_paths if os.path.isfile(p)]
    if not existing:
        print("[picset] No valid local reference images found for upload.")
        return False

    try:
        publisher._send("DOM.enable")
    except CDPError:
        pass
    # Picset SPA sometimes renders upload input lazily; retry both locating the
    # input and sending files to avoid flaky "unavailable/upload failed" states.
    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        node_id = _find_file_input_node_id(publisher, target_kind=target_kind)
        if not node_id:
            if attempt < max_attempts:
                print(
                    f"[picset] Upload target not ready ({target_kind}), retry {attempt}/{max_attempts}..."
                )
                time.sleep(1.0)
                continue
            print(f"[picset] Upload skipped: target input not found ({target_kind}).")
            return False

        try:
            publisher._send(
                "DOM.setFileInputFiles",
                {
                    "nodeId": int(node_id),
                    "files": existing,
                },
            )
            print(f"[picset] Uploaded {len(existing)} image(s) to {target_kind} slot.")
            # Give SPA time to finish preview / token refresh after setFileInputFiles.
            time.sleep(2.2)
            return True
        except CDPError as exc:
            if attempt >= max_attempts:
                print(f"[picset] Upload failed ({target_kind}): {exc}")
                return False
            print(
                f"[picset] Upload failed ({target_kind}) retry {attempt}/{max_attempts}: {exc}"
            )
            time.sleep(1.0)
    return False


def _snapshot_candidate_image_urls(publisher: XiaohongshuPublisher) -> set[str]:
    urls = _evaluate_js(
        publisher,
        """
        (() => {
          const isCandidate = (url, img) => {
            if (!url || !url.startsWith('http')) return false;
            const u = url.toLowerCase();
            if (/\\.(png|jpg|jpeg|webp)(\\?|$)/.test(u)) return true;
            const w = img?.naturalWidth || 0;
            const h = img?.naturalHeight || 0;
            return w >= 512 && h >= 512;
          };
          const arr = [];
          for (const img of [...document.querySelectorAll('img')]) {
            const src = img.currentSrc || img.src || '';
            if (isCandidate(src, img)) arr.push(src);
          }
          return [...new Set(arr)];
        })()
        """,
    )
    if not isinstance(urls, list):
        return set()
    return {u for u in urls if isinstance(u, str) and u.startswith("http")}


def _install_network_image_hooks(publisher: XiaohongshuPublisher) -> None:
    _evaluate_js(
        publisher,
        """
        (() => {
          if (window.__picsetHookInstalled) {
            window.__picsetCapturedImageUrls = [];
            return { ok: true, installed: true, reset: true };
          }
          window.__picsetHookInstalled = true;
          window.__picsetCapturedImageUrls = [];

          const pushUrl = (raw) => {
            if (!raw || typeof raw !== 'string') return;
            const u = raw.trim();
            if (!u.startsWith('http')) return;
            if (!window.__picsetCapturedImageUrls.includes(u)) {
              window.__picsetCapturedImageUrls.push(u);
            }
          };

          const maybeExtract = (obj) => {
            try {
              if (!obj) return;
              if (typeof obj === 'string') {
                const str = obj;
                const re = /(https?:\\/\\/[^\\s"'<>]+(?:png|jpg|jpeg|webp|gif)(?:\\?[^\\s"'<>]*)?)/ig;
                let m = null;
                while ((m = re.exec(str)) !== null) {
                  pushUrl(m[1]);
                }
                return;
              }
              if (Array.isArray(obj)) {
                for (const item of obj) maybeExtract(item);
                return;
              }
              if (typeof obj === 'object') {
                for (const [k, v] of Object.entries(obj)) {
                  if (typeof v === 'string') {
                    const key = k.toLowerCase();
                    if (key.includes('url') || key.includes('image') || key.includes('img') || key.includes('src')) {
                      pushUrl(v);
                    }
                    maybeExtract(v);
                  } else {
                    maybeExtract(v);
                  }
                }
              }
            } catch (_) {}
          };

          const origFetch = window.fetch;
          window.fetch = async (...args) => {
            const res = await origFetch(...args);
            try {
              const cloned = res.clone();
              const ct = (cloned.headers.get('content-type') || '').toLowerCase();
              if (ct.includes('application/json') || ct.includes('text/')) {
                const text = await cloned.text();
                maybeExtract(text);
                try { maybeExtract(JSON.parse(text)); } catch (_) {}
              }
            } catch (_) {}
            return res;
          };

          const OrigXHR = window.XMLHttpRequest;
          function WrappedXHR() {
            const xhr = new OrigXHR();
            xhr.addEventListener('load', function () {
              try {
                const ct = (xhr.getResponseHeader('content-type') || '').toLowerCase();
                if (ct.includes('application/json') || ct.includes('text/')) {
                  const text = xhr.responseText || '';
                  maybeExtract(text);
                  try { maybeExtract(JSON.parse(text)); } catch (_) {}
                }
              } catch (_) {}
            });
            return xhr;
          }
          WrappedXHR.prototype = OrigXHR.prototype;
          try {
            for (const key of Object.getOwnPropertyNames(OrigXHR)) {
              if (!(key in WrappedXHR)) {
                Object.defineProperty(
                  WrappedXHR,
                  key,
                  Object.getOwnPropertyDescriptor(OrigXHR, key) || {
                    value: OrigXHR[key],
                    configurable: true,
                    enumerable: false,
                    writable: true,
                  }
                );
              }
            }
          } catch (_) {}
          window.XMLHttpRequest = WrappedXHR;

          return { ok: true, installed: true };
        })()
        """,
    )


def _get_network_captured_image_urls(publisher: XiaohongshuPublisher) -> list[str]:
    urls = _evaluate_js(
        publisher,
        """
        (() => {
          const arr = Array.isArray(window.__picsetCapturedImageUrls)
            ? window.__picsetCapturedImageUrls
            : [];
          return [...new Set(arr)];
        })()
        """,
    )
    if not isinstance(urls, list):
        return []
    return [u for u in urls if isinstance(u, str) and u.startswith("http")]


def _baseline_urls_after_uploads(publisher: XiaohongshuPublisher) -> set[str]:
    """
    Snapshot DOM + hooked fetch/XHR image URLs after reference/product uploads.
    Must run immediately before clicking generate so only post-generate assets count as new.
    """
    dom = _snapshot_candidate_image_urls(publisher)
    net = _get_network_captured_image_urls(publisher)
    out: set[str] = set(dom)
    for u in net:
        if isinstance(u, str) and u.startswith("http"):
            out.add(u)
    return out


def _clear_network_capture_buffer(publisher: XiaohongshuPublisher) -> None:
    """Reset fetch/XHR capture list so generation traffic is easier to spot."""
    _evaluate_js(
        publisher,
        """
        (() => {
          if (Array.isArray(window.__picsetCapturedImageUrls)) {
            window.__picsetCapturedImageUrls.length = 0;
          }
          return true;
        })()
        """,
    )


def _collect_generated_image_urls(
    publisher: XiaohongshuPublisher,
    timeout_seconds: int,
    baseline_urls: set[str] | None = None,
    stable_rounds: int = 3,
    min_count: int = 1,
) -> list[str]:
    deadline = time.time() + max(10, timeout_seconds)
    seen: dict[str, None] = {}
    baseline = baseline_urls or set()
    stable_hits = 0
    last_new_count = -1
    while time.time() < deadline:
        network_urls = _get_network_captured_image_urls(publisher)
        for u in network_urls:
            if u not in baseline:
                seen[u] = None

        dom_urls = _evaluate_js(
            publisher,
            """
            (() => {
              const badHosts = ['avatar', 'logo', 'icon'];
              const isLikelyResult = (url, img) => {
                if (!url || !url.startsWith('http')) return false;
                const u = url.toLowerCase();
                const hasImageExt = /\\.(png|jpg|jpeg|webp)(\\?|$)/.test(u);
                const w = img?.naturalWidth || 0;
                const h = img?.naturalHeight || 0;
                const looksLargeImage = w >= 512 && h >= 512;
                if (!(hasImageExt || looksLargeImage)) return false;
                if (u.includes('sprite') || u.includes('favicon')) return false;
                if (badHosts.some((k) => u.includes(k))) return false;
                return true;
              };

              const arr = [];
              const imgs = [...document.querySelectorAll('img')];
              for (const img of imgs) {
                const src = img.currentSrc || img.src || '';
                if (isLikelyResult(src, img)) arr.push(src);
              }
              return [...new Set(arr)];
            })()
            """,
        )
        if isinstance(dom_urls, list):
            for u in dom_urls:
                if isinstance(u, str) and u.startswith("http") and u not in baseline:
                    seen[u] = None

        current_count = len(seen)
        if current_count >= max(1, min_count):
            if current_count == last_new_count:
                stable_hits += 1
            else:
                stable_hits = 0
            last_new_count = current_count
            if stable_hits >= max(1, stable_rounds):
                break

        time.sleep(2)

    return list(seen.keys())


def _publish_to_xhs(
    title: str,
    content: str,
    image_paths: list[str],
    account: str | None,
    host: str,
    port: int,
    headless: bool,
    preview: bool,
) -> int:
    if not image_paths:
        print("[picset] No images to publish; skip publish pipeline.")
        return 0

    cmd = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "publish_pipeline.py"),
        "--title",
        title,
        "--content",
        content,
        "--images",
        *image_paths,
        "--host",
        host,
        "--port",
        str(port),
    ]
    if account:
        cmd.extend(["--account", account])
    if headless:
        cmd.append("--headless")
    if preview:
        cmd.append("--preview")

    print("[picset] Triggering publish pipeline...")
    print("[picset] Command:", " ".join(json.dumps(part, ensure_ascii=False) for part in cmd))
    completed = subprocess.run(cmd, cwd=SCRIPT_DIR)
    return int(completed.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate images on Picset and optionally publish to Xiaohongshu."
    )
    parser.add_argument("--host", default="127.0.0.1", help="CDP host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9222, help="CDP port (default: 9222)")
    parser.add_argument("--headless", action="store_true", help="Launch Chrome in headless mode when local")
    parser.add_argument("--account", default=None, help="Account name used by existing profile launcher")
    parser.add_argument("--reuse-existing-tab", action="store_true", help="Prefer reusing existing browser tab")
    parser.add_argument("--picset-url", default=PICSET_AUTH_URL, help="Picset entry URL")
    parser.add_argument("--login-timeout", type=int, default=120, help="Wait seconds for manual login")
    parser.add_argument(
        "--generate-timeout",
        type=int,
        default=120,
        help="Wait seconds for generated images after clicking generate (default: 120)",
    )
    parser.add_argument("--prompt", default=None, help="Generation prompt text")
    parser.add_argument("--prompt-file", default=None, help="Load generation prompt from file")
    parser.add_argument(
        "--product-images",
        nargs="+",
        default=None,
        help="Local product material images. Recommended: pass every run.",
    )
    parser.add_argument("--title", default=None, help="XHS publish title (required when --publish-to-xhs)")
    parser.add_argument("--title-file", default=None, help="XHS title file")
    parser.add_argument("--content", default=None, help="XHS publish content (required when --publish-to-xhs)")
    parser.add_argument("--content-file", default=None, help="XHS content file")
    parser.add_argument("--reference-image-urls", nargs="+", default=None, help="Reference image URLs to download")
    parser.add_argument("--reference-images", nargs="+", default=None, help="Local reference image paths")
    parser.add_argument(
        "--xhs-reference-keyword",
        default=None,
        help="Keyword used to fetch latest XHS hot notes, then use their cover images as design references.",
    )
    parser.add_argument(
        "--xhs-reference-limit",
        type=int,
        default=6,
        help="How many XHS cover images to fetch as references (default: 6).",
    )
    parser.add_argument("--output-dir", default=None, help="Directory to save downloaded generated images")
    parser.add_argument("--max-download", type=int, default=1, help="Max number of generated images to download")
    parser.add_argument("--publish-to-xhs", action="store_true", help="Publish downloaded images via publish_pipeline")
    parser.add_argument("--preview", action="store_true", help="When publishing, fill only and do not click publish")
    parser.add_argument(
        "--disconnect-cdp",
        action="store_true",
        help="Close DevTools WebSocket on exit. Default keeps the browser tab as-is.",
    )
    args = parser.parse_args()

    try:
        prompt = _read_text(args.prompt_file, args.prompt, "prompt")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    reference_paths = _resolve_existing_files(args.reference_images)
    product_paths = _resolve_existing_files(args.product_images)

    if args.product_images and not product_paths:
        print("Error: --product-images provided but no valid files found.", file=sys.stderr)
        sys.exit(2)

    if product_paths:
        print(f"[picset] Product material images: {len(product_paths)}")

    xhs_cover_urls: list[str] = []
    if args.xhs_reference_keyword:
        xhs_cover_urls = _fetch_xhs_reference_main_images(
            keyword=args.xhs_reference_keyword,
            limit=max(1, args.xhs_reference_limit),
        )
        if xhs_cover_urls:
            downloaded_xhs_refs = _download_urls(xhs_cover_urls, args.output_dir)
            reference_paths.extend(downloaded_xhs_refs)
        else:
            print("[picset] Warning: no XHS reference covers fetched.")

    if args.reference_image_urls:
        print(f"[picset] Downloading {len(args.reference_image_urls)} reference URL image(s)...")
        downloaded_refs = _download_urls(args.reference_image_urls, args.output_dir)
        reference_paths.extend(downloaded_refs)

    if not product_paths and not reference_paths:
        print(
            "[picset] Warning: no upload images provided. "
            "Recommended: pass --product-images and/or --xhs-reference-keyword."
        )

    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        output_dir = args.output_dir
    else:
        output_dir = tempfile.mkdtemp(prefix="picset_generated_")

    if _is_local_host(args.host):
        if not ensure_chrome(port=args.port, headless=args.headless, account=args.account):
            print("Error: Failed to start Chrome.", file=sys.stderr)
            sys.exit(2)

    publisher = XiaohongshuPublisher(
        host=args.host,
        port=args.port,
        account_name=args.account,
    )
    generated_local_paths: list[str] = []

    try:
        publisher.connect(
            target_url_prefix="https://picsetai",
            reuse_existing_tab=args.reuse_existing_tab,
        )
        publisher.        _navigate_picset_preserving_session(args.picset_url)
        _wait_for_login_if_needed(publisher, timeout_seconds=args.login_timeout)
        _enter_picset_workspace_if_needed(publisher)
        _require_picset_upload_ui(
            publisher,
            timeout_seconds=max(60, min(150, args.login_timeout)),
            phase="进入工作台后",
        )
        uploaded_reference = True
        if reference_paths:
            uploaded_reference = _upload_reference_images_via_cdp(
                publisher=publisher,
                reference_paths=reference_paths,
                target_kind="reference",
            )
            if not uploaded_reference:
                print("[picset] Warning: reference images were provided but not uploaded to reference slot.")

        uploaded_product = True
        if product_paths:
            uploaded_product = _upload_reference_images_via_cdp(
                publisher=publisher,
                reference_paths=product_paths,
                target_kind="product",
            )
            if not uploaded_product:
                print("[picset] Warning: product images were provided but not uploaded to product slot.")

        # Hooks after uploads: patching fetch/XHR during upload can break Picset auth/session.
        _install_network_image_hooks(publisher)
        baseline_urls = _baseline_urls_after_uploads(publisher)
        _clear_network_capture_buffer(publisher)

        trigger_result = _fill_prompt_and_generate(
            publisher=publisher,
            prompt=prompt,
            reference_paths=reference_paths,
        )
        print("[picset] Trigger result:", json.dumps(trigger_result, ensure_ascii=False))
        if not trigger_result.get("ok"):
            raise RuntimeError("Failed to find prompt input or generate button on current page.")

        image_urls = _collect_generated_image_urls(
            publisher=publisher,
            timeout_seconds=args.generate_timeout,
            baseline_urls=baseline_urls,
            stable_rounds=2,
            min_count=1,
        )
        if not image_urls:
            raise RuntimeError("No generated image URLs detected before timeout.")

        image_urls = image_urls[: max(1, args.max_download)]
        print(f"[picset] Found {len(image_urls)} generated image URL(s).")
        raw_downloaded = _download_urls(image_urls, output_dir)
        generated_local_paths = _validate_downloaded_images(raw_downloaded)
        if not generated_local_paths:
            raise RuntimeError("Failed to download generated images.")

        print("PICSET_RESULT:")
        print(
            json.dumps(
                {
                    "prompt": prompt,
                    "generated_image_urls": image_urls,
                    "generated_local_paths": generated_local_paths,
                    "output_dir": output_dir,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        if args.publish_to_xhs:
            try:
                title = _read_text(args.title_file, args.title, "title")
                content = _read_text(args.content_file, args.content, "content")
            except ValueError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(2)

            exit_code = _publish_to_xhs(
                title=title,
                content=content,
                image_paths=generated_local_paths,
                account=args.account,
                host=args.host,
                port=args.port,
                headless=args.headless,
                preview=args.preview,
            )
            if exit_code != 0:
                sys.exit(exit_code)

    except (RuntimeError, CDPError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    finally:
        if args.disconnect_cdp:
            publisher.disconnect()


if __name__ == "__main__":
    try:
        with single_instance("post_to_xhs_picset"):
            main()
    except SingleInstanceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(3)
