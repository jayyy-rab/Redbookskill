#!/usr/bin/env python3
"""
Minimal-input orchestrator for the SKILL.md full stack + bulk multi-account flow.

Goal: say one paragraph (speech) + point at product image(s); this script derives
--seed-keyword, writes a UTF-8 brief file, then runs bulk_publish_accounts.py.

Speech → seed keyword heuristic (best-effort, no ML):
  • Explicit markers: 「关键词」「主题」「小红书词」「搜索词」「赛道」「：」后为词
  • First #话题 形式 (#茶叶)
  • Otherwise substring match against内置常见类目词 (茶叶、咖啡 …)
  • Fallback: 好物

Product images (--product-images OR env REDBOOK_ZERO_TOUCH_PRODUCT_IMAGE, comma-separated):
  REQUIRED unless dry-run prints help.

Examples (PowerShell, repo root):
  python scripts/zero_touch_xhs.py --speech "关键词茶叶，多渠道铺量，全自动" ^
    --product-images "D:\\tea.png"

  echo 茶叶种草全链路多发 | python scripts/zero_touch_xhs.py --product-images "D:\\tea.png"

  $env:REDBOOK_ZERO_TOUCH_PRODUCT_IMAGE = "D:\\tea.png"; `
  python scripts/zero_touch_xhs.py --speech "主题：茶叶，按账户全发"

  # Picset 成图后走 Photoshop JSX（等价「图像→自动色调/对比度/颜色」）再豆包发布；需 PS + pywin32
  python scripts/zero_touch_xhs.py --speech "关键词茶叶" --product-images D:\\tea.png ^
    --photoshop-after-generate

Forward extra bulk_publish_accounts.py flags after `--`:

  python scripts/zero_touch_xhs.py --product-images pic.png --speech "关键词茶叶" ^
    -- --preview --retry-failed-pass 1
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from env_local_loader import load_env_local  # noqa: E402

load_env_local(REPO_ROOT)


# Substrings searched in speech if no explicit pattern matches (prefer longer first).
_HINT_TERMS_ORDERED: tuple[tuple[str, str], ...] = (
    ("龙井绿茶", "龙井绿茶"),
    ("乌龙茶", "乌龙茶"),
    ("铁观音", "铁观音"),
    ("白茶", "白茶"),
    ("黑茶", "黑茶"),
    ("普洱茶", "普洱茶"),
    ("古树普洱", "普洱茶"),
    ("红茶", "红茶"),
    ("绿茶", "绿茶"),
    ("茶叶", "茶叶"),
    ("咖啡豆", "咖啡"),
    ("咖啡", "咖啡"),
    ("礼盒", "礼盒"),
    ("月饼", "月饼"),
)


def extract_seed_keyword(speech: str) -> str:
    """Infer Picset/XHS seed keyword string from user's one paragraph."""
    t = speech.strip()
    if not t:
        return "好物"

    # Explicit: 关键词：茶叶 / 主题 为「茶叶」
    explicit = re.search(
        r"(?:关键词|主题|小红书|搜索词|赛道|种草词)"
        r"\s*[：:是为]+\s*[「『\"'（(]?\s*([^#\n\r\t，。,.;]{1,32}?)\s*"
        r"[」』\"')）]?",
        t,
        flags=re.MULTILINE,
    )
    if explicit:
        w = explicit.group(1).strip().strip("「」『』\"' ")
        if len(w) >= 1:
            return w[:32]

    hashtag = re.search(
        r"(?:^|\s)#\s*([^\s#]{2,24})",
        t,
        flags=re.UNICODE,
    )
    if hashtag:
        return hashtag.group(1).strip()[:32]

    # Longest hinted category substring wins (茶叶 before 绿茶 if both?)
    hit: str | None = None
    hit_len = 0
    for needle, canon in _HINT_TERMS_ORDERED:
        if needle in t and len(needle) >= hit_len:
            hit_len = len(needle)
            hit = canon

    return hit if hit else "好物"


