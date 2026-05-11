"""
Bulk publish helper for multi-account Xiaohongshu operations.

Design:
1) Prepare once (optional): run full_stack_xhs_picset_publish.py with --preview-publish
   (Step C previews only — no Publish click). CDP uses the first filtered account's
   host/port/account so Picset+XHS Chrome matches multi-account setups (not hardcoded 9222).
2) Publish to each account via publish_pipeline.py with --account and
   --restart-browser-for-account (--preview disables the publish click).
3) Apply throttle + retries and save a machine-readable report.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from core_config import resolve_account_port, resolve_runtime_target
from core_logger import create_run_logger
from media_path_utils import dedupe_existing_paths_by_hash, validate_publish_images

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TMP_DIR = REPO_ROOT / "tmp"


if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _print_subprocess_capture(text: str | None, file) -> None:
    """Print captured UTF-8 child output without crashing on GBK consoles."""
    if not text:
        return
    if not text.endswith("\n"):
        text = text + "\n"
    try:
        file.write(text)
        file.flush()
    except UnicodeEncodeError:
        buf = getattr(file, "buffer", None)
        if buf is not None:
            buf.write(text.encode("utf-8", errors="backslashreplace"))
            buf.flush()


def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_accounts(selected: list[str] | None) -> list[dict]:
    from account_manager import list_accounts

    all_rows = [a for a in list_accounts() if a.get("name")]
    all_names = [a.get("name") for a in all_rows]
    if selected:
        out = [a.strip() for a in selected if a.strip()]
        missing = [a for a in out if a not in all_names]
        if missing:
            raise SystemExit(f"Unknown account(s): {', '.join(missing)}")
        out_rows: list[dict] = []
        for name in out:
            row = next((x for x in all_rows if x.get("name") == name), None)
            if row:
                out_rows.append(row)
        return out_rows
    return all_rows


def _sleep_jitter(min_s: float, max_s: float) -> None:
    if max_s <= 0:
        return
    low = max(0.0, min(min_s, max_s))
    high = max(low, max_s)
    time.sleep(random.uniform(low, high))


def _run(
    cmd: list[str],
    cwd: Path,
    *,
    timeout_seconds: int = 0,
) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _drain(stream, store, out):
        for line in iter(stream.readline, ""):
            out.write(line)
            out.flush()
            store.append(line)
        stream.close()

    import threading
    t1 = threading.Thread(target=_drain, args=(proc.stdout, stdout_lines, sys.stdout))
    t2 = threading.Thread(target=_drain, args=(proc.stderr, stderr_lines, sys.stderr))
    t1.start()
    t2.start()

    try:
        timeout = max(1, int(timeout_seconds)) if timeout_seconds else None
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        t1.join()
        t2.join()

    rc = proc.returncode
    if rc is None:
        rc = 124
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=rc,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )


def _prepare_once(
    args,
    *,
    cdp_host: str,
    cdp_port: int,
    prepare_account: str | None,
    timeout_seconds: int,
) -> tuple[Path, Path, list[str]]:
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    full_stack = SCRIPT_DIR / "full_stack_orchestrated.py"
    cmd = [
        sys.executable,
        str(full_stack),
        "--product-images",
        *args.product_images,
        "--reference-count",
        str(max(1, args.reference_count)),
        "--max-download",
        str(max(1, args.max_download)),
        "--picset-batch-size",
        str(max(1, args.picset_batch_size)),
        "--generate-timeout",
        str(max(60, args.generate_timeout)),
        "--step-a-retries",
        str(max(0, args.step_a_retries)),
        "--preview-publish",
        "--host",
        str(cdp_host),
        "--port",
        str(int(cdp_port)),
    ]
    if prepare_account:
        cmd.extend(["--account", prepare_account])
    if getattr(args, "allow_placeholder_preview", False):
        cmd.append("--allow-placeholder-preview")
    if getattr(args, "photoshop_after_generate", False):
        cmd.append("--photoshop-after-generate")
    if bool(getattr(args, "skip_visual_keyword_discover", False)):
        cmd.append("--skip-visual-keyword-discover")
    cmd.extend(
        [
            "--search-feed-timeout",
            str(max(45, int(getattr(args, "search_feed_timeout", 120)))),
            "--discover-max-probes",
            str(max(1, min(12, int(getattr(args, "discover_max_probes", 1))))),
            "--visual-step-timeout",
            str(max(0, int(getattr(args, "visual_step_timeout", 0)))),
        ]
    )
    if not bool(getattr(args, "reuse_last_generated_for_prepare", True)):
        cmd.append("--no-reuse-last-generated")
    if bool(getattr(args, "strict_step_lock", True)):
        cmd.append("--strict-step-lock")
    else:
        cmd.append("--no-strict-step-lock")
    if args.seed_keyword:
        cmd.extend(["--seed-keyword", args.seed_keyword])
    if args.brief_file:
        cmd.extend(["--brief-file", args.brief_file])
    elif args.brief:
        cmd.extend(["--brief", args.brief])
    if args.product_name:
        cmd.extend(["--product-name", args.product_name])
    if args.product_id:
        cmd.extend(["--product-id", args.product_id])

    acct = prepare_account or "-"
    print(
        "[bulk] Prepare once: generating assets + copy "
        f"(Chrome {cdp_host}:{cdp_port} account={acct})..."
    )
    proc = _run(cmd, REPO_ROOT, timeout_seconds=timeout_seconds)
    if proc.returncode != 0:
        _print_subprocess_capture(proc.stdout, sys.stdout)
        _print_subprocess_capture(proc.stderr, sys.stderr)
        raise SystemExit(f"Prepare step failed (exit={proc.returncode}).")

    summary_path = TMP_DIR / "last_picset_summary.json"
    title_path = TMP_DIR / "xhs_promo_out" / "title.txt"
    content_path = TMP_DIR / "xhs_promo_out" / "content.txt"

    if not summary_path.is_file() or not title_path.is_file() or not content_path.is_file():
        raise SystemExit("Prepare artifacts missing: summary/title/content not found.")

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    gen = [Path(p) for p in payload.get("generated_local_paths") or []]
    gen = dedupe_existing_paths_by_hash([str(p) for p in gen if p.is_file()])
    gen = validate_publish_images(payload, gen)
    if len(gen) < max(1, args.max_download):
        raise SystemExit(
            "Need at least "
            f"{args.max_download} valid generated images (non-reference), got {len(gen)}."
        )

    return title_path, content_path, gen[: max(1, args.max_download)]


def _warn_shared_cdp_port(
    accounts: list[dict], fallback_port: int, host: str
) -> bool:
    """Warn when multiple accounts map to the same local CDP port."""
    has_shared = False
    h = host.strip().lower()
    if h not in {"127.0.0.1", "localhost", "::1"}:
        return has_shared
    port_to_names: dict[int, list[str]] = {}
    for row in accounts:
        name = str(row.get("name") or "")
        if not name:
            continue
        p = int(row.get("port") or fallback_port)
        port_to_names.setdefault(p, []).append(name)
    for p, names in sorted(port_to_names.items()):
        if len(names) <= 1:
            continue
        has_shared = True
        print(
            f"[bulk] Warning: accounts {names} share CDP port {p} on {host}. "
            "Each bulk publish run restarts Chrome for the correct profile; "
            "do not run two publish_pipeline processes on that port at once.",
            file=sys.stderr,
        )
    return has_shared


def _publish_one(
    account: str,
    title_file: Path,
    content_file: Path,
    images: list[str],
    preview: bool,
    host: str,
    port: int,
    *,
    headless: bool = False,
    timing_jitter: float = 0.25,
    restart_browser_for_account: bool = False,
    timeout_seconds: int = 0,
    expected_nickname: str = "",
    context_key: str = "",
    product_name: str = "",
    product_id: str = "",
) -> subprocess.CompletedProcess:
    publish = SCRIPT_DIR / "publish_pipeline.py"
    cmd = [
        sys.executable,
        str(publish),
        "--host",
        host,
        "--port",
        str(port),
        "--reuse-existing-tab",
        "--timing-jitter",
        str(timing_jitter),
        "--title-file",
        str(title_file),
        "--content-file",
        str(content_file),
        "--images",
        *images,
    ]
    if account.strip():
        cmd.extend(["--account", account.strip()])
    if context_key.strip():
        cmd.extend(["--context-key", context_key.strip()])
    if product_name:
        cmd.extend(["--product-name", product_name])
        cmd.append("--click-add-product")
    if product_id:
        cmd.extend(["--product-id", product_id])
        cmd.append("--click-add-product")
    if headless:
        cmd.append("--headless")
    if preview:
        cmd.append("--preview")
    if restart_browser_for_account:
        cmd.append("--restart-browser-for-account")
    if expected_nickname.strip():
        cmd.extend(["--expected-nickname", expected_nickname.strip()])
    return _run(cmd, REPO_ROOT, timeout_seconds=timeout_seconds)


def _slice_groups(accounts: list[dict], group_size: int) -> list[list[dict]]:
    if group_size <= 0:
        return [accounts]
    return [accounts[i : i + group_size] for i in range(0, len(accounts), group_size)]


def _load_group_window_plan(plan_file: str | None) -> dict | None:
    if not plan_file:
        return None
    p = Path(plan_file)
    if not p.is_file():
        raise SystemExit(f"group window plan file not found: {plan_file}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"invalid group window plan json: {e}") from e

    interval = int(raw.get("interval_seconds") or 0)
    if interval <= 0:
        raise SystemExit("group window plan requires positive interval_seconds")
    groups = raw.get("groups") or {}
    if not isinstance(groups, dict):
        raise SystemExit("group window plan 'groups' must be an object")
    default_cfg = raw.get("default") or {}
    if not isinstance(default_cfg, dict):
        raise SystemExit("group window plan 'default' must be an object")
    return {
        "interval_seconds": interval,
        "groups": groups,
        "default": default_cfg,
    }


def _resolve_group_offset_seconds(group_name: str, plan: dict | None) -> int | None:
    if not plan:
        return None
    groups = plan.get("groups") or {}
    cfg = groups.get(group_name) if isinstance(groups, dict) else None
    if cfg is None:
        cfg = plan.get("default") or {}
    if not isinstance(cfg, dict):
        return None
    try:
        return int(cfg.get("offset_seconds") or 0)
    except Exception:
        return 0


def _wait_for_group_slot(
    group_name: str,
    plan: dict | None,
    *,
    no_wait_when_missed_slot: bool = False,
    slot_grace_seconds: float = 0.0,
) -> None:
    if not plan:
        return
    interval = int(plan.get("interval_seconds") or 0)
    if interval <= 0:
        return
    offset = int(_resolve_group_offset_seconds(group_name, plan) or 0)
    now = time.time()
    grace = max(0.0, float(slot_grace_seconds))
    effective_now = max(0.0, now - grace)
    # Align to recurring slots: base + k * interval, where base is offset.
    k = math.ceil((effective_now - offset) / interval)
    target = offset + k * interval
    wait_s = max(0.0, target - now)
    if no_wait_when_missed_slot and wait_s > 0:
        print(
            f"[bulk] group-window missed slot, continue now "
            f"group={group_name or '-'} offset={offset}s interval={interval}s"
        )
        return
    if wait_s > 0:
        print(
            f"[bulk] group-window wait group={group_name or '-'} "
            f"offset={offset}s interval={interval}s sleep={wait_s:.1f}s"
        )
        time.sleep(wait_s)


def _run_publish_with_retry(
    *,
    acc_row: dict,
    title_file: Path,
    content_file: Path,
    images: list[str],
    preview: bool,
    host: str,
    fallback_port: int,
    retries: int,
    sleep_min: float,
    sleep_max: float,
    headless: bool = False,
    timing_jitter: float = 0.25,
    restart_browser_for_account: bool = False,
    timeout_seconds: int = 0,
    use_browser_contexts: bool = False,
    context_browser_account: str = "",
    product_name: str = "",
    product_id: str = "",
) -> tuple[bool, int, subprocess.CompletedProcess | None]:
    acc = str(acc_row.get("name"))
    if use_browser_contexts:
        account_port = int(fallback_port)
    else:
        account_port = int(resolve_account_port(acc, fallback=fallback_port))
    publish_account = (context_browser_account.strip() if use_browser_contexts else acc)
    context_key = (f"xhs:{acc}" if use_browser_contexts else "")
    attempt = 0
    last_proc: subprocess.CompletedProcess | None = None
    while attempt <= max(0, retries):
        attempt += 1
        proc = _publish_one(
            account=publish_account,
            title_file=title_file,
            content_file=content_file,
            images=images,
            preview=preview,
            host=host,
            port=account_port,
            headless=headless,
            timing_jitter=timing_jitter,
            restart_browser_for_account=restart_browser_for_account,
            timeout_seconds=timeout_seconds,
            expected_nickname=str(acc_row.get("expected_nickname") or ""),
            context_key=context_key,
            product_name=product_name,
            product_id=product_id,
        )
        last_proc = proc
        if proc.returncode == 0:
            return True, attempt, last_proc
        print(f"[bulk] account={acc} failed attempt={attempt} exit={proc.returncode}", file=sys.stderr)
        _sleep_jitter(sleep_min, sleep_max)
    return False, attempt, last_proc


def main() -> None:
    port_explicit = "--port" in sys.argv
    account_explicit = "--prepare-account" in sys.argv

    parser = argparse.ArgumentParser(description="Bulk publish to many XHS accounts")
    parser.add_argument("--product-images", nargs="+", required=False)
    parser.add_argument("--seed-keyword", default=None)
    parser.add_argument("--brief", default=None)
    parser.add_argument("--brief-file", default=None)
    parser.add_argument("--product-name", default="", help="商品名称，用于发布步骤的商品匹配")
    parser.add_argument("--product-id", default="", help="商品 ID，精确匹配")

    parser.add_argument("--accounts", nargs="*", default=None, help="Account names. Omit to use all.")
    parser.add_argument(
        "--only-groups",
        nargs="*",
        default=None,
        help="Only publish accounts whose group is in this set, e.g. A B.",
    )
    parser.add_argument("--max-accounts", type=int, default=0, help="Limit number of accounts; 0 means no limit.")
    parser.add_argument(
        "--group-size",
        type=int,
        default=0,
        help="Batch size per window; 0 means all in one window.",
    )
    parser.add_argument(
        "--round-robin",
        action="store_true",
        help="Force one-account-per-window rotation (equivalent to --group-size 1).",
    )
    parser.add_argument(
        "--group-window-seconds",
        type=float,
        default=0.0,
        help="Wait seconds between windows when --group-size > 0.",
    )
    parser.add_argument(
        "--group-window-plan-file",
        default=None,
        help="JSON file for per-group recurring time windows.",
    )
    parser.add_argument(
        "--no-wait-when-missed-slot",
        action="store_true",
        help="If set, skip waiting for next slot when current group slot is already missed.",
    )
    parser.add_argument(
        "--slot-grace-seconds",
        type=float,
        default=0.0,
        help="Grace seconds for near-miss slot timing before considered missed.",
    )

    parser.add_argument("--reference-count", type=int, default=4)
    parser.add_argument("--max-download", type=int, default=1)
    parser.add_argument("--picset-batch-size", type=int, default=1)
    parser.add_argument("--generate-timeout", type=int, default=1200)
    parser.add_argument(
        "--search-feed-timeout",
        type=int,
        default=120,
        help="转发 full_stack→visual：单次 search-feeds CDP 上限秒数（宜 90~180）",
    )
    parser.add_argument(
        "--discover-max-probes",
        type=int,
        default=1,
        help="转发 full_stack→visual：recommended_best 时再探测几条候选词（越小越快）",
    )
    parser.add_argument(
        "--visual-step-timeout",
        type=int,
        default=0,
        help="转发 full_stack：Step A 整段超时；0=full_stack 按搜词+生图预算自动推算",
    )
    parser.add_argument("--step-a-retries", type=int, default=0)
    parser.add_argument(
        "--allow-placeholder-preview",
        action="store_true",
        help="转发 prepare：无 ARK_API_KEY/ARK_MODEL 时用占位标题/正文（full_stack --allow-placeholder-preview）",
    )
    parser.add_argument(
        "--photoshop-after-generate",
        action="store_true",
        help="转发 prepare full_stack → visual：Picset 生成后对图做 Photoshop 自动色调等再进豆包/发布（需 PS+pywin32）",
    )
    parser.add_argument(
        "--skip-visual-keyword-discover",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "转发 full_stack/visual：跳过 Step1 discover 的多轮 search-feeds，"
            "仅用种子关键词走 xhs_images 内一次封面搜索。"
        ),
    )
    parser.add_argument(
        "--reuse-last-generated-for-prepare",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "转发 prepare full_stack：是否复用 tmp/last_picset_summary.json 中仍存在的生成图（默认开启）。"
            "整链重跑小红书搜图+Picset 请加 --no-reuse-last-generated-for-prepare。"
        ),
    )

    parser.add_argument("--skip-prepare", action="store_true", help="Reuse existing tmp artifacts.")
    parser.add_argument("--title-file", default=None)
    parser.add_argument("--content-file", default=None)
    parser.add_argument("--images", nargs="*", default=None)

    parser.add_argument("--preview", action="store_true", help="Preview mode for all accounts.")
    parser.add_argument(
        "--publish-headless",
        action="store_true",
        help="Pass --headless to publish_pipeline for each account (Chrome without a window).",
    )
    parser.add_argument(
        "--timing-jitter",
        type=float,
        default=0.25,
        help="Forwarded to publish_pipeline --timing-jitter (default 0.25).",
    )
    parser.add_argument(
        "--prepare-account",
        default=None,
        help=(
            "Account name whose CDP port/profile is used for Prepare (Picset + full_stack Step C). "
            "Default: first account in the filtered run list."
        ),
    )
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument(
        "--retry-failed-pass",
        type=int,
        default=0,
        help="Extra compensation passes for failed accounts after main run.",
    )
    parser.add_argument("--sleep-min", type=float, default=6.0)
    parser.add_argument("--sleep-max", type=float, default=16.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument(
        "--use-browser-contexts",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Use one Chrome process/port with isolated BrowserContext per account.",
    )
    parser.add_argument(
        "--context-browser-account",
        default=None,
        help="When --use-browser-contexts, this account profile launches Chrome (default: prepare-account or default).",
    )
    parser.add_argument(
        "--step-timeout-seconds",
        type=int,
        default=5400,
        help=(
            "Prepare / 单账号发布的子进程超时（秒）。_prepare 内含搜图+Picset+可选 PS；"
            "过小会误杀长尾步骤。默认 5400。"
            "设为 0 关闭（不推荐）。"
        ),
    )
    parser.add_argument(
        "--max-runtime-seconds",
        type=int,
        default=0,
        help=(
            "Hard stop for entire bulk run. "
            "When reached, no new steps start. 0 disables."
        ),
    )
    parser.add_argument(
        "--restart-browser-for-account",
        action="store_true",
        default=False,
        help=(
            "Publish stage only (local CDP): restart Chrome for each account profile. "
            "Default OFF to keep the browser windows alive."
        ),
    )
    parser.add_argument(
        "--strict-step-lock",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Hard gate mode (default OFF): if enabled, stop after first failed account. "
            "By default, continue publishing next accounts when one fails."
        ),
    )
    parser.add_argument(
        "--continue-on-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Continue to next account immediately after a failed account "
            "(default ON)."
        ),
    )

    args = parser.parse_args()
    prepare_account_seed = (args.prepare_account or "").strip() or None
    _, resolved_port, runtime_meta = resolve_runtime_target(
        host=args.host,
        port=int(args.port),
        account=prepare_account_seed,
        port_explicit=port_explicit,
        account_explicit=account_explicit,
    )
    args.port = int(resolved_port)

    logger = create_run_logger(
        "bulk_publish_accounts",
        host=args.host,
        port=int(args.port),
        prepare_account=(prepare_account_seed or ""),
        preview=bool(args.preview),
    )
    logger.info(
        "bulk_start",
        data={
            "runtime_meta": runtime_meta,
            "skip_prepare": bool(args.skip_prepare),
            "retries": int(args.retries),
        },
    )

    if (not args.skip_prepare) and (not args.product_images):
        parser.error("--product-images is required unless --skip-prepare is used.")
    if bool(args.round_robin) and int(args.group_size) <= 0:
        args.group_size = 1
    group_window_plan = _load_group_window_plan(args.group_window_plan_file)
    started_ts = time.time()

    accounts = _load_accounts(args.accounts)
    if args.only_groups:
        allowed = {g.strip() for g in args.only_groups if g and g.strip()}
        accounts = [a for a in accounts if (a.get("group") or "").strip() in allowed]
    if args.max_accounts > 0:
        accounts = accounts[: args.max_accounts]
    if not accounts:
        raise SystemExit("No accounts to publish.")

    has_shared_port = _warn_shared_cdp_port(accounts, args.port, args.host)
    effective_restart_browser_for_account = bool(args.restart_browser_for_account) or bool(
        has_shared_port
    )
    if has_shared_port and not bool(args.restart_browser_for_account):
        print(
            "[bulk] Auto-enabled --restart-browser-for-account because accounts share local CDP port.",
            flush=True,
        )

    prepare_cdp: dict | None = None
    if args.skip_prepare:
        if not (args.title_file and args.content_file and args.images):
            raise SystemExit("With --skip-prepare, pass --title-file --content-file --images...")
        title_file = Path(args.title_file)
        content_file = Path(args.content_file)
        images = dedupe_existing_paths_by_hash([str(Path(p)) for p in args.images if Path(p).is_file()])
        if not title_file.is_file() or not content_file.is_file() or not images:
            raise SystemExit("Invalid prepare artifacts for --skip-prepare.")
    else:
        prep_name = (args.prepare_account or "").strip()
        if prep_name:
            prepare_row = next((a for a in accounts if str(a.get("name") or "") == prep_name), None)
            if prepare_row is None:
                raise SystemExit(
                    f"--prepare-account {prep_name!r} is not among the accounts selected for this run."
                )
        else:
            prepare_row = accounts[0]
        anchor_port = int(prepare_row.get("port") or args.port)
        anchor_acc = str(prepare_row.get("name") or "").strip() or None
        prepare_cdp = {
            "host": args.host,
            "port": anchor_port,
            "account": anchor_acc or "",
        }
        print(
            f"[bulk] Prepare anchors to CDP {args.host}:{anchor_port}"
            + (f" (--account={anchor_acc})" if anchor_acc else "")
            + "; full_stack Step C uses --preview-publish (no Publish click).\n"
            "[bulk] After Prepare, each account phase "
            + (
                "will NOT click Publish (--preview)." if args.preview else "will click Publish (omit --preview)."
            ),
            flush=True,
        )
        title_file, content_file, images = _prepare_once(
            args,
            cdp_host=args.host,
            cdp_port=anchor_port,
            prepare_account=anchor_acc,
            timeout_seconds=int(args.step_timeout_seconds),
        )

    report = {
        "started_at": datetime.now().isoformat(),
        "preview": bool(args.preview),
        "prepare_cdp": prepare_cdp,
        "publish_headless": bool(args.publish_headless),
        "timing_jitter": float(args.timing_jitter),
        "accounts_total": len(accounts),
        "only_groups": [g for g in (args.only_groups or []) if g],
        "group_window_plan_file": args.group_window_plan_file or "",
        "no_wait_when_missed_slot": bool(args.no_wait_when_missed_slot),
        "slot_grace_seconds": float(args.slot_grace_seconds),
        "success": [],
        "failed": [],
        "compensation_success": [],
        "compensation_failed": [],
        "title_file": str(title_file),
        "content_file": str(content_file),
        "images": images,
    }

    windows = _slice_groups(accounts, args.group_size)
    done_count = 0
    stop_due_to_failure = False
    for widx, window in enumerate(windows, start=1):
        print(f"[bulk] window {widx}/{len(windows)} start, size={len(window)}")
        for acc_row in window:
            if args.max_runtime_seconds > 0:
                elapsed = time.time() - started_ts
                if elapsed >= max(1, int(args.max_runtime_seconds)):
                    raise SystemExit(
                        f"Bulk run reached --max-runtime-seconds={int(args.max_runtime_seconds)}; stop."
                    )
            done_count += 1
            acc = str(acc_row.get("name"))
            acc_group = (acc_row.get("group") or "").strip()
            account_port = int(acc_row.get("port") or args.port)
            _wait_for_group_slot(
                acc_group,
                group_window_plan,
                no_wait_when_missed_slot=bool(args.no_wait_when_missed_slot),
                slot_grace_seconds=float(args.slot_grace_seconds),
            )
            print(
                f"[bulk] ({done_count}/{len(accounts)}) publishing account={acc} "
                f"group={acc_group or '-'} port={account_port} "
                f"proxy={'yes' if acc_row.get('proxy') else 'no'}"
            )
            ok, attempt, last_proc = _run_publish_with_retry(
                acc_row=acc_row,
                title_file=title_file,
                content_file=content_file,
                images=images,
                preview=args.preview,
                host=args.host,
                fallback_port=args.port,
                retries=args.retries,
                sleep_min=args.sleep_min,
                sleep_max=args.sleep_max,
                headless=args.publish_headless,
                timing_jitter=float(args.timing_jitter),
                restart_browser_for_account=effective_restart_browser_for_account,
                timeout_seconds=int(args.step_timeout_seconds),
                use_browser_contexts=bool(args.use_browser_contexts),
                context_browser_account=(
                    str(args.context_browser_account or args.prepare_account or "default")
                ),
                product_name=str(args.product_name or ""),
                product_id=str(args.product_id or ""),
            )
            if ok:
                report["success"].append({"account": acc, "attempt": attempt, "window": widx})
            else:
                report["failed"].append(
                    {
                        "account": acc,
                        "window": widx,
                        "exit_code": None if last_proc is None else last_proc.returncode,
                        "stdout_tail": (last_proc.stdout[-1200:] if last_proc and last_proc.stdout else ""),
                        "stderr_tail": (last_proc.stderr[-1200:] if last_proc and last_proc.stderr else ""),
                    }
                )
                if (not args.continue_on_failure) and args.strict_step_lock:
                    print(
                        f"[bulk] strict-step-lock: stop after failed account={acc}.",
                        file=sys.stderr,
                    )
                    stop_due_to_failure = True
                    break

            if done_count < len(accounts):
                _sleep_jitter(args.sleep_min, args.sleep_max)
        if stop_due_to_failure:
            break

        if widx < len(windows) and args.group_window_seconds > 0:
            print(f"[bulk] window {widx} done, sleeping {args.group_window_seconds}s before next window")
            time.sleep(max(0.0, args.group_window_seconds))

    # Compensation passes: retry failed accounts in additional rounds.
    extra_passes = max(0, int(args.retry_failed_pass))
    if (not args.continue_on_failure) and args.strict_step_lock and report["failed"]:
        extra_passes = 0
    for pass_idx in range(1, extra_passes + 1):
        pending_names = [x.get("account") for x in report["failed"] if x.get("account")]
        if not pending_names:
            break
        pending_rows = [a for a in accounts if a.get("name") in set(pending_names)]
        print(f"[bulk] compensation pass {pass_idx}/{extra_passes}, pending={len(pending_rows)}")
        still_failed: list[dict] = []
        for acc_row in pending_rows:
            if args.max_runtime_seconds > 0:
                elapsed = time.time() - started_ts
                if elapsed >= max(1, int(args.max_runtime_seconds)):
                    raise SystemExit(
                        f"Bulk run reached --max-runtime-seconds={int(args.max_runtime_seconds)}; stop."
                    )
            acc = str(acc_row.get("name"))
            ok, attempt, last_proc = _run_publish_with_retry(
                acc_row=acc_row,
                title_file=title_file,
                content_file=content_file,
                images=images,
                preview=args.preview,
                host=args.host,
                fallback_port=args.port,
                retries=args.retries,
                sleep_min=args.sleep_min,
                sleep_max=args.sleep_max,
                headless=args.publish_headless,
                timing_jitter=float(args.timing_jitter),
                restart_browser_for_account=effective_restart_browser_for_account,
                timeout_seconds=int(args.step_timeout_seconds),
                use_browser_contexts=bool(args.use_browser_contexts),
                context_browser_account=(
                    str(args.context_browser_account or args.prepare_account or "default")
                ),
            )
            if ok:
                report["compensation_success"].append(
                    {"account": acc, "attempt": attempt, "compensation_pass": pass_idx}
                )
            else:
                still_failed.append(
                    {
                        "account": acc,
                        "compensation_pass": pass_idx,
                        "exit_code": None if last_proc is None else last_proc.returncode,
                        "stdout_tail": (last_proc.stdout[-1200:] if last_proc and last_proc.stdout else ""),
                        "stderr_tail": (last_proc.stderr[-1200:] if last_proc and last_proc.stderr else ""),
                    }
                )
        report["compensation_failed"] = still_failed
        report["failed"] = still_failed

    report["ended_at"] = datetime.now().isoformat()
    report["ok_count"] = len(report["success"])
    report["compensation_ok_count"] = len(report["compensation_success"])
    report["fail_count"] = len(report["failed"])

    report_path = TMP_DIR / f"bulk_publish_report_{_now_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bulk] report: {report_path}")
    print(f"[bulk] done. ok={report['ok_count']} fail={report['fail_count']}")
    logger.info(
        "bulk_done",
        data={
            "report_path": str(report_path),
            "ok_count": int(report["ok_count"]),
            "fail_count": int(report["fail_count"]),
            "compensation_ok_count": int(report["compensation_ok_count"]),
        },
    )

    if report["fail_count"] > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()

