"""
Download Xiaohongshu note images to a dated Desktop folder, then upload them to Picset
「参考设计图」slot (same browser CDP session).

Typical flow:
  1) Search by keyword OR open a single note by feed_id + xsec_token
  2) Collect image URLs (covers from search, or all images from note detail)
  3) Download to Desktop\\xhs_picset_YYYYMMDD-NN\\
  3b) Optional --watermark-post-workflow / --watermark-full-auto on **downloaded XHS** refs
  3c) Optional --photoshop-after-generate (with --generate): Picset **生成图**落盘后再做 PS 自动色调/对比度/颜色；
      可选 --manual-draw-before-photoshop 才等待人工画图信号（默认不等待）。
  4) Navigate to Picset CN: upload XHS downloads to 「参考设计图」, optional local files to 「产品素材图」 (--product-images)
  5) Optional (--generate): hooks + baseline snapshot, fill prompt, click 「生成」,
     wait for new image URLs (network/DOM), download to Desktop\\Picset生成图_YYYYMMDD-NN\\
  6) Optional (--publish-to-xhs): run publish_pipeline.py with generated images

Requires Chrome/Edge with remote debugging on --port (default 9222), already logged into XHS + Picset.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import traceback

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from chrome_launcher import ensure_chrome, restart_chrome  # noqa: E402
from cdp_publish import CDPError, XiaohongshuPublisher  # noqa: E402
from pipeline_debug import pipeline_debug_log  # noqa: E402
from image_downloader import ImageDownloader  # noqa: E402
from picset_automation import (  # noqa: E402
    _baseline_urls_after_uploads,
    _clear_network_capture_buffer,
    _collect_generated_image_urls,
    _download_urls,
    _evaluate_js,
    _enter_picset_workspace_if_needed,
    _fill_prompt_and_generate,
    _picset_try_set_batch_count,
    _install_network_image_hooks,
    _publish_to_xhs,
    _read_text,
    _require_picset_upload_ui,
    _resolve_existing_files,
    _upload_reference_images_via_cdp,
    _validate_downloaded_images,
    _wait_for_login_if_needed,
)

DEFAULT_PICSET_CN = "https://picsetai.cn/"


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    pipeline_debug_log(
        hypothesis_id,
        location,
        message,
        data,
        run_id=f"xhs2picset_{int(time.time() * 1000)}",
    )
    # endregion


def _is_local_host(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _desktop_base() -> Path:
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    desktop = Path(profile) / "Desktop"
    if not desktop.is_dir():
        desktop = Path.home() / "Desktop"
    return desktop


def make_desktop_session_dir(prefix: str = "xhs_picset") -> str:
    """Desktop\\prefix_YYYYMMDD-NN (NN increments if folder exists)."""
    desktop = _desktop_base()
    date_str = datetime.now().strftime("%Y%m%d")
    n = 1
    while True:
        candidate = desktop / f"{prefix}_{date_str}-{n:02d}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        n += 1


def make_desktop_generated_dir() -> str:
    """Desktop\\Picset生成图_YYYYMMDD-NN — Picset 产出图默认放桌面根目录，便于找。"""
    desktop = _desktop_base()
    date_str = datetime.now().strftime("%Y%m%d")
    n = 1
    while True:
        candidate = desktop / f"Picset生成图_{date_str}-{n:02d}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        n += 1


def _extract_cover_urls_from_search_stdout(raw_output: str, limit: int) -> list[str]:
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
    return deduped


def _collect_image_urls_from_detail(detail: Any) -> list[str]:
    """Walk note detail JSON for imageList -> infoList -> url."""
    urls: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "imageList" in obj and isinstance(obj["imageList"], list):
                for im in obj["imageList"]:
                    if not isinstance(im, dict):
                        continue
                    for info in im.get("infoList") or []:
                        if isinstance(info, dict):
                            u = info.get("url")
                            if isinstance(u, str) and u.startswith("http"):
                                urls.append(u)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(detail)
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _dedupe_files_by_sha256(paths: list[str]) -> tuple[list[str], list[str]]:
    """Deduplicate local files by content hash, preserving order."""
    unique: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for p in paths:
        try:
            digest = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        except Exception:
            unique.append(p)
            continue
        if digest in seen:
            duplicates.append(p)
            continue
        seen.add(digest)
        unique.append(p)
    return unique, duplicates


def _ecommerce_cover_preference_score(path: str, *, thumb: int = 128) -> float:
    """
    Heuristic score: higher ≈ looks more like a product / lifestyle photo to sell from;
    lower ≈ big typography poster / meme / infographic.

    Uses edge density + color variance + penalizes very "busy" top band (common title overlays).
    """
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return 0.0
    try:
        import numpy as np  # noqa: PLC0415
    except ImportError:
        return _ecommerce_cover_preference_score_pillow_only(path, thumb=thumb)

    try:
        im = Image.open(path).convert("RGB")
        im = im.resize((thumb, thumb), Image.Resampling.LANCZOS)
        g = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
        gx = np.abs(g[:, 1:] - g[:, :-1])
        gy = np.abs(g[1:, :] - g[:-1, :])
        edge_mean = float((np.mean(gx) + np.mean(gy)) * 0.5)
        # Strong global edges typical of typography / infographic
        edge_term = 1.8 - abs(edge_mean - 0.075) * 12.0
        if edge_mean > 0.16:
            edge_term -= (edge_mean - 0.16) * 8.0
        rgb = np.asarray(im, dtype=np.float32) / 255.0
        rg = rgb[..., 0] - rgb[..., 1]
        yb = 0.5 * (rgb[..., 0] + rgb[..., 1]) - rgb[..., 2]
        colorful = float(np.sqrt(np.var(rg) + np.var(yb)))
        color_term = min(2.5, colorful * 5.0)
        # Top stripe: captions / big titles increase edges there
        h = g.shape[0]
        top = g[: max(1, h // 5), :]
        tg = np.abs(top[:, 1:] - top[:, :-1])
        ty = np.abs(top[1:, :] - top[:-1, :])
        top_edge = float((np.mean(tg) + np.mean(ty)) * 0.5)
        top_penalty = max(0.0, top_edge - 0.11) * 6.5
        return float(edge_term + color_term - top_penalty)
    except Exception:
        return 0.0


def _ecommerce_cover_preference_score_pillow_only(path: str, *, thumb: int = 64) -> float:
    """Cheap fallback without numpy."""
    try:
        from PIL import Image  # noqa: PLC0415

        im = Image.open(path).convert("L").resize((thumb, thumb), Image.Resampling.LANCZOS)
        px = list(im.getdata())
        mean = sum(px) / float(len(px) or 1)
        gx = []
        rowsz = thumb
        for row in range(thumb):
            for col in range(1, thumb):
                gx.append(abs(px[row * rowsz + col] - px[row * rowsz + col - 1]))
        edge_mean = sum(gx) / float(len(gx) or 1) / 255.0
        edge_term = 1.4 - abs(edge_mean - 0.08) * 10.0
        if edge_mean > 0.15:
            edge_term -= (edge_mean - 0.15) * 6.0
        return float(edge_term)
    except Exception:
        return 0.0


def _normalize_generated_url(url: str) -> str:
    """Normalize generated URL for dedupe (ignore cache-busting query suffix)."""
    u = (url or "").strip()
    if not u:
        return u
    return u.split("?", 1)[0]


def _picset_result_unavailable_state(publisher: XiaohongshuPublisher) -> dict[str, Any]:
    """Probe Picset result cards for '不可用' placeholders."""
    try:
        state = _evaluate_js(
            publisher,
            """
            (() => {
              const text = (document.body?.innerText || '').toLowerCase();
              const hasUnavailable = text.includes('不可用') || text.includes('unavailable');
              const hasRetry = text.includes('重试') || text.includes('重新生成');
              const icons = document.querySelectorAll('img[alt*="不可用"], [class*="unavailable"]').length;
              return {
                hasUnavailable: !!hasUnavailable || icons > 0,
                hasRetryHint: !!hasRetry,
                snippet: text.slice(0, 500),
              };
            })()
            """,
        )
        if isinstance(state, dict):
            return state
    except Exception:
        pass
    return {"hasUnavailable": False, "hasRetryHint": False, "snippet": ""}


def _picset_network_healthcheck(
    publisher: XiaohongshuPublisher,
    *,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    """Best-effort network preflight before generation."""
    js = f"""
        (async () => {{
          const timeoutMs = {max(3000, int(timeout_seconds) * 1000)};
          const run = async (url) => {{
            try {{
              const ctrl = new AbortController();
              const t = setTimeout(() => ctrl.abort(), timeoutMs);
              const res = await fetch(url, {{
                method: 'GET',
                mode: 'no-cors',
                cache: 'no-store',
                signal: ctrl.signal,
              }});
              clearTimeout(t);
              return {{ ok: true, status: res.status || 0 }};
            }} catch (e) {{
              return {{ ok: false, error: String(e) }};
            }}
          }};
          const online = navigator.onLine !== false;
          const picset = await run('https://picsetai.cn/');
          const xhsCdn = await run('https://sns-webpic-qc.xhscdn.com/');
          return {{ online, picset, xhsCdn }};
        }})()
    """
    result = _evaluate_js(publisher, js)
    return result if isinstance(result, dict) else {"online": True, "picset": {"ok": True}, "xhsCdn": {"ok": True}}


def _write_failure_snapshot(
    publisher: XiaohongshuPublisher | None,
    *,
    stage: str,
    err_text: str,
) -> None:
    try:
        snap_dir = Path(SCRIPT_DIR).parent / "tmp" / "failure_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"xhs2picset_{stage}_{ts}"
        png_path = snap_dir / f"{base}.png"
        log_path = snap_dir / f"{base}.log"

        if publisher and publisher.ws:
            try:
                publisher._send("Page.enable")
                shot = publisher._send(
                    "Page.captureScreenshot",
                    {"format": "png", "captureBeyondViewport": True},
                )
                data = shot.get("data", "")
                if isinstance(data, str) and data:
                    png_path.write_bytes(base64.b64decode(data))
            except Exception:
                pass

        dbg = Path(Path(SCRIPT_DIR).parent / "debug-471add.log")
        tail: list[str] = []
        if dbg.is_file():
            try:
                tail = dbg.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
            except Exception:
                tail = []
        if not tail:
            tail = traceback.format_exc().splitlines()[-30:]

        content = [f"stage={stage}", f"error={err_text}", "", "recent_logs_tail_30:"]
        content.extend(tail)
        log_path.write_text("\n".join(content) + "\n", encoding="utf-8")
        print(f"[xhs2picset] Failure snapshot saved: {log_path}")
        if png_path.is_file():
            print(f"[xhs2picset] Failure screenshot saved: {png_path}")
    except Exception:
        pass


def _write_generation_checkpoint_summary(
    summary_path: Path,
    *,
    output_dir: str,
    reference_paths: list[str],
    product_material_paths: list[str],
    generated_urls: list[str],
    generated_local_paths: list[str],
    generated_output_dir: str | None,
) -> None:
    """After Picset URLs → local files: persist summary before PS/retry-heavy tail."""
    blob: dict[str, Any] = {
        "output_dir": output_dir,
        "reference_local_paths": list(reference_paths),
        "reference_count": len(reference_paths),
        "local_paths": list(reference_paths),
        "count": len(reference_paths),
        "generated_image_urls": list(generated_urls),
        "generated_local_paths": list(generated_local_paths),
        "generated_output_dir": generated_output_dir or "",
        "picset_generation_checkpoint": True,
    }
    if product_material_paths:
        blob["product_local_paths"] = list(product_material_paths)
        blob["product_count"] = len(product_material_paths)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(blob, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        "[xhs2picset] 已写入生图 checkpoint（PS/发布后步骤失败时，可按该 summary 跳过再搜小红书）。",
        flush=True,
    )


def _is_transient_cdp_promise_failure(exc: BaseException) -> bool:
    msg = str(exc)
    low = msg.lower()
    return "promise was collected" in low or (
        "-32000" in msg and "promise" in low
    )


def _retry_on_cdp_promise_collected(
    fn,
    *,
    label: str,
    attempts: int = 5,
    delay_seconds: float = 2.0,
):
    """Retry flaky CDP evaluate calls when Chrome reports 'Promise was collected'."""
    last_exc: BaseException | None = None
    for i in range(1, max(1, attempts) + 1):
        try:
            return fn()
        except (CDPError, RuntimeError) as exc:
            last_exc = exc
            if not _is_transient_cdp_promise_failure(exc) or i >= attempts:
                raise
            print(
                f"[xhs2picset] CDP transient ({label}) retry {i}/{attempts}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(max(0.3, delay_seconds))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Unexpected retry state for {label}")


def run_search_covers(
    keyword: str,
    sort_by: str,
    limit_notes: int,
    *,
    host: str,
    port: int,
    account: str | None,
    publish_time: str,
    note_type: str,
    reuse_existing_tab: bool = True,
) -> list[str]:
    import subprocess

    cmd = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "cdp_publish.py"),
        "--host",
        host,
        "--port",
        str(port),
        *(["--account", account] if account else []),
        *(
            ["--reuse-existing-tab"]
            if reuse_existing_tab
            else []
        ),
        "search-feeds",
        "--keyword",
        keyword,
        "--sort-by",
        sort_by,
        "--publish-time",
        publish_time,
        "--note-type",
        note_type,
    ]
    subprocess_timeout = max(120, 180)
    last_out = ""
    for attempt in range(1, 4):
        proc = subprocess.run(
            cmd,
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=subprocess_timeout,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        last_out = out
        urls = _extract_cover_urls_from_search_stdout(out, limit=limit_notes)
        if urls:
            if attempt > 1:
                print(
                    f"[xhs2picset] search-feeds recovered on attempt {attempt}/3 "
                    f"({len(urls)} cover URL(s)).",
                    flush=True,
                )
            return urls
        if proc.returncode != 0:
            print(
                f"[xhs2picset] search-feeds exit={proc.returncode} (attempt {attempt}/3)",
                file=sys.stderr,
                flush=True,
            )
        if attempt < 3:
            backoff = float(attempt * 4)
            print(
                f"[xhs2picset] search-feeds returned 0 covers; backoff {backoff:.0f}s then retry "
                f"({attempt + 1}/3)...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(backoff)
    return _extract_cover_urls_from_search_stdout(last_out, limit=limit_notes)


def run_detail_all_images(
    host: str,
    port: int,
    feed_id: str,
    xsec_token: str,
) -> list[str]:
    publisher = XiaohongshuPublisher(host=host, port=port)
    publisher.connect(reuse_existing_tab=True)
    try:
        result = publisher.get_feed_detail(
            feed_id=feed_id,
            xsec_token=xsec_token,
            load_all_comments=False,
        )
        detail = result.get("detail") or {}
        return _collect_image_urls_from_detail(detail)
    finally:
        publisher.disconnect()


def main() -> None:
    # region agent log
    pipeline_debug_log(
        "H0",
        "xhs_images_to_picset.py:main",
        "entry",
        {
            "cwd": str(Path.cwd()),
            "scripts_parent": str(Path(SCRIPT_DIR).parent),
        },
    )
    # endregion
    parser = argparse.ArgumentParser(
        description="Download XHS images to Desktop folder and upload to Picset reference slot."
    )
    parser.add_argument(
        "--keyword",
        default=None,
        help="Search keyword; uses each note's cover image (see --limit-notes).",
    )
    parser.add_argument(
        "--feed-id",
        default=None,
        help="With --xsec-token: open note detail and download all images in the note.",
    )
    parser.add_argument("--xsec-token", default=None, help="Required with --feed-id.")
    parser.add_argument(
        "--sort-by",
        default="最多点赞",
        help="When using --keyword, search sort (default: 最多点赞).",
    )
    parser.add_argument(
        "--publish-time",
        default="一周内",
        choices=("不限", "一天内", "一周内", "半年内"),
        help="When using --keyword: publish time filter (default: 一周内).",
    )
    parser.add_argument(
        "--note-type",
        default="图文",
        choices=("不限", "视频", "图文"),
        help="When using --keyword: note type filter (default: 图文).",
    )
    parser.add_argument(
        "--limit-notes",
        type=int,
        default=24,
        help="Max notes to take cover images from (keyword mode).",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=12,
        help="Max total images to download.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Save folder (default: Desktop\\xhs_picset_YYYYMMDD-NN).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--account", default=None)
    parser.add_argument(
        "--reuse-existing-tab",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="小红书 search-feeds：复用已有标签页，尽量不新开搜索结果页（默认开启）。",
    )
    parser.add_argument(
        "--prefer-ecommerce-covers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "关键词搜封面：多拉一些候选，下载后优先保留更像「电商实拍图」的封面，"
            "弱化大字报/纯文字/强信息图样式（默认开启；需 Pillow，有 numpy 则更准）。"
        ),
    )
    parser.add_argument(
        "--strict-step-lock",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Hard gate mode (default ON): do not continue on incomplete/failed steps.",
    )
    parser.add_argument(
        "--picset-network-precheck",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Check Picset/CDN reachability before generate (default ON).",
    )
    parser.add_argument(
        "--restart-browser-for-account",
        action="store_true",
        default=False,
        help="Local host: restart Chrome using given --account before operations. "
        "Default OFF to keep the browser windows alive during multi-account runs.",
    )
    parser.add_argument("--picset-url", default=DEFAULT_PICSET_CN)
    parser.add_argument("--login-timeout", type=int, default=120)
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Only download to folder; do not open Picset.",
    )
    parser.add_argument(
        "--product-images",
        nargs="+",
        default=None,
        metavar="PATH",
        help=(
            "Local product/SKU images uploaded to Picset 「产品素材图」(after reference uploads). "
            "Pass every run when the page requires product materials."
        ),
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="After uploading references, fill prompt and click Picset generate button.",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Prompt text for generation (used with --generate).",
    )
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="UTF-8 file with prompt (overrides --prompt).",
    )
    parser.add_argument(
        "--generate-timeout",
        type=int,
        default=120,
        help="Seconds to wait for Picset result images after clicking generate (default: 120).",
    )
    parser.add_argument(
        "--generated-output-dir",
        default=None,
        help=(
            "Folder for Picset generated images (default: Desktop\\Picset生成图_YYYYMMDD-NN)."
        ),
    )
    parser.add_argument(
        "--max-download",
        type=int,
        default=1,
        help="Max generated images to download when using --generate (default: 1).",
    )
    parser.add_argument(
        "--picset-batch-size",
        type=int,
        default=1,
        metavar="N",
        help=(
            "在 Picset 界面尽量切换到「生成 N 张」（如 4）再点生成；默认与 "
            "`--max-download` 相同。若单次未出够图会自动多轮补足。"
        ),
    )
    parser.add_argument(
        "--publish-to-xhs",
        action="store_true",
        help="After generation, publish downloaded images via publish_pipeline.py.",
    )
    parser.add_argument("--preview", action="store_true", help="Publish pipeline: fill only, do not submit.")
    parser.add_argument("--title", default=None, help="XHS title (with --publish-to-xhs).")
    parser.add_argument("--title-file", default=None, help="UTF-8 file for XHS title.")
    parser.add_argument("--content", default=None, help="XHS note body (with --publish-to-xhs).")
    parser.add_argument("--content-file", default=None, help="UTF-8 file for XHS body.")
    parser.add_argument(
        "--disconnect-cdp",
        action="store_true",
        help="Close DevTools WebSocket on exit. Default leaves the browser tab open.",
    )
    parser.add_argument(
        "--summary-json",
        default=None,
        help="Also write the final stdout summary JSON to this UTF-8 file (for orchestration).",
    )
    parser.add_argument(
        "--watermark-post-workflow",
        action="store_true",
        help=(
            "After XHS images are downloaded: create postprocess/ 说明、子目录，"
            "并可选打开无痕清印与资源管理器；人工去水印后按说明用 PS 批处理自动色调/对比度/颜色。"
        ),
    )
    parser.add_argument(
        "--watermark-tool-url",
        default="https://wuhenqingyin.com.cn/#",
        help="去水印工具页（默认无痕清印）。",
    )
    parser.add_argument(
        "--no-open-watermark-url",
        action="store_true",
        help="不自动在浏览器打开去水印网站（与 --watermark-post-workflow 或 --watermark-full-auto 同用）。",
    )
    parser.add_argument(
        "--watermark-full-auto",
        action="store_true",
        help=(
            "下载后全自动处理：OpenCV 右下角蒙版修复 + Pillow 自动优化 + 可选 Photoshop COM；"
            "与 --watermark-post-workflow 二选一（不要同时开）。"
            "默认仍会在浏览器打开无痕清印（可选用该站人工再修图），"
            "若不要打开网页请加 --no-open-watermark-url。"
        ),
    )
    parser.add_argument(
        "--watermark-no-inpaint",
        action="store_true",
        help="与 --watermark-full-auto 同用：跳过 OpenCV 修复，仅复制原图进 01_去水印后（仍走 Pillow/PS）。",
    )
    parser.add_argument(
        "--watermark-corner-w",
        type=float,
        default=0.36,
        help="与 --watermark-full-auto 同用：右下角水印区宽度占图宽比例（默认 0.36）。",
    )
    parser.add_argument(
        "--watermark-corner-h",
        type=float,
        default=0.14,
        help="与 --watermark-full-auto 同用：右下角水印区高度占图高比例（默认 0.14）。",
    )
    parser.add_argument(
        "--watermark-photoshop",
        action="store_true",
        help="与 --watermark-full-auto 同用：已装 Photoshop + pywin32 时尝试 JSX 批处理自动色调/对比度/颜色。",
    )
    parser.add_argument(
        "--photoshop-after-generate",
        action="store_true",
        help=(
            "需同时 --generate：Picset「生成图」下载到本机后，再对该目录下生成文件做 Photoshop "
            "「图像→自动色调/对比度/颜色」等价的 JSX 批处理（需 PS + pywin32）；"
            "发布与 summary 优先使用处理后路径。"
        ),
    )
    parser.add_argument(
        "--manual-draw-before-photoshop",
        action="store_true",
        dest="manual_draw_before_photoshop",
        help=(
            "与 --photoshop-after-generate 同用：启用「画图软件人工处理」门禁（落盘后到 "
            "postprocess_ps/ 下创建信号文件后才继续）。"
            "**默认关闭**：编排/多账号链路会自动进 Photoshop 调色，避免无限等待 READY.signal。"
        ),
    )
    parser.add_argument(
        "--skip-manual-draw-before-photoshop",
        action="store_true",
        dest="skip_manual_draw_before_photoshop",
        help=(
            "与 --photoshop-after-generate 同用（兼容旧参数）："
            "强制关闭人工画图门禁（即使开了 --manual-draw-before-photoshop 也会关闭）。"
        ),
    )
    parser.add_argument(
        "--require-manual-draw-output",
        action="store_true",
        help=(
            "与 --photoshop-after-generate 同用：要求在 manual_draw_output 目录检测到人工处理结果，"
            "否则退出，防止误用未画图素材继续发布。"
        ),
    )
    parser.add_argument(
        "--manual-draw-signal-file",
        default="READY.signal",
        help="Manual draw gate signal filename under postprocess_ps (default: READY.signal).",
    )
    parser.add_argument(
        "--manual-draw-wait-timeout-seconds",
        type=int,
        default=0,
        help=(
            "人工画图门禁等待 READY.signal 的超时（秒）。仅与 --manual-draw-before-photoshop 同用。"
            "默认为 0：将自动采用 4 小时上限；显式设为正数则按该秒数等待。"
        ),
    )
    parser.add_argument(
        "--wait-enter-after-watermark",
        action="store_true",
        help=(
            "完成水印相关步骤后暂停：在终端按回车再继续 Picset 上传与 --generate。"
            "用于先在无痕清印处理参考图、按说明落盘后再进生图。"
        ),
    )
    parser.add_argument(
        "--require-watermarked-references",
        action="store_true",
        dest="require_watermarked_references",
        help=(
            "与 --watermark-post-workflow + --wait-enter-after-watermark 同用："
            "放行 Picset 前若 postprocess/01_去水印后 无有效参考图则退出。"
        ),
    )
    args = parser.parse_args()
    _debug_log(
        "H14",
        "xhs_images_to_picset.py:main:args",
        "xhs_images_to_picset args parsed",
        {
            "has_keyword": bool(args.keyword),
            "generate": bool(args.generate),
            "generate_timeout": int(args.generate_timeout),
            "max_download": int(args.max_download),
            "login_timeout": int(args.login_timeout),
        },
    )

    if args.feed_id and not args.xsec_token:
        print("Error: --xsec-token is required with --feed-id.", file=sys.stderr)
        sys.exit(2)
    if args.keyword and args.feed_id:
        print("Error: use either --keyword or --feed-id, not both.", file=sys.stderr)
        sys.exit(2)
    if not args.keyword and not args.feed_id:
        print("Error: provide --keyword or --feed-id + --xsec-token.", file=sys.stderr)
        sys.exit(2)
    if args.require_watermarked_references:
        if not args.watermark_post_workflow:
            print(
                "Error: --require-watermarked-references 需要同时 --watermark-post-workflow。",
                file=sys.stderr,
            )
            sys.exit(2)
        if not args.wait_enter_after_watermark:
            print(
                "Error: --require-watermarked-references 需要同时 --wait-enter-after-watermark。",
                file=sys.stderr,
            )
            sys.exit(2)

    if args.watermark_full_auto and args.watermark_post_workflow:
        print(
            "Error: use either --watermark-full-auto or --watermark-post-workflow, not both.",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.generate and args.skip_upload:
        print("Error: --generate cannot be used with --skip-upload.", file=sys.stderr)
        sys.exit(2)
    if args.publish_to_xhs and not args.generate:
        print("Error: --publish-to-xhs requires --generate.", file=sys.stderr)
        sys.exit(2)
    if args.photoshop_after_generate and not args.generate:
        print(
            "Error: --photoshop-after-generate requires --generate.",
            file=sys.stderr,
        )
        sys.exit(2)

    output_dir = args.output_dir or make_desktop_session_dir()
    print(f"[xhs2picset] Save folder: {output_dir}")

    product_paths: list[str] = []
    if args.product_images:
        product_paths = _resolve_existing_files(list(args.product_images))
        if not product_paths:
            print(
                "Error: --product-images given but no valid files found.",
                file=sys.stderr,
            )
            sys.exit(2)
        print(f"[xhs2picset] Product material files: {len(product_paths)}")

    prefer_shop = getattr(args, "prefer_ecommerce_covers", True)
    kw_pull = int(args.limit_notes)
    if prefer_shop and args.keyword:
        kw_pull = min(
            48,
            max(int(args.limit_notes), int(args.max_images) * 3, 15),
        )

    urls: list[str] = []
    if args.feed_id:
        urls = run_detail_all_images(
            host=args.host,
            port=args.port,
            feed_id=args.feed_id.strip(),
            xsec_token=args.xsec_token.strip(),
        )
        print(f"[xhs2picset] Detail images found: {len(urls)}")
    else:
        assert args.keyword is not None
        _debug_log(
            "H14",
            "xhs_images_to_picset.py:main:search_start",
            "Starting search cover collection",
            {
                "keyword_len": len(args.keyword.strip()),
                "limit_notes": int(kw_pull),
                "prefer_ecommerce_pull": prefer_shop,
            },
        )
        urls = run_search_covers(
            keyword=args.keyword.strip(),
            sort_by=args.sort_by,
            limit_notes=int(kw_pull),
            host=args.host,
            port=int(args.port),
            account=args.account,
            publish_time=str(args.publish_time),
            note_type=str(args.note_type),
            reuse_existing_tab=bool(args.reuse_existing_tab),
        )
        _debug_log(
            "H14",
            "xhs_images_to_picset.py:main:search_end",
            "Search cover collection finished",
            {"url_count": len(urls)},
        )
        print(f"[xhs2picset] Cover images from search: {len(urls)}")

    mi = max(1, int(args.max_images))
    if args.feed_id:
        urls = urls[: min(len(urls), max(mi * 3, 18) if prefer_shop else mi)]
    elif args.keyword and not prefer_shop:
        urls = urls[:mi]
    # keyword + prefer_shop: download all URLs returned up to kw_pull cap
    paths: list[str] = []
    if not urls:
        if args.strict_step_lock:
            print(
                "[xhs2picset] Error: search returned 0 URLs under strict-step-lock; abort.",
                file=sys.stderr,
            )
            sys.exit(2)
        # Fallback: when home feed search is blocked (e.g. IP risk / NOT_LOGGED_IN),
        # reuse the last successful local reference images so Picset can still generate.
        cached_paths: list[str] = []
        if args.summary_json:
            summary_p = Path(args.summary_json)
            if summary_p.is_file():
                try:
                    payload = json.loads(summary_p.read_text(encoding="utf-8"))
                    cached_paths = (
                        payload.get("reference_local_paths")
                        or payload.get("local_paths")
                        or []
                    )
                except Exception:
                    cached_paths = []
        cached_paths = [
            str(p)
            for p in cached_paths
            if p and isinstance(p, str) and Path(p).is_file()
        ]
        if cached_paths:
            paths = cached_paths[: max(1, args.max_images)]
            print(
                "[xhs2picset] No image URLs collected from search; "
                "reusing cached reference_local_paths from summary-json.",
                flush=True,
            )
        else:
            print("[xhs2picset] No image URLs collected (and no cached references).", file=sys.stderr)
            sys.exit(2)
    else:
        downloader = ImageDownloader(temp_dir=output_dir)
        paths = downloader.download_all(urls)
    print(f"[xhs2picset] Downloaded {len(paths)} file(s)." )

    # Prefer e-commerce-ish photos vs typography / poster covers; optionally combine with
    # aHash distance to SKU product (--product-images).
    if paths and (prefer_shop or bool(product_paths)):
        try:
            from PIL import Image

            def _ahash_bits_local(img_path: str, hash_size: int = 8) -> list[int]:
                im = Image.open(img_path).convert("L").resize((hash_size, hash_size))
                px = list(im.getdata())
                mean_val = sum(px) / float(len(px))
                return [1 if p > mean_val else 0 for p in px]

            def _hamming_local(a: list[int], b: list[int]) -> int:
                if len(a) != len(b):
                    return 10**9
                return sum(1 for x, y in zip(a, b) if x != y)

            prod_bits_lp: list[int] | None = None
            if product_paths:
                try:
                    prod_bits_lp = _ahash_bits_local(product_paths[0])
                except Exception:
                    prod_bits_lp = None

            triples: list[tuple[str, float, int]] = []
            for p in paths:
                eco_f = (
                    float(_ecommerce_cover_preference_score(p))
                    if prefer_shop
                    else 0.0
                )
                hm_i = 0
                if prod_bits_lp is not None:
                    try:
                        hm_i = _hamming_local(
                            prod_bits_lp,
                            _ahash_bits_local(p),
                        )
                    except Exception:
                        hm_i = 9999
                triples.append((p, eco_f, hm_i))

            if prefer_shop and prod_bits_lp is not None:
                triples.sort(key=lambda t: (-t[1], t[2]))
            elif prefer_shop:
                triples.sort(key=lambda t: -t[1])
            elif prod_bits_lp is not None:
                triples.sort(key=lambda t: t[2])

            paths = [t[0] for t in triples]
            if prefer_shop:
                paths = paths[:mi]
                print(
                    f"[xhs2picset] E-commerce-ish cover ranking: kept top {len(paths)} "
                    f"of {len(triples)} downloaded (product-sim tie-break)."
                    if product_paths
                    else f"[xhs2picset] E-commerce-ish cover ranking: kept top "
                    f"{len(paths)} of {len(triples)} downloaded.",
                    flush=True,
                )
            elif prod_bits_lp is not None:
                print(
                    f"[xhs2picset] Product match: reordered {len(paths)} reference cover(s) "
                    "by similarity.",
                    flush=True,
                )
        except Exception:
            if prefer_shop:
                paths = paths[:mi]

    if args.watermark_full_auto:
        from postprocess_xhs_workflow import run_full_auto_pipeline

        run_full_auto_pipeline(
            output_dir,
            paths,
            no_inpaint=bool(args.watermark_no_inpaint),
            corner_width_ratio=float(args.watermark_corner_w),
            corner_height_ratio=float(args.watermark_corner_h),
            photoshop_batch=bool(args.watermark_photoshop),
        )
        if not bool(args.no_open_watermark_url):
            from postprocess_xhs_workflow import open_watermark_tool_url

            open_watermark_tool_url(
                (args.watermark_tool_url or "").strip(),
                label="[xhs2picset]",
            )
    elif args.watermark_post_workflow:
        from postprocess_xhs_workflow import materialize_after_xhs_download

        materialize_after_xhs_download(
            output_dir,
            paths,
            watermark_url=(args.watermark_tool_url or "").strip()
            or "https://wuhenqingyin.com.cn/#",
            open_browser=not bool(args.no_open_watermark_url),
            open_folder=True,
        )

    if (
        (args.watermark_full_auto or args.watermark_post_workflow)
        and args.wait_enter_after_watermark
        and not args.skip_upload
    ):
        wurl = (args.watermark_tool_url or "").strip() or "https://wuhenqingyin.com.cn/#"
        clean_dir_gate = Path(output_dir) / "postprocess" / "01_去水印后"
        if args.watermark_post_workflow:
            from postprocess_xhs_workflow import print_watermark_site_human_gate

            print_watermark_site_human_gate(
                wurl,
                clean_dir=clean_dir_gate,
                label="[xhs2picset]",
            )
        else:
            print(
                "[xhs2picset]「全自动水印」后继续：可按需再在浏览器打开无痕清印校对；按回继续 Picset。",
                flush=True,
            )
        print(
            "[xhs2picset] 就绪后在终端按 **回车** 继续 Picset 上传与生图。（Ctrl+C 取消）",
            flush=True,
        )
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("[xhs2picset] 已中断。", file=sys.stderr)
            sys.exit(130)

    if args.watermark_post_workflow:
        pp_clean = Path(output_dir) / "postprocess" / "01_去水印后"
        if pp_clean.is_dir():
            cleaned_refs = sorted(
                str(p.resolve())
                for p in pp_clean.iterdir()
                if p.is_file()
                and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            )
        else:
            cleaned_refs = []

        if args.require_watermarked_references:
            if not cleaned_refs:
                print(
                    "[xhs2picset] Error: 已启用「必须先有无水印参考」。"
                    f"但未在下列目录找到 jpg/png/webp：{pp_clean}",
                    "\n请先完成无痕清印解析，把成品保存进该文件夹后再跑一次。",
                    file=sys.stderr,
                    sep="",
                )
                sys.exit(2)

        if cleaned_refs:
            print(
                f"[xhs2picset] 使用「postprocess/01_去水印后」共 {len(cleaned_refs)} 张作为 Picset 参考上传。",
                flush=True,
            )
            paths = cleaned_refs

    if args.skip_upload:
        print("[xhs2picset] Done (skip-upload).")
        return

    if _is_local_host(args.host):
        # Important for multi-account flows:
        # `ensure_chrome` only checks "port open" and won't guarantee that the
        # existing Chrome instance belongs to `args.account`. If the port is
        # already occupied by another profile, XHS login/search can fail.
        if args.account and args.restart_browser_for_account:
            restart_chrome(
                port=args.port,
                headless=args.headless,
                account=args.account,
            )
            time.sleep(1)

        if not ensure_chrome(
            port=args.port,
            headless=args.headless,
            account=args.account,
        ):
            # Only restart as a last resort when the port isn't reachable.
            if args.account:
                restart_chrome(
                    port=args.port,
                    headless=args.headless,
                    account=args.account,
                )
            if not ensure_chrome(
                port=args.port,
                headless=args.headless,
                account=args.account,
            ):
                print("Error: could not ensure browser.", file=sys.stderr)
                sys.exit(2)

    publisher = XiaohongshuPublisher(
        host=args.host,
        port=args.port,
        account_name=args.account,
    )
    try:
        # Prefer an existing Picset tab; avoid hijacking a random tab then full-reloading Picset
        # (which often drops in-SPA session and forces re-login).
        publisher.connect(
            target_url_prefix="https://picsetai",
            reuse_existing_tab=args.reuse_existing_tab,
        )
        _debug_log(
            "H15",
            "xhs_images_to_picset.py:main:publisher_connected",
            "CDP connected and before Picset workspace checks",
            {"host": args.host, "port": int(args.port)},
        )
        publisher._navigate_picset_preserving_session(args.picset_url)
        _wait_for_login_if_needed(publisher, timeout_seconds=args.login_timeout)
        _enter_picset_workspace_if_needed(publisher)
        _require_picset_upload_ui(
            publisher,
            timeout_seconds=max(60, min(150, args.login_timeout)),
            phase="进入工作台后",
        )

        # Do NOT install fetch/XHR hooks until uploads finish — patching network during
        # file upload has been observed to destabilize Picset (session / kicked to login).

        ok = _upload_reference_images_via_cdp(
            publisher,
            paths,
            target_kind="reference",
        )
        if not ok:
            print("[xhs2picset] Upload to Picset reference slot failed.", file=sys.stderr)
            sys.exit(2)
        print("[xhs2picset] Uploaded to Picset 「参考设计图」.")

        if product_paths:
            ok_prod = _upload_reference_images_via_cdp(
                publisher,
                product_paths,
                target_kind="product",
            )
            if not ok_prod:
                print(
                    "[xhs2picset] Upload to Picset 「产品素材图」 failed.",
                    file=sys.stderr,
                )
                sys.exit(2)
            print("[xhs2picset] Uploaded to Picset 「产品素材图」.")

        baseline_after_upload: set[str] | None = None
        if args.generate:
            _install_network_image_hooks(publisher)
            baseline_after_upload = _baseline_urls_after_uploads(publisher)
            _clear_network_capture_buffer(publisher)
            if args.picset_network_precheck:
                net = _picset_network_healthcheck(publisher, timeout_seconds=20)
                print(f"[xhs2picset] Picset precheck: {json.dumps(net, ensure_ascii=False)}")
                if args.strict_step_lock:
                    if (not bool(net.get("online", True))
                        or not bool((net.get("picset") or {}).get("ok", False))
                        or not bool((net.get("xhsCdn") or {}).get("ok", False))):
                        raise RuntimeError(
                            "Picset network precheck failed under strict-step-lock."
                        )

        generated_urls: list[str] = []
        generated_local_paths: list[str] = []
        generated_dir_used: str | None = None
        photoshop_gen_meta: dict[str, Any] | None = None

        if args.generate:
            if args.prompt_file:
                with open(args.prompt_file, encoding="utf-8") as f:
                    gen_prompt = f.read().strip()
            elif args.prompt:
                gen_prompt = args.prompt.strip()
            else:
                gen_prompt = (
                    "参考已上传图片的风格与排版，生成一张电商详情主图，"
                    "新中式高级感，留白标题区，4:5 竖版。"
                )
            if not gen_prompt:
                print("Error: empty prompt for --generate.", file=sys.stderr)
                sys.exit(2)

            want = max(1, args.max_download)
            batch_ui = (
                args.picset_batch_size
                if args.picset_batch_size is not None
                else want
            )
            batch_ui = max(1, min(16, int(batch_ui)))

            ui_pick = _retry_on_cdp_promise_collected(
                lambda: _picset_try_set_batch_count(publisher, batch_ui),
                label="set_batch_count",
            )
            print(
                "[xhs2picset] Picset 「生成张数」界面（尽力切换）:",
                json.dumps(ui_pick, ensure_ascii=False),
            )

            accumulated_urls: list[str] = []
            known_urls: set[str] = set(
                _normalize_generated_url(u) for u in (baseline_after_upload or ())
            )
            per_round_timeout = max(
                45,
                min(360, args.generate_timeout // max(1, want) + 45),
            )

            # Prefer one-shot: UI 设为 N 张（如 4）且希望一次拿够 → 单次点击 + 更长等待。
            use_one_shot = batch_ui >= want
            if use_one_shot:
                _clear_network_capture_buffer(publisher)
                _debug_log(
                    "H15",
                    "xhs_images_to_picset.py:main:one_shot_start",
                    "Starting one-shot generate collection",
                    {"want": int(want), "batch_ui": int(batch_ui), "timeout": int(args.generate_timeout)},
                )
                trigger = _retry_on_cdp_promise_collected(
                    lambda: _fill_prompt_and_generate(
                        publisher,
                        gen_prompt,
                        paths + product_paths,
                        batch_hint=batch_ui,
                    ),
                    label="fill_prompt_and_generate_one_shot",
                )
                print(
                    "[xhs2picset] Generate trigger (one-shot, UI batch=%d):"
                    % batch_ui,
                    json.dumps(trigger, ensure_ascii=False),
                )
                if not trigger.get("ok"):
                    raise RuntimeError(
                        "Picset: prompt input or generate button not found (see trigger JSON)."
                    )
                clicked_label = str(trigger.get("clickedLabel") or "")
                if batch_ui > 1 and str(batch_ui) not in clicked_label:
                    raise RuntimeError(
                        "Picset 生成按钮未处于目标张数。"
                        f"期望包含「{batch_ui}」，实际 clickedLabel={clicked_label!r}。"
                    )
                batch = _retry_on_cdp_promise_collected(
                    lambda: _collect_generated_image_urls(
                        publisher=publisher,
                        timeout_seconds=max(90, min(900, int(args.generate_timeout))),
                        baseline_urls=set(known_urls),
                        stable_rounds=2,
                        min_count=min(want, batch_ui),
                    ),
                    label="collect_generated_urls_one_shot",
                )
                for u in batch:
                    if not (isinstance(u, str) and u.startswith("http")):
                        continue
                    nu = _normalize_generated_url(u)
                    if nu and nu not in known_urls:
                        accumulated_urls.append(u)
                        known_urls.add(nu)
                print(
                    f"[xhs2picset] One-shot collected {len(accumulated_urls)} URL(s) "
                    f"(target {want})."
                )
                if args.strict_step_lock and len(accumulated_urls) < 1:
                    unavailable = _picset_result_unavailable_state(publisher)
                    if unavailable.get("hasUnavailable"):
                        raise RuntimeError(
                            "Picset result shows '不可用' after generation trigger; "
                            "strict-step-lock blocks fallback rounds."
                        )
                _debug_log(
                    "H15",
                    "xhs_images_to_picset.py:main:one_shot_end",
                    "One-shot generate collection finished",
                    {"collected": len(accumulated_urls), "target": int(want)},
                )

            # 单次不够则多轮补足（未走 one-shot 或 one-shot 未满时）。
            rounds_done = 0
            max_fallback_rounds = max(want * 3, 12)
            stall = 0
            while len(accumulated_urls) < want and rounds_done < max_fallback_rounds:
                prev_n = len(accumulated_urls)
                _clear_network_capture_buffer(publisher)
                trigger = _retry_on_cdp_promise_collected(
                    lambda: _fill_prompt_and_generate(
                        publisher,
                        gen_prompt,
                        paths + product_paths,
                        batch_hint=None,
                    ),
                    label="fill_prompt_and_generate_fallback",
                )
                rounds_done += 1
                print(
                    "[xhs2picset] Generate trigger (fallback %d):"
                    % rounds_done,
                    json.dumps(trigger, ensure_ascii=False),
                )
                if not trigger.get("ok"):
                    raise RuntimeError(
                        "Picset: prompt input or generate button not found (see trigger JSON)."
                    )

                batch = _retry_on_cdp_promise_collected(
                    lambda: _collect_generated_image_urls(
                        publisher=publisher,
                        timeout_seconds=per_round_timeout,
                        baseline_urls=set(known_urls),
                        stable_rounds=2,
                        min_count=1,
                    ),
                    label="collect_generated_urls_fallback",
                )
                before_ct = len(accumulated_urls)
                for u in batch:
                    if not (isinstance(u, str) and u.startswith("http")):
                        continue
                    nu = _normalize_generated_url(u)
                    if nu and nu not in known_urls:
                        accumulated_urls.append(u)
                        known_urls.add(nu)
                print(
                    f"[xhs2picset] Fallback round {rounds_done}: "
                    f"+{len(accumulated_urls) - before_ct} new URL(s); total {len(accumulated_urls)}"
                )
                if len(accumulated_urls) >= want:
                    break
                if len(accumulated_urls) == prev_n:
                    stall += 1
                    if stall >= 3:
                        unavailable = _picset_result_unavailable_state(publisher)
                        if unavailable.get("hasUnavailable"):
                            raise RuntimeError(
                                "Picset result appears unavailable ('不可用'); stop and retry later."
                            )
                        print(
                            "[xhs2picset] Warning: no new images in 3 fallback tries; "
                            "stopping early.",
                            file=sys.stderr,
                        )
                        break
                else:
                    stall = 0
                time.sleep(3.0)

            generated_urls = accumulated_urls[:want]
            if not generated_urls:
                raise RuntimeError(
                    "No generated image URLs detected before timeout "
                    f"({args.generate_timeout}s total budget). "
                    "Try increasing --generate-timeout or reduce --max-download."
                )

            print(f"[xhs2picset] Found {len(generated_urls)} generated image URL(s) after up to {want} round(s).")

            generated_dir_used = (
                args.generated_output_dir or make_desktop_generated_dir()
            )
            Path(generated_dir_used).mkdir(parents=True, exist_ok=True)
            print(f"[xhs2picset] 生成图目录（桌面）: {generated_dir_used}")

            raw_downloaded = _download_urls(generated_urls, generated_dir_used)
            generated_local_paths = _validate_downloaded_images(raw_downloaded)
            if not generated_local_paths:
                raise RuntimeError(
                    "Failed to download or validate generated images "
                    "(check Picset page and network)."
                )
            generated_local_paths, dup_local = _dedupe_files_by_sha256(generated_local_paths)
            if dup_local:
                print(
                    f"[xhs2picset] Deduplicated identical generated files by hash: "
                    f"-{len(dup_local)} file(s)."
                )
            if len(generated_local_paths) < want:
                raise RuntimeError(
                    f"Need {want} different generated images, but only got "
                    f"{len(generated_local_paths)} unique files after dedup."
                )

            print("[xhs2picset] Saved generated images:", len(generated_local_paths))

            if args.summary_json:
                _write_generation_checkpoint_summary(
                    Path(args.summary_json),
                    output_dir=output_dir,
                    reference_paths=paths,
                    product_material_paths=product_paths,
                    generated_urls=list(generated_urls),
                    generated_local_paths=list(generated_local_paths),
                    generated_output_dir=generated_dir_used,
                )

            if args.photoshop_after_generate:
                print(
                    "[xhs2picset] Entering Photoshop auto color stage "
                    f"with {len(generated_local_paths)} image(s)...",
                    flush=True,
                )
                # Human-in-the-loop gate: opt-in via --manual-draw-before-photoshop.
                # Default OFF so bulk / full_stack never blocks on READY.signal forever.
                use_manual_gate = bool(
                    getattr(args, "manual_draw_before_photoshop", False)
                ) and not bool(getattr(args, "skip_manual_draw_before_photoshop", False))
                if use_manual_gate:
                    manual_root = Path(generated_dir_used) / "postprocess_ps"
                    manual_in = manual_root / "manual_draw_input"
                    manual_out = manual_root / "manual_draw_output"
                    manual_in.mkdir(parents=True, exist_ok=True)
                    manual_out.mkdir(parents=True, exist_ok=True)

                    # Refresh manual input folder with current generated files.
                    for f in list(manual_in.glob("*")):
                        if f.is_file():
                            try:
                                f.unlink()
                            except Exception:
                                pass
                    for src in generated_local_paths:
                        sp = Path(src)
                        if sp.is_file():
                            try:
                                dst = manual_in / sp.name
                                dst.write_bytes(sp.read_bytes())
                            except Exception:
                                pass

                    print(
                        "[xhs2picset] Manual draw gate: please edit images in Paint first.",
                        flush=True,
                    )
                    print(
                        f"[xhs2picset] 1) Open and edit files in: {manual_in}",
                        flush=True,
                    )
                    print(
                        f"[xhs2picset] 2) Save edited results to: {manual_out}",
                        flush=True,
                    )
                    if sys.platform == "win32":
                        try:
                            os.startfile(str(manual_in))  # noqa: S606
                            os.startfile(str(manual_out))  # noqa: S606
                        except Exception:
                            pass
                    print(
                        "[xhs2picset] Manual draw gate: create signal file to continue.",
                        flush=True,
                    )
                    signal_name = str(args.manual_draw_signal_file or "READY.signal").strip() or "READY.signal"
                    signal_path = manual_root / signal_name
                    print(
                        f"[xhs2picset] Waiting for signal file: {signal_path}",
                        flush=True,
                    )
                    waited = 0
                    timeout_s = max(0, int(args.manual_draw_wait_timeout_seconds))
                    if timeout_s <= 0:
                        timeout_s = 4 * 3600
                        print(
                            "[xhs2picset] manual-draw gate: 未指定 --manual-draw-wait-timeout-seconds 时 "
                            f"最长等待 {timeout_s}s（避免误配为无限阻塞）。",
                            flush=True,
                        )
                    while not signal_path.is_file():
                        time.sleep(1.0)
                        waited += 1
                        if waited >= timeout_s:
                            print(
                                f"[xhs2picset] Error: manual draw signal timeout ({timeout_s}s).",
                                file=sys.stderr,
                            )
                            sys.exit(2)
                    try:
                        signal_path.unlink()
                    except Exception:
                        pass

                    edited_paths = sorted(
                        str(p.resolve())
                        for p in manual_out.iterdir()
                        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                    )
                    if edited_paths:
                        generated_local_paths = edited_paths
                    elif args.require_manual_draw_output:
                        print(
                            "[xhs2picset] Error: no files found in manual_draw_output; "
                            "abort by --require-manual-draw-output.",
                            file=sys.stderr,
                        )
                        sys.exit(2)
                    else:
                        print(
                            "[xhs2picset] Warning: no manual draw output found; "
                            "continue with pre-draw generated files.",
                            file=sys.stderr,
                        )

                from postprocess_xhs_workflow import (
                    materialize_generated_photoshop_autotcc,
                )

                generated_local_paths, photoshop_gen_meta = (
                    materialize_generated_photoshop_autotcc(
                        generated_dir_used,
                        generated_local_paths,
                    )
                )

            if args.publish_to_xhs:
                # Safety guard: never publish using raw XHS reference images.
                # Only allow files under the current generated output directory.
                if generated_dir_used:
                    gen_root = Path(generated_dir_used).resolve()
                    generated_local_paths = [
                        str(Path(p).resolve())
                        for p in generated_local_paths
                        if Path(p).is_file() and Path(p).resolve().is_relative_to(gen_root)
                    ]
                if not generated_local_paths:
                    print(
                        "Error: no valid generated images for publish "
                        "(blocked reference-image fallback).",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                try:
                    pub_title = _read_text(args.title_file, args.title, "title")
                    pub_content = _read_text(args.content_file, args.content, "content")
                except ValueError as exc:
                    print(f"Error: {exc}", file=sys.stderr)
                    sys.exit(2)

                exit_code = _publish_to_xhs(
                    title=pub_title,
                    content=pub_content,
                    image_paths=generated_local_paths,
                    account=args.account,
                    host=args.host,
                    port=args.port,
                    headless=args.headless,
                    preview=args.preview,
                )
                if exit_code != 0:
                    sys.exit(exit_code)

        summary: dict[str, Any] = {
            "output_dir": output_dir,
            "reference_local_paths": paths,
            "reference_count": len(paths),
            # Legacy keys (same as reference_*)
            "local_paths": paths,
            "count": len(paths),
        }
        if product_paths:
            summary["product_local_paths"] = product_paths
            summary["product_count"] = len(product_paths)
        if args.generate:
            summary["generated_image_urls"] = generated_urls
            summary["generated_local_paths"] = generated_local_paths
            summary["generated_output_dir"] = generated_dir_used
            if photoshop_gen_meta is not None:
                summary["photoshop_after_generate"] = photoshop_gen_meta

        blob = json.dumps(summary, ensure_ascii=False, indent=2)
        print(blob)
        if args.summary_json:
            Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.summary_json).write_text(blob + "\n", encoding="utf-8")
            print(f"[xhs2picset] summary-json → {args.summary_json}")
    except (CDPError, RuntimeError) as e:
        _write_failure_snapshot(
            publisher if "publisher" in locals() else None,
            stage="main_exception",
            err_text=str(e),
        )
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    finally:
        if args.disconnect_cdp:
            publisher.disconnect()


if __name__ == "__main__":
    main()