def _resolve_product_images(cli_paths: list[str] | None) -> list[str]:
    if cli_paths:
        return [str(Path(p).resolve()) for p in cli_paths if Path(p).is_file()]
    raw = (
        os.environ.get("REDBOOK_ZERO_TOUCH_PRODUCT_IMAGE", "").strip()
        or os.environ.get("REDBOOK_AUTORUN_PRODUCT_IMAGE", "").strip()
    )
    if not raw:
        return []
    # Comma-separated for env (Windows paths rarely use comma)
    paths = [x.strip().strip('"') for x in raw.split(",") if x.strip()]
    out: list[str] = []
    for p in paths:
        if Path(p).is_file():
            out.append(str(Path(p).resolve()))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="一句话启动：小红书全自动（bulk_publish_accounts → full_stack ×1 + 各账号 publish）",
    )
    ap.add_argument(
        "--speech",
        default=None,
        help="你给助手的一段话（必选其一：--speech / --speech-file / 标准输入）",
    )
    ap.add_argument(
        "--speech-file",
        default=None,
        help="同上，内容为 UTF-8 文件路径",
    )
    ap.add_argument(
        "--product-images",
        nargs="+",
        default=None,
        help="产品素材图路径（可多图）；不写则用环境 REDBOOK_ZERO_TOUCH_PRODUCT_IMAGE（逗号分隔）",
    )
    ap.add_argument(
        "--seed-keyword",
        default=None,
        help="跳过自动推断：直接指定小红书/Picset 关键词",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将执行的命令与各参数，不写文件、不调 bulk",
    )
    ap.add_argument(
        "--print-plan",
        action="store_true",
        help="与 --dry-run 类似但同时打印推导出的 keyword 与 brief 正文",
    )
    ap.add_argument(
        "--photoshop-after-generate",
        action="store_true",
        dest="photoshop_after_generate",
        help="传给 bulk/full_stack Step A：生成图后对「after_photoshop_autotcc」再走豆包与发布（需 PS+pywin32）",
    )
    args, bulk_extra = ap.parse_known_args()

    speech = (args.speech or "").strip()
    if args.speech_file:
        sp = Path(args.speech_file).expanduser()
        if not sp.is_file():
            raise SystemExit(f"--speech-file 不存在：{sp}")
        speech = sp.read_text(encoding="utf-8").strip()

    if not speech:
        if sys.stdin.isatty():
            print(
                "请输入一段话（可多行）；结束输入：Unix Ctrl+D，PowerShell Ctrl+Z Enter：",
                file=sys.stderr,
            )
        stdin_text = sys.stdin.read()
        speech = (stdin_text or "").strip()

    if not speech:
        raise SystemExit("请提供 --speech、--speech-file 或 stdin 里的一段话。")

    seed = (args.seed_keyword or "").strip() or extract_seed_keyword(speech)

    images = _resolve_product_images(list(args.product_images) if args.product_images else None)
    if not images:
        print(
            "Error: 需要提供产品素材图：`--product-images 路径`，"
            "或设置环境变量 REDBOOK_ZERO_TOUCH_PRODUCT_IMAGE=绝对路径(,多个用英文逗号)。",
            file=sys.stderr,
        )
        sys.exit(2)

    TMP = REPO_ROOT / "tmp"
    TMP.mkdir(parents=True, exist_ok=True)
    brief_path = TMP / "zero_touch_brief.txt"
    brief_path.write_text(speech + "\n", encoding="utf-8")

    bulk_py = SCRIPT_DIR / "bulk_publish_accounts.py"
    cmd: list[str] = [
        sys.executable,
        str(bulk_py),
        "--product-images",
        *images,
        "--seed-keyword",
        seed,
        "--brief-file",
        str(brief_path),
    ]
    if getattr(args, "photoshop_after_generate", False):
        cmd.append("--photoshop-after-generate")
    cmd.extend(bulk_extra)

    print(f"[zero-touch] 推断关键词 seed-keyword：「{seed}」", flush=True)
    print(f"[zero-touch] 产品素材：{' | '.join(images)}", flush=True)
    print(f"[zero-touch] 说明已写入：{brief_path}", flush=True)
    print(f"[zero-touch] 启动：{' '.join(cmd)}", flush=True)

    if args.dry_run or args.print_plan:
        if args.print_plan:
            print("--- speech ---\n" + speech + "\n--------------", flush=True)
        if args.dry_run:
            sys.exit(0)

    proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
