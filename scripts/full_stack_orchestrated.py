"""
Orchestrated full-stack workflow with class-based steps.

Pipeline:
  Step A: XHS refs -> Picset generate (via visual_publish_pipeline.py)
  Step B: Copywriting (Ark) or placeholder fallback
  Step C: publish_pipeline.py

This script is designed for lower maintenance:
- each stage is isolated in a class;
- step-level failure report is generated as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from env_local_loader import load_env_local
from media_path_utils import dedupe_existing_paths_by_hash, validate_publish_images
from workflow_core import StepFailure, WorkflowContext, WorkflowRunner, WorkflowStep
from workflow_status import derive_overall_status, next_action_for_named_flow

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TMP_DIR = REPO_ROOT / "tmp"

load_env_local(REPO_ROOT)

STEP_ORDER = ("a", "b", "c")
STEP_NAME_TO_ID = {
    "step_a_visual_generate": "a",
    "step_b_copywriting": "b",
    "step_c_publish": "c",
}
STEP_NO_BY_ID = {"a": 1, "b": 2, "c": 3}


def _step_index(step_id: str) -> int:
    return STEP_ORDER.index(step_id)


def _report_step_no(step_name: str) -> int:
    sid = STEP_NAME_TO_ID.get(step_name, "")
    return int(STEP_NO_BY_ID.get(sid, 0))


def _derive_state_status(report_statuses: list[str]) -> str:
    return derive_overall_status(
        report_statuses,
        allow_skipped_as_success=False,
    )


def _next_action_from_reports(selected_steps: list[WorkflowStep], reports: list[Any]) -> str:
    selected_names = [s.name for s in selected_steps]
    completed = {r.name for r in reports}
    last_status = str(reports[-1].status) if reports else None
    last_name = str(reports[-1].name) if reports else None
    return next_action_for_named_flow(
        selected_step_names=selected_names,
        completed_step_names=completed,
        last_status=last_status,
        last_name=last_name,
    )


def _build_pending_fix_list(reports: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in reports:
        if str(r.status) == "failed":
            err = r.error or {}
            out.append(
                {
                    "step": _report_step_no(r.name),
                    "name": r.name,
                    "reason": str(err.get("message") or "step_failed"),
                    "status": "pending_fix",
                    "created_at": r.ended_at,
                }
            )
    return out


def _write_state_json(
    path: Path,
    *,
    run_id: str,
    run_mode: str,
    task_input: dict[str, Any],
    ctx: WorkflowContext,
    selected_steps: list[WorkflowStep],
    reports: list[Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    statuses = [str(r.status) for r in reports]
    current_step = max((_report_step_no(r.name) for r in reports), default=0)
    payload = {
        "task_id": run_id,
        "run_mode": run_mode,
        "current_step": current_step,
        "current_status": _derive_state_status(statuses),
        "next_action": _next_action_from_reports(selected_steps, reports),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": task_input,
        "context": {
            "meta": ctx.meta,
            "artifacts": ctx.artifacts,
        },
        "steps": [
            {
                "step": _report_step_no(r.name),
                "name": r.name,
                "status": r.status,
                "started_at": r.started_at,
                "ended_at": r.ended_at,
                "duration_ms": r.duration_ms,
                "data": r.data,
                "error": r.error,
            }
            for r in reports
        ],
        "pending_fix_list": _build_pending_fix_list(reports),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _pick_steps(
    all_steps: list[WorkflowStep],
    *,
    from_step: str,
    to_step: str,
) -> list[WorkflowStep]:
    lo = _step_index(from_step)
    hi = _step_index(to_step)
    if lo > hi:
        raise SystemExit("--from-step cannot be after --to-step.")

    selected: list[WorkflowStep] = []
    for step in all_steps:
        sid = STEP_NAME_TO_ID.get(step.name, "")
        if not sid:
            continue
        pos = _step_index(sid)
        if lo <= pos <= hi:
            selected.append(step)
    return selected


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_seconds: int = 0,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            timeout=(max(1, int(timeout_seconds)) if timeout_seconds else None),
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124)


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
        f"【商品】{seed_keyword}相关产品。视觉主张：干净、高级，适合小红书种草与电商主图。\n"
        "【卖点】突出质感/口感/场景价值，避免夸大承诺。\n"
        "【受众】送礼、自用、办公与家庭场景。\n"
        "【语气】真实体验 + 决策信息，便于转化。"
    )


def _placeholder_copy(seed_keyword: str) -> tuple[str, str]:
    title = f"{seed_keyword}新品到啦，送礼自用都合适"
    body = (
        f"这次把{seed_keyword}做成更适合日常分享的版本，颜值在线，风味更清爽。\n"
        "入口层次感更好，搭配茶点和下午茶都很合适。"
    )
    tags = f"#{seed_keyword} #茶叶推荐 #办公室茶饮 #日常分享 #平价好物 #小红书种草"
    return title, f"{body}\n\n{tags}\n"


def _load_reusable_generated_paths(summary_path: Path, want: int) -> list[str]:
    if not summary_path.is_file():
        return []
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    gen_paths = summary.get("generated_local_paths") or []
    gen_paths = dedupe_existing_paths_by_hash([str(Path(p)) for p in gen_paths])
    gen_paths = validate_publish_images(summary, gen_paths)
    if len(gen_paths) < max(1, int(want)):
        return []
    return gen_paths[: max(1, int(want))]


class StepAVisualGenerate(WorkflowStep):
    name = "step_a_visual_generate"

    def __init__(self, args: argparse.Namespace):
        self.args = args

    def run(self, ctx: WorkflowContext) -> dict[str, Any]:
        summary_path = Path(ctx.artifacts["summary_path"])
        want = max(1, int(self.args.max_download))

        gen_paths: list[str] = []
        if bool(getattr(self.args, "reuse_last_generated", True)):
            gen_paths = _load_reusable_generated_paths(summary_path, want)
            if gen_paths:
                ctx.artifacts["generated_paths"] = gen_paths
                return {
                    "reused": True,
                    "generated_count": len(gen_paths),
                    "summary_path": str(summary_path),
                }

        if bool(getattr(self.args, "dry_run", False)):
            ctx.artifacts["generated_paths"] = []
            return {
                "dry_run": True,
                "reused": False,
                "generated_count": 0,
                "summary_path": str(summary_path),
                "note": "Step A external calls skipped in dry-run mode.",
            }

        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "visual_publish_pipeline.py"),
            "--product-images",
            *self.args.product_images,
            "--seed-keyword",
            ctx.artifacts["seed_keyword"],
            "--keyword-strategy",
            self.args.keyword_strategy,
            "--sort-by",
            self.args.sort_by,
            "--publish-time",
            self.args.publish_time,
            "--limit-notes",
            str(max(1, int(self.args.reference_count))),
            "--max-reference-covers",
            str(max(1, int(self.args.reference_count))),
            "--max-download",
            str(max(1, int(self.args.max_download))),
            "--picset-batch-size",
            str(max(1, int(self.args.picset_batch_size))),
            "--generate-timeout",
            str(max(60, int(self.args.generate_timeout))),
            "--summary-json",
            str(summary_path),
            "--host",
            self.args.host,
            "--port",
            str(int(self.args.port)),
            "--picset-url",
            self.args.picset_url,
            "--search-feed-timeout",
            str(max(45, int(self.args.search_feed_timeout))),
            "--discover-max-probes",
            str(max(1, min(12, int(self.args.discover_max_probes)))),
        ]
        if self.args.account:
            cmd.extend(["--account", self.args.account])
        if bool(getattr(self.args, "skip_visual_keyword_discover", False)):
            cmd.append("--skip-keyword-discover")
        if bool(getattr(self.args, "photoshop_after_generate", False)):
            cmd.append("--photoshop-after-generate")
        if bool(getattr(self.args, "strict_step_lock", True)):
            cmd.append("--strict-step-lock")
        else:
            cmd.append("--no-strict-step-lock")

        sft = max(45, int(self.args.search_feed_timeout))
        dmp = max(2, min(12, int(self.args.discover_max_probes)))
        gen_budget = max(300, int(self.args.generate_timeout))
        color_slack = 720 if bool(getattr(self.args, "photoshop_after_generate", False)) else 0
        if int(getattr(self.args, "visual_step_timeout", 0)) > 0:
            step_budget = int(self.args.visual_step_timeout)
        else:
            step_budget = (sft * max(2, dmp) + 180) + (gen_budget + 780 + color_slack) + 300
        step_budget = max(960, min(7200, step_budget))

        attempts = max(1, int(self.args.step_a_retries))
        last_rc = 2
        for i in range(1, attempts + 1):
            print(
                f"[orchestrated] Step A attempt {i}/{attempts} (timeout={step_budget}s)",
                flush=True,
            )
            cp = _run_subprocess(cmd, cwd=SCRIPT_DIR, timeout_seconds=step_budget)
            last_rc = int(cp.returncode)
            if last_rc == 0:
                break
            if i < attempts:
                time.sleep(5)

        if last_rc != 0:
            raise StepFailure(
                "Step A failed: visual_publish_pipeline",
                code=(124 if last_rc == 124 else 2),
                detail={"returncode": last_rc},
            )

        if not summary_path.is_file():
            raise StepFailure("Step A failed: missing summary JSON.", code=2)

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        gen_paths = summary.get("generated_local_paths") or []
        gen_paths = dedupe_existing_paths_by_hash([str(Path(p)) for p in gen_paths])
        gen_paths = validate_publish_images(summary, gen_paths)
        if len(gen_paths) < want:
            raise StepFailure(
                "Step A failed: insufficient valid generated images.",
                code=2,
                detail={"need": want, "got": len(gen_paths)},
            )

        ctx.artifacts["generated_paths"] = gen_paths[:want]
        return {
            "reused": False,
            "generated_count": len(ctx.artifacts["generated_paths"]),
            "summary_path": str(summary_path),
        }


class StepBCopywriting(WorkflowStep):
    name = "step_b_copywriting"

    def __init__(self, args: argparse.Namespace):
        self.args = args

    def run(self, ctx: WorkflowContext) -> dict[str, Any]:
        promo_dir = Path(ctx.artifacts["promo_dir"])
        promo_dir.mkdir(parents=True, exist_ok=True)
        seed_keyword = str(ctx.artifacts["seed_keyword"])

        if self.args.brief_file:
            brief_src = Path(self.args.brief_file)
            if not brief_src.is_file():
                raise StepFailure("Step B failed: --brief-file not found.", code=2)
            brief_text = brief_src.read_text(encoding="utf-8")
        elif self.args.brief:
            brief_text = self.args.brief
        else:
            brief_text = _auto_brief(seed_keyword)

        brief_path = promo_dir / "product_brief.txt"
        brief_path.write_text(brief_text.strip() + "\n", encoding="utf-8")
        title_path = promo_dir / "title.txt"
        content_path = promo_dir / "content.txt"

        if bool(getattr(self.args, "dry_run", False)):
            title, body = _placeholder_copy(seed_keyword)
            title_path.write_text(title + "\n", encoding="utf-8")
            content_path.write_text(body, encoding="utf-8")
            ctx.artifacts["has_ark"] = False
            ctx.artifacts["title_file"] = str(title_path)
            ctx.artifacts["content_file"] = str(content_path)
            return {
                "dry_run": True,
                "title_file": str(title_path),
                "content_file": str(content_path),
                "note": "Step B model call skipped in dry-run mode.",
            }

        has_ark = bool((os.environ.get("ARK_API_KEY") or "").strip()) and bool(
            (os.environ.get("ARK_MODEL") or "").strip()
        )
        ctx.artifacts["has_ark"] = has_ark

        if has_ark:
            cmd = [
                sys.executable,
                str(SCRIPT_DIR / "douban_promo_copy.py"),
                "--provider",
                "ark",
                "--brief-file",
                str(brief_path),
                "--seed-keyword",
                seed_keyword,
                "--images",
                *ctx.artifacts["generated_paths"],
                "--out-dir",
                str(promo_dir),
                "--dump-raw-response",
            ]
            cp = _run_subprocess(cmd, cwd=SCRIPT_DIR)
            if cp.returncode != 0:
                if bool(getattr(self.args, "strict_step_lock", True)):
                    raise StepFailure(
                        "Step B failed: Ark copy generation failed.",
                        code=2,
                        detail={"returncode": int(cp.returncode)},
                    )
                title, body = _placeholder_copy(seed_keyword)
                title_path.write_text(title + "\n", encoding="utf-8")
                content_path.write_text(body, encoding="utf-8")
        elif bool(getattr(self.args, "allow_placeholder_preview", False)):
            title, body = _placeholder_copy(seed_keyword)
            title_path.write_text(title + "\n", encoding="utf-8")
            content_path.write_text(body, encoding="utf-8")
        else:
            raise StepFailure(
                "Step B failed: ARK_API_KEY / ARK_MODEL missing.",
                code=2,
            )

        if not title_path.is_file() or not content_path.is_file():
            raise StepFailure("Step B failed: title/content output missing.", code=2)

        ctx.artifacts["title_file"] = str(title_path)
        ctx.artifacts["content_file"] = str(content_path)
        return {
            "has_ark": has_ark,
            "title_file": str(title_path),
            "content_file": str(content_path),
        }


class StepCPublish(WorkflowStep):
    name = "step_c_publish"

    def __init__(self, args: argparse.Namespace):
        self.args = args

    def run(self, ctx: WorkflowContext) -> dict[str, Any]:
        has_ark = bool(ctx.artifacts.get("has_ark"))
        preview = bool(self.args.preview_publish) or (not has_ark)

        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "publish_pipeline.py"),
            "--title-file",
            str(ctx.artifacts["title_file"]),
            "--content-file",
            str(ctx.artifacts["content_file"]),
            "--images",
            *ctx.artifacts["generated_paths"],
            "--host",
            self.args.host,
            "--port",
            str(int(self.args.port)),
            "--reuse-existing-tab",
            "--timing-jitter",
            "0.25",
        ]
        if self.args.account:
            cmd.extend(["--account", self.args.account])
        if (self.args.product_name or "").strip():
            cmd.extend(["--product-name", self.args.product_name.strip()])
        if (self.args.product_id or "").strip():
            cmd.extend(["--product-id", self.args.product_id.strip()])
        if bool((self.args.product_name or "").strip() or (self.args.product_id or "").strip()):
            cmd.append("--click-add-product")
        if preview:
            cmd.append("--preview")

        if bool(getattr(self.args, "dry_run", False)):
            return {
                "dry_run": True,
                "preview": preview,
                "images_count": len(ctx.artifacts.get("generated_paths") or []),
                "note": "Step C publish call skipped in dry-run mode.",
                "command": cmd,
            }

        cp = subprocess.run(cmd, cwd=str(SCRIPT_DIR), capture_output=True, text=True, errors="replace")
        sys.stdout.write(cp.stdout)
        sys.stderr.write(cp.stderr)
        _sr = next((json.loads(l[12:]) for l in cp.stdout.splitlines() if l.startswith("STEP_RESULT:")), {})
        if cp.returncode != 0:
            raise StepFailure(
                str((_sr.get("error") or {}).get("message", "publish_pipeline failed")),
                code=2,
                detail={"returncode": int(cp.returncode), "preview": preview, "step_result": _sr},
            )
        return {"preview": preview, "images_count": len(ctx.artifacts["generated_paths"]), "step_result": _sr}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Class-based orchestrated full-stack workflow.")
    parser.add_argument("--product-images", nargs="+", required=False)
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
    parser.add_argument("--reference-count", type=int, default=12)
    parser.add_argument("--max-download", type=int, default=1)
    parser.add_argument("--picset-batch-size", type=int, default=1)
    parser.add_argument("--generate-timeout", type=int, default=900)
    parser.add_argument("--search-feed-timeout", type=int, default=120)
    parser.add_argument("--discover-max-probes", type=int, default=4)
    parser.add_argument("--visual-step-timeout", type=int, default=0)
    parser.add_argument("--brief-file", default=None)
    parser.add_argument("--brief", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--account", default=None)
    parser.add_argument("--product-name", default="", help="Optional target product name for publish step.")
    parser.add_argument("--product-id", default="", help="Optional target product id for publish step.")
    parser.add_argument("--picset-url", default="https://picsetai.com/zh-CN")
    parser.add_argument(
        "--from-step",
        choices=STEP_ORDER,
        default="a",
        help="Start from step id: a=visual, b=copywriting, c=publish.",
    )
    parser.add_argument(
        "--to-step",
        choices=STEP_ORDER,
        default="c",
        help="End at step id: a=visual, b=copywriting, c=publish.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run selected steps in preflight mode without external publish/generate calls.",
    )
    parser.add_argument(
        "--generated-images",
        nargs="*",
        default=None,
        help="Optional generated image paths for starting from step b/c.",
    )
    parser.add_argument(
        "--title-file",
        default=None,
        help="Optional title file path when starting from step c.",
    )
    parser.add_argument(
        "--content-file",
        default=None,
        help="Optional content file path when starting from step c.",
    )
    parser.add_argument("--preview-publish", action="store_true")
    parser.add_argument("--force-publish", action="store_true")
    parser.add_argument("--allow-placeholder-preview", action="store_true")
    parser.add_argument("--step-a-retries", type=int, default=1)
    parser.add_argument(
        "--skip-visual-keyword-discover",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--photoshop-after-generate", action="store_true")
    parser.add_argument(
        "--reuse-last-generated",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--strict-step-lock",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--workflow-report-out",
        default=None,
        help="Optional JSON path for step-level report.",
    )
    parser.add_argument(
        "--state-out",
        default=None,
        help="Optional JSON path for incremental state output (default: tmp/state_<run_id>.json).",
    )
    args = parser.parse_args()
    if args.from_step == "a" and not args.product_images:
        parser.error("--product-images is required when --from-step includes step A.")
    if _step_index(args.from_step) > _step_index(args.to_step):
        parser.error("--from-step cannot be after --to-step.")
    return args


def main() -> None:
    args = _parse_args()
    run_id = f"orchestrated_{int(time.time() * 1000)}"
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = TMP_DIR / "last_picset_summary.json"
    promo_dir = TMP_DIR / "xhs_promo_out"
    state_path = (
        Path(args.state_out).resolve()
        if args.state_out
        else (TMP_DIR / f"state_{run_id}.json")
    )
    report_path = (
        Path(args.workflow_report_out).resolve()
        if args.workflow_report_out
        else (TMP_DIR / f"workflow_report_{run_id}.json")
    )
    has_ark_env = bool((os.environ.get("ARK_API_KEY") or "").strip()) and bool(
        (os.environ.get("ARK_MODEL") or "").strip()
    )
    is_dry_run = bool(args.dry_run or args.preview_publish or (not has_ark_env))
    run_mode = "dry_run" if is_dry_run else "real_run"

    product_images = args.product_images or []
    seed_keyword = (args.seed_keyword or "").strip() or _infer_seed_keyword(product_images, "商品")
    print(f"[orchestrated] run_id={run_id}")
    print(f"[orchestrated] seed_keyword={seed_keyword}")
    print(
        f"[orchestrated] step-range={args.from_step}->{args.to_step} "
        f"dry_run={'yes' if args.dry_run else 'no'}"
    )
    print(f"[orchestrated] run_mode={run_mode}")

    ctx = WorkflowContext(
        run_id=run_id,
        strict=bool(args.strict_step_lock),
        artifacts={
            "seed_keyword": seed_keyword,
            "summary_path": str(summary_path),
            "promo_dir": str(promo_dir),
        },
        meta={
            "host": args.host,
            "port": int(args.port),
            "account": args.account,
            "product_name": (args.product_name or "").strip(),
            "product_id": (args.product_id or "").strip(),
            "strict_step_lock": bool(args.strict_step_lock),
            "from_step": args.from_step,
            "to_step": args.to_step,
            "dry_run": is_dry_run,
            "run_mode": run_mode,
        },
    )
    task_input = {
        "product_images": [str(Path(p).expanduser().resolve()) for p in (args.product_images or [])],
        "seed_keyword": seed_keyword,
        "product_target": {
            "product_name": (args.product_name or "").strip(),
            "product_id": (args.product_id or "").strip(),
        },
        "host": args.host,
        "port": int(args.port),
        "account": args.account,
        "run_mode": run_mode,
        "from_step": args.from_step,
        "to_step": args.to_step,
    }
    want = max(1, int(args.max_download))
    if _step_index(args.from_step) >= _step_index("b"):
        if args.generated_images:
            preload = dedupe_existing_paths_by_hash(
                [str(Path(p).expanduser().resolve()) for p in args.generated_images]
            )
            ctx.artifacts["generated_paths"] = preload[:want]
        else:
            preload = _load_reusable_generated_paths(summary_path, want)
            if preload:
                ctx.artifacts["generated_paths"] = preload
        if not ctx.artifacts.get("generated_paths"):
            raise SystemExit(
                "No generated images found for step b/c. "
                "Run from step A first, or pass --generated-images."
            )

    if _step_index(args.from_step) >= _step_index("c"):
        title_src = Path(args.title_file).resolve() if args.title_file else (promo_dir / "title.txt")
        content_src = (
            Path(args.content_file).resolve() if args.content_file else (promo_dir / "content.txt")
        )
        if not title_src.is_file() or not content_src.is_file():
            raise SystemExit(
                "Missing title/content files for step C. "
                "Run step B first, or pass --title-file and --content-file."
            )
        ctx.artifacts["title_file"] = str(title_src)
        ctx.artifacts["content_file"] = str(content_src)
        if "has_ark" not in ctx.artifacts:
            ctx.artifacts["has_ark"] = bool((os.environ.get("ARK_API_KEY") or "").strip()) and bool(
                (os.environ.get("ARK_MODEL") or "").strip()
            )

    runner = WorkflowRunner(strict=bool(args.strict_step_lock))
    all_steps: list[WorkflowStep] = [
        StepAVisualGenerate(args),
        StepBCopywriting(args),
        StepCPublish(args),
    ]
    steps = _pick_steps(all_steps, from_step=args.from_step, to_step=args.to_step)
    reports: list[Any] = []
    exit_code = 0
    _write_state_json(
        state_path,
        run_id=run_id,
        run_mode=run_mode,
        task_input=task_input,
        ctx=ctx,
        selected_steps=steps,
        reports=reports,
    )
    for step in steps:
        step_exit, chunk = runner.execute([step], ctx)
        if chunk:
            reports.extend(chunk)
        if step_exit != 0 and exit_code == 0:
            exit_code = int(step_exit)
        _write_state_json(
            state_path,
            run_id=run_id,
            run_mode=run_mode,
            task_input=task_input,
            ctx=ctx,
            selected_steps=steps,
            reports=reports,
        )
        if step_exit != 0 and bool(args.strict_step_lock):
            break
    runner.write_report(report_path, ctx, reports, exit_code)
    print(f"[orchestrated] report -> {report_path}")
    print(f"[orchestrated] state -> {state_path}")

    for r in reports:
        print(
            f"[orchestrated] {r.name}: {r.status} ({r.duration_ms}ms)",
            flush=True,
        )
        if r.error:
            print(f"[orchestrated]   error: {r.error.get('message')}", file=sys.stderr)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
