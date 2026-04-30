"""
小红书搜封面 → Picset（4 参考 + 产品）→ 生成 4 张 →（可选豆包文案）→ 带话题发布。

依赖：本机 Chrome CDP、已登录小红书 + Picset；豆包需 ARK_API_KEY + ARK_MODEL。

用法示例：

  python scripts/full_stack_xhs_picset_publish.py ^
    --product-images "D:\\tea.png" ^
    --seed-keyword "绿茶" ^
    --brief-file "D:\\brief.txt"

默认要求设置 ARK（`ARK_API_KEY` + `ARK_MODEL`）才进入发布步骤；
若需兼容旧行为，可显式加 `--allow-placeholder-preview`，在无 ARK 时写占位文案并仅预览。

可选：`--photoshop-after-generate` 在 Picset 下载生成图后对「图像→自动色调/对比度/颜色」做 JSX 批处理
（等价手动点菜单；需 Photoshop + pywin32），再通过 summary 优选 after_photoshop_autotcc 路径进豆包与发布。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from env_local_loader import load_env_local

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DEBUG_LOG_PATH = Path(REPO_ROOT) / "debug-471add.log"
load_env_local(REPO_ROOT)


def _debug_log(
    run_id: str,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
) -> None:
    payload = {
        "sessionId": "471add",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[debug471add] log_write_failed path={DEBUG_LOG_PATH} err={e}", file=sys.stderr)


# region agent log
_debug_log(
    "module_load",
    "H9",
    "full_stack_xhs_picset_publish.py:module",
    "Module loaded; instrumentation active",
    {"log_path": str(DEBUG_LOG_PATH)},
)
# endregion


def _infer_seed_keyword(product_images: list[str], fallback: str = "商品") -> str:
    joined = " ".join(Path(p).name for p in product_images).lower()
    rules = [
        (("月饼", "mooncake"), "月饼"),
        (("茶", "绿茶", "tea"), "绿茶"),
        (("咖啡", "coffee"), "咖啡"),
        (("蛋糕", "cake"), "蛋糕"),
        (("护肤", "skincare"), "护肤"),
    ]
    for keys, label in rules:
        if any(k in joined for k in keys):
            return label
    return fallback


def _auto_brief(seed_keyword: str) -> str:
    return (
        f"【商品】{seed_keyword}相关产品。视觉主张：干净、高级、适合小红书种草与电商主图。\n"
        "【卖点】突出口感/质感/场景价值，避免夸大承诺与医疗化表达。\n"
        "【受众】送礼、自用、办公室/家庭场景。\n"
        "【语气】真实体验+购买决策信息，口语化，便于转化。"
    )


def _placeholder_copy(seed_keyword: str) -> tuple[str, str]:
    title = f"{seed_keyword}上新｜好看又好吃的节日礼盒"
    body = (
        f"这次把{seed_keyword}做成更适合送礼和自留的版本，开盒颜值在线，口味层次也更丰富。"
        "入口细腻不腻，搭配茶饮更平衡，办公室下午茶和家庭分享都很合适。"
    )
    tags = f"#{seed_keyword} #中秋送礼 #礼盒推荐 #好物种草 #节日氛围 #办公室分享 #家庭团圆 #小红书好物"
    return title, f"{body}\n\n{tags}\n"


def _dedupe_existing_paths_by_hash(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        pp = Path(p)
        if not pp.is_file():
            continue
        digest = hashlib.sha256(pp.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        out.append(str(pp))
    return out


def _validate_publish_images(summary: dict, image_paths: list[str]) -> list[str]:
    """
    Guardrail: only allow true generated outputs for publish.

    Prevent accidental fallback to downloaded XHS reference images when generation
    or manual drawing flow is incomplete.
    """
    generated_root_raw = str(summary.get("generated_output_dir") or "").strip()
    ref_root_raw = str(summary.get("output_dir") or "").strip()
    generated_root = Path(generated_root_raw).resolve() if generated_root_raw else None
    ref_root = Path(ref_root_raw).resolve() if ref_root_raw else None

    out: list[str] = []
    for p in image_paths:
        pp = Path(p).resolve()
        if not pp.is_file():
            continue
        # Must be under generated_output_dir when available.
        if generated_root and not pp.is_relative_to(generated_root):
            continue
        # Must never come from XHS reference download directory.
        if ref_root and pp.is_relative_to(ref_root):
            continue
        out.append(str(pp))
    return out


def _load_reusable_generated_paths(summary_path: Path, want: int) -> list[str]:
    """
    Read previous summary-json and return reusable generated image paths.
    """
    if not summary_path.is_file():
        return []
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    gen_paths = summary.get("generated_local_paths") or []
    gen_paths = _dedupe_existing_paths_by_hash([str(Path(p)) for p in gen_paths])
    gen_paths = _validate_publish_images(summary, gen_paths)
    if len(gen_paths) < max(1, int(want)):
        return []
    return gen_paths[: max(1, int(want))]


def main() -> None:
    parser = argparse.ArgumentParser(description="XHS 参考 → Picset×4 → 豆包文案 → 发布")
    parser.add_argument("--product-images", nargs="+", required=True)
    parser.add_argument("--seed-keyword", default=None)
    parser.add_argument(
        "--keyword-strategy",
        choices=("seed", "recommended_first", "recommended_best"),
        default="recommended_best",
    )
    parser.add_argument("--sort-by", default="最多点赞")
    parser.add_argument(
        "--publish-time",
        default="一周内",
        choices=("不限", "一天内", "一周内", "半年内"),
    )
    parser.add_argument("--reference-count", type=int, default=12, help="搜索封面张数")
    parser.add_argument("--max-download", type=int, default=1, help="生成图下载张数")
    parser.add_argument(
        "--picset-batch-size",
        type=int,
        default=1,
        help="Picset 界面「一次生成 N 张」（默认 1）",
    )
    parser.add_argument("--generate-timeout", type=int, default=900)
    parser.add_argument(
        "--search-feed-timeout",
        type=int,
        default=120,
        help="单次 search-feeds 超时（传给 visual_publish_pipeline，避免长时间无响应）",
    )
    parser.add_argument(
        "--discover-max-probes",
        type=int,
        default=4,
        help="热点词多候选评估上限（传给 visual_publish_pipeline）",
    )
    parser.add_argument(
        "--visual-step-timeout",
        type=int,
        default=0,
        help=(
            "Step A（visual_publish_pipeline）整段硬超时秒数；0=根据搜词+生图预算自动估算"
            "（约 search×discover + generate，上限 7200）"
        ),
    )
    parser.add_argument("--brief-file", default=None, help="给豆包的商品说明 UTF-8（推荐）")
    parser.add_argument("--brief", default=None, help="内联 brief（无文件时用）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--account", default=None)
    parser.add_argument("--picset-url", default="https://picsetai.cn/")
    parser.add_argument(
        "--preview-publish",
        action="store_true",
        help="发布时只预览（不点发布）",
    )
    parser.add_argument(
        "--force-publish",
        action="store_true",
        help="兼容参数（当前与默认行为一致：不加 --preview-publish 时尝试自动发布）",
    )
    parser.add_argument(
        "--allow-placeholder-preview",
        action="store_true",
        help="无 ARK 时允许写占位文案，并以 --preview 结束（兼容旧行为）",
    )
    parser.add_argument(
        "--step-a-retries",
        type=int,
        default=1,
        help="Step A（参考图+Picset）失败后自动重试次数（默认 1）",
    )
    parser.add_argument(
        "--skip-visual-keyword-discover",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "跳过 visual Step1 的 discover_keyword（多轮 search-feeds 探针），"
            "直接用 --seed-keyword 作为 xhs_images 搜索词（仍会在 xhs_images 内搜一次封面）。"
            "需要 recommended_best 多候选探针时请设 --no-skip-visual-keyword-discover。"
        ),
    )
    parser.add_argument(
        "--photoshop-after-generate",
        action="store_true",
        help=(
            "Step A（Picset 生成图落盘后）：Photoshop JSX 等价「图像→自动色调/对比度/颜色」;"
            " 需 PS + pip install pywin32；summary 会用 after_photoshop_autotcc 再走豆包/发布。"
        ),
    )
    parser.add_argument(
        "--reuse-last-generated",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Reuse generated images from tmp/last_picset_summary.json when valid "
            "(default ON) to skip XHS search + Picset generation."
        ),
    )
    parser.add_argument(
        "--strict-step-lock",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Hard gate mode (default ON): if any step fails, do not start next step."
        ),
    )
    args = parser.parse_args()
    run_id = f"run_{int(time.time() * 1000)}"
    print(f"[debug471add] active log_path={DEBUG_LOG_PATH}", flush=True)
    # region agent log
    _debug_log(
        run_id,
        "H1",
        "full_stack_xhs_picset_publish.py:main:args",
        "Parsed CLI args for publish mode",
        {
            "preview_publish": bool(args.preview_publish),
            "allow_placeholder_preview": bool(args.allow_placeholder_preview),
            "force_publish": bool(args.force_publish),
            "has_account_arg": bool(args.account),
            "host": args.host,
            "port": int(args.port),
        },
    )
    # endregion
    seed_keyword = (args.seed_keyword or "").strip() or _infer_seed_keyword(args.product_images, "商品")
    print(f"[full-stack] Zero-touch mode keyword: {seed_keyword}", flush=True)

    summary_path = Path(REPO_ROOT) / "tmp" / "last_picset_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    want = max(1, args.max_download)
    gen_paths: list[str] = []

    if args.reuse_last_generated:
        gen_paths = _load_reusable_generated_paths(summary_path, want)
        if gen_paths:
            print(
                f"[full-stack] Reusing {len(gen_paths)} existing generated image(s); "
                "skip XHS search + Picset generation.",
                flush=True,
            )

    if not gen_paths:
        vp_cmd = [
            sys.executable,
            str(Path(SCRIPT_DIR) / "visual_publish_pipeline.py"),
            "--product-images",
            *args.product_images,
            "--seed-keyword",
            seed_keyword,
            "--keyword-strategy",
            args.keyword_strategy,
            "--sort-by",
            args.sort_by,
            "--publish-time",
            args.publish_time,
            "--limit-notes",
            str(max(1, args.reference_count)),
            "--max-reference-covers",
            str(max(1, args.reference_count)),
            "--max-download",
            str(max(1, args.max_download)),
        ]
        pbs = args.picset_batch_size if args.picset_batch_size is not None else args.max_download
        vp_cmd += [
            "--picset-batch-size",
            str(max(1, int(pbs))),
            "--generate-timeout",
            str(args.generate_timeout),
            "--summary-json",
            str(summary_path),
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--picset-url",
            args.picset_url,
        ]
        if args.account:
            vp_cmd.extend(["--account", args.account])
        if args.strict_step_lock:
            vp_cmd.append("--strict-step-lock")
        else:
            vp_cmd.append("--no-strict-step-lock")
        if getattr(args, "photoshop_after_generate", False):
            vp_cmd.append("--photoshop-after-generate")
        if bool(getattr(args, "skip_visual_keyword_discover", False)):
            vp_cmd.append("--skip-keyword-discover")

        vp_cmd.extend(
            [
                "--search-feed-timeout",
                str(max(45, int(args.search_feed_timeout))),
                "--discover-max-probes",
                str(max(2, min(12, int(args.discover_max_probes)))),
            ]
        )

        attempts = max(1, int(args.step_a_retries))
        r1 = None
        sft = max(45, int(args.search_feed_timeout))
        dmp = max(2, min(12, int(args.discover_max_probes)))
        gen_budget = max(300, int(args.generate_timeout))
        photo_slack = 720 if getattr(args, "photoshop_after_generate", False) else 0
        if int(getattr(args, "visual_step_timeout", 0)) > 0:
            step_a_budget = int(args.visual_step_timeout)
        else:
            discover_budget = sft * max(2, dmp) + 180
            bridge_budget = gen_budget + 780 + photo_slack
            step_a_budget = discover_budget + bridge_budget + 300
        step_a_budget = max(960, min(7200, step_a_budget))

        for i in range(1, attempts + 1):
            if i >= 2:
                salvage = _load_reusable_generated_paths(summary_path, want)
                if len(salvage) >= want:
                    print(
                        f"[full-stack] Step A attempt {i}/{attempts}: "
                        f"检测到 Picset 已落盘生成图（{len(salvage)} 张），"
                        "跳过小红书搜索与 Picset 全流程重跑。",
                        flush=True,
                    )
                    r1 = subprocess.CompletedProcess(vp_cmd, 0, "", "")
                    break
            print(
                f"[full-stack] Step A: visual_publish_pipeline → Picset (attempt {i}/{attempts}) …"
                f" [outer-timeout={step_a_budget}s]",
                flush=True,
            )
            try:
                r1 = subprocess.run(vp_cmd, cwd=SCRIPT_DIR, timeout=float(step_a_budget))
            except subprocess.TimeoutExpired:
                print(
                    f"[full-stack] ERROR: Step A 超过整段超时 {step_a_budget}s（已终止子进程）。"
                    " 可提高 --visual-step-timeout / --generate-timeout，或减少 --discover-max-probes。",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(124)
            if r1.returncode == 0:
                break
            if i < attempts:
                print("[full-stack] Step A failed; retrying in 5s...", file=sys.stderr)
                time.sleep(5)
        if r1 is None or r1.returncode != 0:
            sys.exit(r1.returncode if r1 else 2)

        if not summary_path.is_file():
            print("Error: summary-json missing.", file=sys.stderr)
            sys.exit(2)

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        gen_paths = summary.get("generated_local_paths") or []
        gen_paths = _dedupe_existing_paths_by_hash([str(Path(p)) for p in gen_paths])
        gen_paths = _validate_publish_images(summary, gen_paths)
        if len(gen_paths) < 1:
            print(
                "Error: no valid generated images for publish "
                "(blocked reference-image fallback).",
                file=sys.stderr,
            )
            sys.exit(2)
        if len(gen_paths) < want:
            print(
                f"Error: need {want} unique generated images, but only got {len(gen_paths)}.",
                file=sys.stderr,
            )
            sys.exit(2)
        gen_paths = gen_paths[:want]
    print(
        f"[full-stack] Using {len(gen_paths)} generated image(s) for copy + publish.",
        flush=True,
    )

    promo_dir = Path(REPO_ROOT) / "tmp" / "xhs_promo_out"
    promo_dir.mkdir(parents=True, exist_ok=True)

    has_ark = bool((os.environ.get("ARK_API_KEY") or "").strip()) and bool(
        (os.environ.get("ARK_MODEL") or "").strip()
    )
    # region agent log
    _debug_log(
        run_id,
        "H2",
        "full_stack_xhs_picset_publish.py:main:ark_check",
        "ARK environment readiness evaluated",
        {
            "has_ark_api_key": bool((os.environ.get("ARK_API_KEY") or "").strip()),
            "has_ark_model": bool((os.environ.get("ARK_MODEL") or "").strip()),
            "has_ark": bool(has_ark),
        },
    )
    # endregion

    if args.brief_file:
        brief_src = Path(args.brief_file)
        if not brief_src.is_file():
            print(f"Error: --brief-file not found: {brief_src}", file=sys.stderr)
            sys.exit(2)
        brief_text = brief_src.read_text(encoding="utf-8")
    elif args.brief:
        brief_text = args.brief
    else:
        brief_text = _auto_brief(seed_keyword)

    brief_path = promo_dir / "product_brief.txt"
    brief_path.write_text(brief_text.strip() + "\n", encoding="utf-8")

    douban = Path(SCRIPT_DIR) / "douban_promo_copy.py"
    title_path = promo_dir / "title.txt"
    content_path = promo_dir / "content.txt"

    if has_ark:
        print("[full-stack] Step B: 豆包（Ark）生成标题/正文/话题 …", flush=True)
        dcmd = [
            sys.executable,
            str(douban),
            "--provider",
            "ark",
            "--brief-file",
            str(brief_path),
            "--seed-keyword",
            seed_keyword,
            "--images",
            *gen_paths,
            "--out-dir",
            str(promo_dir),
            "--dump-raw-response",
        ]
        r2 = subprocess.run(dcmd, cwd=SCRIPT_DIR)
        if r2.returncode != 0:
            if args.strict_step_lock:
                print(
                    "[full-stack] Error: Step B (Ark) failed; strict-step-lock prevents Step C.",
                    file=sys.stderr,
                )
                sys.exit(r2.returncode)
            # Non-strict mode fallback
            print(
                "[full-stack] WARNING: Step B (Ark) failed; using placeholder title/content to keep the workflow going.",
                file=sys.stderr,
            )
            t, c = _placeholder_copy(seed_keyword)
            title_path.write_text(t + "\n", encoding="utf-8")
            content_path.write_text(c, encoding="utf-8")
    elif args.allow_placeholder_preview:
        print(
            "[full-stack] Step B: 未检测到 ARK_API_KEY+ARK_MODEL，"
            "写入占位文案；发布将使用 --preview（allow-placeholder-preview）。",
            file=sys.stderr,
        )
        t, c = _placeholder_copy(seed_keyword)
        title_path.write_text(t + "\n", encoding="utf-8")
        content_path.write_text(c, encoding="utf-8")
    else:
        # region agent log
        _debug_log(
            run_id,
            "H2",
            "full_stack_xhs_picset_publish.py:main:ark_missing_exit",
            "Exit due to missing ARK env and placeholder preview disabled",
            {"exit_code": 2},
        )
        # endregion
        print(
            "[full-stack] Error: 未检测到 ARK_API_KEY+ARK_MODEL。"
            "为避免只填不发，请先设置豆包环境变量后重试；"
            "如需旧行为请显式加 --allow-placeholder-preview。",
            file=sys.stderr,
        )
        sys.exit(2)

    publish = Path(SCRIPT_DIR) / "publish_pipeline.py"
    preview = bool(args.preview_publish) or (not has_ark)
    # region agent log
    _debug_log(
        run_id,
        "H3",
        "full_stack_xhs_picset_publish.py:main:preview_decision",
        "Preview decision computed",
        {
            "preview": bool(preview),
            "preview_publish_arg": bool(args.preview_publish),
            "has_ark": bool(has_ark),
        },
    )
    # endregion

    pub_cmd = [
        sys.executable,
        str(publish),
        "--title-file",
        str(title_path),
        "--content-file",
        str(content_path),
        "--images",
        *gen_paths,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--reuse-existing-tab",
        "--timing-jitter",
        "0.25",
    ]
    if args.account:
        pub_cmd.extend(["--account", args.account])
    if preview:
        pub_cmd.append("--preview")
    # region agent log
    _debug_log(
        run_id,
        "H4",
        "full_stack_xhs_picset_publish.py:main:publish_cmd",
        "Prepared publish command",
        {
            "contains_preview_flag": ("--preview" in pub_cmd),
            "images_count": len(gen_paths),
            "has_account_arg": bool(args.account),
        },
    )
    # endregion

    print(
        f"[full-stack] Step C: publish_pipeline ({'预览' if preview else '尝试发布'}) …",
        flush=True,
    )
    r3 = subprocess.run(pub_cmd, cwd=SCRIPT_DIR)
    # region agent log
    _debug_log(
        run_id,
        "H5",
        "full_stack_xhs_picset_publish.py:main:publish_result",
        "publish_pipeline subprocess finished",
        {"returncode": int(r3.returncode)},
    )
    # endregion
    sys.exit(r3.returncode)


if __name__ == "__main__":
    main()
