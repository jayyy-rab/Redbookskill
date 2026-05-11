"""
小红书「热点参考 → 下载封面 → Picset（参考槽+产品槽）→ 生成 → 可选发图文」编排入口。

不负责图像语义匹配（需你与产品赛道一致的关键词）；热点词来自 search-feeds 的下拉推荐。

用法示例：

  python scripts/visual_publish_pipeline.py \\
    --product-images "D:\\tea_product.png" \\
    --seed-keyword "绿茶" \\
    --keyword-strategy recommended_first \\
    --publish-to-xhs \\
    --title "新品春茶" \\
    --content "详见图2\\n#绿茶 #春茶"

  # 只做图不发笔记：不要加 --publish-to-xhs，也不要传 --title/--content
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from pipeline_debug import pipeline_debug_log  # noqa: E402


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    pipeline_debug_log(
        hypothesis_id,
        location,
        message,
        data,
        run_id=f"visual_pipeline_{int(time.time() * 1000)}",
    )
    # endregion


def _cdp_argv_globals(
    host: str,
    port: int,
    account: str | None,
    reuse_existing_tab: bool,
) -> list[str]:
    """Prefix for cdp_publish.py: global flags before subcommand."""
    argv = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "cdp_publish.py"),
        "--host",
        str(host).strip(),
        "--port",
        str(int(port)),
    ]
    if account:
        argv.extend(["--account", str(account)])
    if reuse_existing_tab:
        argv.append("--reuse-existing-tab")
    return argv


def _parse_search_feeds_payload(stdout: str) -> dict[str, object]:
    marker = "SEARCH_FEEDS_RESULT:\n"
    if marker not in stdout:
        return {}
    try:
        return json.loads(stdout.split(marker, 1)[1])
    except json.JSONDecodeError:
        return {}


def _to_int_like(v: object) -> int:
    s = str(v or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    return int(digits) if digits else 0


def _score_search_payload(payload: dict[str, object]) -> tuple[int, int]:
    feeds = payload.get("feeds") if isinstance(payload, dict) else None
    if not isinstance(feeds, list):
        return (0, 0)
    likes_sum = 0
    valid = 0
    for feed in feeds[:12]:
        if not isinstance(feed, dict):
            continue
        note = feed.get("noteCard")
        if not isinstance(note, dict):
            continue
        inter = note.get("interactInfo")
        if not isinstance(inter, dict):
            continue
        likes_sum += _to_int_like(inter.get("likedCount"))
        valid += 1
    return (likes_sum, valid)


def discover_keyword(
    seed_keyword: str,
    sort_by: str,
    strategy: str,
    publish_time: str,
    note_type: str,
    *,
    host: str = "127.0.0.1",
    port: int = 9222,
    account: str | None = None,
    reuse_existing_tab: bool = True,
    search_feed_timeout: int = 120,
    discover_max_probes: int = 4,
) -> tuple[str, list[str]]:
    """
    Returns (keyword_for_search, recommended_keywords_for_info).
    """
    cmd = _cdp_argv_globals(host, port, account, reuse_existing_tab) + [
        "search-feeds",
        "--keyword",
        seed_keyword.strip(),
        "--sort-by",
        sort_by,
        "--publish-time",
        publish_time,
        "--note-type",
        note_type,
    ]
    to = max(30, min(300, int(search_feed_timeout)))
    try:
        proc = subprocess.run(
            cmd,
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=to,
        )
    except subprocess.TimeoutExpired:
        print(
            f"[visual-pipeline] Step1: search-feeds 超时（>{to}s），退回种子词「{seed_keyword.strip()}」",
            file=sys.stderr,
            flush=True,
        )
        return seed_keyword.strip(), []
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    payload = _parse_search_feeds_payload(out)
    recommended = list(payload.get("recommended_keywords") or [])
    if not isinstance(recommended, list):
        recommended = []

    if strategy == "recommended_first" and recommended:
        chosen = str(recommended[0]).strip()
        if chosen:
            print(f"[visual-pipeline] Step1: 使用搜索下拉推荐词 → 「{chosen}」")
            return chosen, recommended
    probe_cap = max(2, min(12, int(discover_max_probes)))
    if strategy == "recommended_best" and recommended:
        # First search-feeds (above) already downloaded seed_keyword → reuse payload for seed score
        candidates = [seed_keyword.strip()] + [str(x).strip() for x in recommended if str(x).strip()]
        best_kw = seed_keyword.strip()
        best_score = _score_search_payload(payload)
        others = candidates[1:probe_cap]
        extra_total = len(others)
        for idx, kw in enumerate(others, start=1):
            print(
                f"[visual-pipeline] Step1b: discover extra probe {idx}/{extra_total} "
                f"(cap={probe_cap}) keyword={kw!r} timeout={to}s …",
                flush=True,
            )
            scmd = _cdp_argv_globals(host, port, account, reuse_existing_tab) + [
                "search-feeds",
                "--keyword",
                kw,
                "--sort-by",
                sort_by,
                "--publish-time",
                publish_time,
                "--note-type",
                note_type,
            ]
            try:
                sproc = subprocess.run(
                    scmd,
                    cwd=SCRIPT_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=to,
                )
            except subprocess.TimeoutExpired:
                print(
                    f"[visual-pipeline] Step1b: probe 超时 keyword={kw!r}，跳过该候选",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            sout = (sproc.stdout or "") + "\n" + (sproc.stderr or "")
            spayload = _parse_search_feeds_payload(sout)
            score = _score_search_payload(spayload)
            if score > best_score:
                best_score = score
                best_kw = kw
        print(
            f"[visual-pipeline] Step1: 推荐词多候选评估完成，选择「{best_kw}」"
            f"（点赞总量={best_score[0]}，有效样本={best_score[1]}）"
        )
        return best_kw, recommended

    print(f"[visual-pipeline] Step1: 使用种子关键词 → 「{seed_keyword.strip()}」")
    return seed_keyword.strip(), recommended


def main() -> None:
    # region agent log
    pipeline_debug_log(
        "H0",
        "visual_publish_pipeline.py:main",
        "entry",
        {
            "cwd": str(Path.cwd()),
            "scripts_parent": str(Path(SCRIPT_DIR).parent),
        },
    )
    # endregion
    parser = argparse.ArgumentParser(
        description="热点/赛道词 → 下载参考封面 → Picset 参考+产品 → 生成 → 可选发布图文",
    )
    parser.add_argument(
        "--product-images",
        nargs="+",
        required=True,
        metavar="PATH",
        help="产品素材（必填），上传到 Picset「产品素材图」",
    )
    parser.add_argument(
        "--seed-keyword",
        default="茶叶",
        help="种子关键词，用于拉搜索推荐词与首屏封面（默认：茶叶）",
    )
    parser.add_argument(
        "--keyword-strategy",
        choices=("seed", "recommended_first", "recommended_best"),
        default="recommended_best",
        help=(
            "seed=始终用种子词搜索；recommended_first=若存在下拉推荐词则用第一条 "
            "（更贴「热点」，但与产品是否匹配需你自选 seed）；"
            "recommended_best=评估多条推荐词后选更适合电商的候选（默认）"
        ),
    )
    parser.add_argument(
        "--skip-keyword-discover",
        action="store_true",
        default=False,
        help=(
            "跳过 Step1 的 discover_keyword（不再跑额外的 search-feeds 子进程与多 probe）；"
            "直接用 --seed-keyword 作为传给 xhs_images 的检索词。"
            "xhs_images 内仍会做一次封面搜索。"
        ),
    )
    parser.add_argument(
        "--sort-by",
        default="最多点赞",
        help="搜索排序：综合 / 最新 / 最多点赞 / 最多评论 / 最多收藏",
    )
    parser.add_argument(
        "--publish-time",
        default="一周内",
        choices=("不限", "一天内", "一周内", "半年内"),
        help="发布时间筛选（默认：一周内）",
    )
    parser.add_argument(
        "--limit-notes",
        type=int,
        default=24,
        help="参与参考的笔记数量上限（封面张数上限）",
    )
    parser.add_argument(
        "--max-reference-covers",
        type=int,
        default=12,
        help="下载并上传为参考图的张数；设为 1 即「只选一张封面」做参考",
    )
    parser.add_argument(
        "--no-generate",
        action="store_true",
        help="仅热词+下载由本脚本处理；不调用生图（未实现，请用 xhs_images_to_picset --skip-upload）",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Picset 生成提示词；不配则用内置电商茶叶向默认句",
    )
    parser.add_argument("--prompt-file", default=None, help="提示词 UTF-8 文件")
    parser.add_argument("--generate-timeout", type=int, default=300)
    parser.add_argument(
        "--search-feed-timeout",
        type=int,
        default=120,
        metavar="SEC",
        help="单次小红书 search-feeds 子进程超时（秒）；默认 120，过大易出现「卡住」体感",
    )
    parser.add_argument(
        "--discover-max-probes",
        type=int,
        default=4,
        metavar="N",
        help="keyword-strategy=recommended_best 时除种子 payload 外最多再搜几条候选（总 search≈1+本值）",
    )
    parser.add_argument(
        "--bridge-timeout",
        type=int,
        default=0,
        metavar="SEC",
        help="xhs_images_to_picset 子进程硬超时；0 = login + generate-timeout 自动推算",
    )
    parser.add_argument(
        "--max-download",
        type=int,
        default=1,
        help="Picset 生成图最多下载张数（传给 xhs_images_to_picset --max-download）",
    )
    parser.add_argument(
        "--picset-batch-size",
        type=int,
        default=1,
        metavar="N",
        help="传给 xhs_images_to_picset：画图界面尽量切成「N 张」（默认同 --max-download）",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument(
        "--strict-step-lock",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Hard gate mode (default ON): step failure blocks downstream steps.",
    )
    parser.add_argument("--account", default=None)
    parser.add_argument(
        "--reuse-existing-tab",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="小红书 search-feeds（Step1）：复用当前标签页，减少新开搜索结果页（默认开启）。",
    )
    parser.add_argument(
        "--prefer-ecommerce-covers",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "搜参考封面：优先更像电商实拍图的封面，弱化大字报/纯文字图（传给 xhs_images_to_picset，默认开）。"
        ),
    )
    parser.add_argument("--picset-url", default="https://picsetai.com/zh-CN")
    parser.add_argument("--login-timeout", type=int, default=180)
    parser.add_argument("--publish-to-xhs", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--title", default=None)
    parser.add_argument("--title-file", default=None)
    parser.add_argument("--content", default=None)
    parser.add_argument("--content-file", default=None)
    parser.add_argument(
        "--save-topic-json",
        default=None,
        help="可选：把 Step1 推荐词列表写入该 UTF-8 JSON 路径",
    )
    parser.add_argument(
        "--summary-json",
        default=None,
        help="传给 xhs_images_to_picset：把本轮参考/生成路径等写入 JSON，便于编排发布",
    )
    parser.add_argument(
        "--watermark-post-workflow",
        action="store_true",
        help="传给 xhs_images_to_picset：下载小红书参考图后写入去水印/PS 批处理说明并可打开无痕清印",
    )
    parser.add_argument(
        "--watermark-tool-url",
        default="https://wuhenqingyin.com.cn/#",
        help="去水印工具 URL（默认无痕清印）",
    )
    parser.add_argument(
        "--no-open-watermark-url",
        action="store_true",
        help="与 --watermark-post-workflow 同用：不自动打开去水印网页",
    )
    parser.add_argument(
        "--watermark-full-auto",
        action="store_true",
        help="传给 xhs_images_to_picset：全自动本地去水印区修复 + Pillow + 可选 Photoshop",
    )
    parser.add_argument(
        "--watermark-no-inpaint",
        action="store_true",
        help="同上：跳过 OpenCV inpaint（仅复制原图进 01 再优化）",
    )
    parser.add_argument("--watermark-corner-w", type=float, default=0.36)
    parser.add_argument("--watermark-corner-h", type=float, default=0.14)
    parser.add_argument(
        "--watermark-photoshop",
        action="store_true",
        help="同上：尝试 Photoshop JSX 批处理（需 PS + pywin32）",
    )
    parser.add_argument(
        "--photoshop-after-generate",
        action="store_true",
        help="传给 xhs_images_to_picset：Picset 生成图落盘后再做 PS 自动色调/对比度/颜色",
    )
    parser.add_argument(
        "--manual-draw-before-photoshop",
        action="store_true",
        help=(
            "传给 xhs_images_to_picset：在 PS 前启用「画图软件人工」门禁（READY.signal）。"
            "默认不传，避免编排任务无限等待。"
        ),
    )
    parser.add_argument(
        "--wait-enter-after-watermark",
        action="store_true",
        help="传给 xhs_images_to_picset：水印/无痕清印步骤后在终端按回车再继续 Picset",
    )

    args = parser.parse_args()
    if args.watermark_full_auto and args.watermark_post_workflow:
        print(
            "Error: use either --watermark-full-auto or --watermark-post-workflow, not both.",
            file=sys.stderr,
        )
        sys.exit(2)
    _debug_log(
        "H13",
        "visual_publish_pipeline.py:main:args",
        "Visual pipeline args parsed",
        {
            "keyword_strategy": args.keyword_strategy,
            "limit_notes": int(args.limit_notes),
            "max_reference_covers": int(args.max_reference_covers),
            "generate_timeout": int(args.generate_timeout),
            "max_download": int(args.max_download),
            "publish_to_xhs": bool(args.publish_to_xhs),
        },
    )

    if args.no_generate:
        print(
            "Error: 请使用: python scripts/xhs_images_to_picset.py --skip-upload ... 做仅下载。",
            file=sys.stderr,
        )
        sys.exit(2)

    if getattr(args, "skip_keyword_discover", False):
        kw = (args.seed_keyword or "").strip() or "商品"
        recommended = []
        print(
            "[visual-pipeline] Step1 跳过 (--skip-keyword-discover)；"
            f"搜索关键词 = {kw!r}",
            flush=True,
        )
    else:
        kw, recommended = discover_keyword(
            args.seed_keyword,
            args.sort_by,
            args.keyword_strategy,
            args.publish_time,
            "图文",
            host=args.host,
            port=int(args.port),
            account=args.account,
            reuse_existing_tab=bool(args.reuse_existing_tab),
            search_feed_timeout=int(args.search_feed_timeout),
            discover_max_probes=int(args.discover_max_probes),
        )

    if args.save_topic_json:
        p = Path(args.save_topic_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "search_keyword_used": kw,
                    "seed_keyword": args.seed_keyword.strip(),
                    "recommended_keywords": recommended,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"[visual-pipeline] 已写入话题/推荐词: {p}")

    lim = max(1, min(args.limit_notes, args.max_reference_covers))

    default_prompt = (
        "电商详情主图：参考已上传的热门笔记封面风格与构图，结合产品素材图；"
        "清新自然光影、竖版 4:5，新中式高级感，顶部留白标题区。"
    )

    bridge: list[str] = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "xhs_images_to_picset.py"),
        "--keyword",
        kw,
        "--sort-by",
        args.sort_by,
        "--publish-time",
        args.publish_time,
        "--limit-notes",
        str(lim),
        "--max-images",
        str(lim),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--picset-url",
        args.picset_url,
        "--login-timeout",
        str(args.login_timeout),
        "--generate",
        "--generate-timeout",
        str(args.generate_timeout),
        "--max-download",
        str(max(1, args.max_download)),
    ]
    if args.picset_batch_size is not None:
        bridge.extend(
            ["--picset-batch-size", str(max(1, int(args.picset_batch_size)))]
        )
    bridge += [
        "--product-images",
        *args.product_images,
    ]
    if args.prompt_file:
        bridge.extend(["--prompt-file", args.prompt_file])
    elif args.prompt:
        bridge.extend(["--prompt", args.prompt])
    else:
        bridge.extend(["--prompt", default_prompt])

    if args.account:
        bridge.extend(["--account", args.account])
    if args.reuse_existing_tab:
        bridge.append("--reuse-existing-tab")
    else:
        bridge.append("--no-reuse-existing-tab")
    if getattr(args, "prefer_ecommerce_covers", True):
        bridge.append("--prefer-ecommerce-covers")
    else:
        bridge.append("--no-prefer-ecommerce-covers")

    if args.summary_json:
        bridge.extend(["--summary-json", args.summary_json])
    if args.strict_step_lock:
        bridge.append("--strict-step-lock")
    else:
        bridge.append("--no-strict-step-lock")

    if args.photoshop_after_generate:
        bridge.append("--photoshop-after-generate")
        if getattr(args, "manual_draw_before_photoshop", False):
            bridge.append("--manual-draw-before-photoshop")
    if args.watermark_full_auto:
        bridge.append("--watermark-full-auto")
        if args.watermark_no_inpaint:
            bridge.append("--watermark-no-inpaint")
        bridge.extend(["--watermark-corner-w", str(float(args.watermark_corner_w))])
        bridge.extend(["--watermark-corner-h", str(float(args.watermark_corner_h))])
        if args.watermark_photoshop:
            bridge.append("--watermark-photoshop")
    elif args.watermark_post_workflow:
        bridge.append("--watermark-post-workflow")
        if getattr(args, "watermark_tool_url", None):
            bridge.extend(["--watermark-tool-url", str(args.watermark_tool_url)])
        if args.no_open_watermark_url:
            bridge.append("--no-open-watermark-url")

    if getattr(args, "wait_enter_after_watermark", False):
        bridge.append("--wait-enter-after-watermark")

    if args.publish_to_xhs:
        bridge.append("--publish-to-xhs")
        if args.preview:
            bridge.append("--preview")
        if args.title_file:
            bridge.extend(["--title-file", args.title_file])
        elif args.title:
            bridge.extend(["--title", args.title])
        if args.content_file:
            bridge.extend(["--content-file", args.content_file])
        elif args.content:
            bridge.extend(["--content", args.content])
        if not (args.title or args.title_file) or not (args.content or args.content_file):
            print(
                "Error: --publish-to-xhs 需要 --title/--title-file 与 --content/--content-file",
                file=sys.stderr,
            )
            sys.exit(2)

    print("[visual-pipeline] Step2–5: 调用 xhs_images_to_picset ...")
    try:
        printable = subprocess.list2cmdline(bridge)
    except Exception:
        printable = " ".join(bridge)
    print("[visual-pipeline]", printable)
    if int(args.bridge_timeout) > 0:
        bto = max(240, int(args.bridge_timeout))
    else:
        # Extra slack when PS JSX runs after Picset (often 5–15+ min on large exports).
        ps_slack = 900 if bool(getattr(args, "photoshop_after_generate", False)) else 0
        bto = max(480, int(args.login_timeout) + int(args.generate_timeout) + 420 + ps_slack)
    print(f"[visual-pipeline] bridge hard-timeout: {int(bto)}s (login/gen/下载/PS 预算)", flush=True)
    t0 = time.time()
    _debug_log(
        "H13",
        "visual_publish_pipeline.py:main:bridge_start",
        "Starting xhs_images_to_picset subprocess",
        {
            "argv0": bridge[0],
            "argv1": bridge[1] if len(bridge) > 1 else "",
            "arg_count": len(bridge),
            "bridge_timeout": int(bto),
        },
    )
    try:
        proc = subprocess.run(bridge, cwd=SCRIPT_DIR, timeout=bto)
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 1)
        print(
            f"[visual-pipeline] ERROR: xhs_images_to_picset 超时（>{bto}s, 已耗时 {elapsed}s）。"
            " 可提高 --generate-timeout / --bridge-timeout，或检查 Picset/CDP。",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(124)
    _debug_log(
        "H13",
        "visual_publish_pipeline.py:main:bridge_end",
        "xhs_images_to_picset subprocess finished",
        {"returncode": int(proc.returncode), "elapsed_sec": round(time.time() - t0, 2)},
    )
    raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()
