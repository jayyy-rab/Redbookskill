"""Strict dry-run runner for XHS Step1~Step10 with hard step gate.

Rules implemented:
1) Execute steps in order.
2) Only status=success can continue.
3) failed/manual_review/stopped blocks all downstream steps.
4) Downstream steps are marked skipped with reason=blocked_by_previous_failure.
5) No real publish (always preview mode).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from workflow_core import (
    StepErrorPayload,
    StepEvidence,
    StepResult as ContractStepResult,
    StepSuccessCheck,
)
from workflow_io import (
    append_failure_record,
    build_failure_record,
    read_step_result,
    write_step_result,
)
from workflow_status import (
    TERMINAL_BLOCK_STATUSES,
    derive_overall_status,
    next_action_for_numbered_flow,
)


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
BANNED_TERMS = {"100%", "永久有效", "全网第一", "最强", "必买"}
XHS_SORT_BY = "\u6700\u591a\u70b9\u8d5e"
XHS_NOTE_TYPE = "\u56fe\u6587"
XHS_PUBLISH_TIME = "\u4e00\u5468\u5185"


@dataclass
class StepResult:
    step: int
    name: str
    status: str
    reason: str
    logs: list[str]
    files: list[str]
    functions: list[str]
    screenshot: str | None = None
    fix_suggestion: str = ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(cmd: list[str], log_path: Path, timeout: int = 0) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=(timeout if timeout > 0 else None),
    )
    log_body = (
        f"$ {' '.join(cmd)}\n"
        f"\n--- stdout ---\n{cp.stdout or ''}"
        f"\n--- stderr ---\n{cp.stderr or ''}"
        f"\n--- returncode ---\n{cp.returncode}\n"
    )
    _write_text(log_path, log_body)
    return cp


def _parse_search_result(raw_stdout: str) -> dict[str, Any] | None:
    marker = "SEARCH_FEEDS_RESULT:"
    if marker not in raw_stdout:
        return None
    try:
        payload = raw_stdout.split(marker, 1)[1].strip()
        return json.loads(payload)
    except Exception:
        return None


def _parse_product_select_evidence(raw_output: str) -> dict[str, Any] | None:
    marker = "PRODUCT_SELECT_EVIDENCE_JSON:"
    if marker not in raw_output:
        return None
    lines = raw_output.splitlines()
    for line in lines:
        ln = line.strip()
        if not ln.startswith(marker):
            continue
        payload = ln[len(marker):].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def _is_readable_image(path: Path) -> bool:
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def _skip(step: int, name: str, reason: str) -> StepResult:
    return StepResult(
        step=step,
        name=name,
        status="skipped",
        reason=reason,
        logs=[],
        files=[],
        functions=[],
        fix_suggestion="Fix previous failed step first, then resume from Step {}.".format(step),
    )


def _derive_final_status(steps: list[StepResult]) -> str:
    return derive_overall_status(
        [s.status for s in steps],
        allow_skipped_as_success=True,
    )


def _next_action(steps: list[StepResult]) -> str:
    if not steps:
        return next_action_for_numbered_flow(
            has_steps=False,
            last_step=0,
            last_status="pending",
            start_step=1,
            max_step=10,
        )
    last = max(steps, key=lambda s: int(s.step))
    return next_action_for_numbered_flow(
        has_steps=True,
        last_step=int(last.step),
        last_status=str(last.status),
        start_step=1,
        max_step=10,
    )


def run_strict_dryrun(args: argparse.Namespace) -> dict[str, Any]:
    run_id = f"dryrun_step10_{int(time.time() * 1000)}"
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    contract_steps_dir = run_dir / "step_results"
    contract_failures_path = run_dir / "failure_records.json"
    run_mode = "dry_run"
    product_target = {
        "product_name": (args.product_name or "").strip(),
        "product_id": (args.product_id or "").strip(),
    }

    report_steps: list[StepResult] = []
    pending_fixes: list[dict[str, Any]] = []
    blocked = False
    blocked_reason = ""
    context_data: dict[str, Any] = {
        "product_target": product_target,
        "product_name": str(product_target.get("product_name") or ""),
        "product_id": str(product_target.get("product_id") or ""),
        "product_image_path": str(Path(args.product_image).resolve()),
        "reference_images": [],
        "generated_images": [],
        "copy": {},
        "product_match_evidence": {},
        "step_contract_data": {},
        "run_mode": run_mode,
        "dry_run": True,
        # 第五阶段：嵌套结构（双写兼容，旧字段保留）
        "input": {
            "product_target": dict(product_target),
        },
        "assets": {
            "product_image_path": str(Path(args.product_image).resolve()),
        },
        "product": {
            "name": str(product_target.get("product_name") or ""),
            "id": str(product_target.get("product_id") or ""),
        },
        "state": {
            "run_mode": run_mode,
            "dry_run": True,
        },
        "evidence": {},
        "error": {},
    }
    task_input = {
        "product_image": str(Path(args.product_image).resolve()),
        "keyword": args.keyword,
        "product_target": product_target,
        "account": args.account,
        "host": args.host,
        "port": args.port,
        "run_mode": run_mode,
        "dry_run": True,
    }

    def _append_pending_fix(step_no: int, step_name: str, reason: str) -> None:
        if any(int(x.get("step", -1)) == int(step_no) for x in pending_fixes):
            return
        pending_fixes.append(
            {
                "step": step_no,
                "name": step_name,
                "reason": reason,
                "status": "pending_fix",
                "created_at": _now(),
            }
        )

    def block(step_no: int, step_name: str, reason: str) -> None:
        nonlocal blocked, blocked_reason
        blocked = True
        blocked_reason = "blocked_by_previous_failure"
        _append_pending_fix(step_no, step_name, reason)

    def _read_contract_results() -> list[ContractStepResult]:
        out: list[ContractStepResult] = []
        files = sorted(contract_steps_dir.glob("step_*.json"))
        for p in files:
            try:
                out.append(read_step_result(p))
            except Exception:
                continue
        return out

    def _build_contract_summary() -> dict[str, Any]:
        items = _read_contract_results()
        statuses = [str(x.status) for x in items]
        by_status: dict[str, int] = {}
        for s in statuses:
            by_status[s] = int(by_status.get(s, 0)) + 1
        last_item = items[-1] if items else None
        return {
            "total_steps_written": len(items),
            "status_counts": by_status,
            "current_status": (
                derive_overall_status(statuses, allow_skipped_as_success=False)
                if statuses
                else "pending"
            ),
            "blocked": any(s in TERMINAL_BLOCK_STATUSES for s in statuses),
            "last_step_index": (int(last_item.step_id[4:]) if last_item and last_item.step_id.startswith("step") else 0),
            "last_step_name": (str(last_item.step_name) if last_item else ""),
            "last_step_status": (str(last_item.status) if last_item else "pending"),
        }

    def should_skip_next_step() -> str | None:
        nonlocal blocked, blocked_reason
        if blocked:
            if not blocked_reason:
                blocked_reason = "blocked_by_previous_failure"
            return blocked_reason
        for item in _read_contract_results():
            if str(item.status) in TERMINAL_BLOCK_STATUSES:
                blocked = True
                blocked_reason = "blocked_by_previous_failure"
                _append_pending_fix(
                    int(item.step_id[4:]),
                    str(item.step_name or f"step_{int(item.step_id[4:])}"),
                    str(item.error.message or "step_failed"),
                )
                return blocked_reason
        for item in report_steps:
            if item.status in TERMINAL_BLOCK_STATUSES:
                blocked = True
                blocked_reason = "blocked_by_previous_failure"
                _append_pending_fix(item.step, item.name, item.reason)
                return blocked_reason
        return None

    def _write_state_snapshot() -> None:
        payload = {
            "task_id": run_id,
            "run_mode": run_mode,
            "updated_at": _now(),
            "current_step": int(report_steps[-1].step) if report_steps else 0,
            "current_status": _derive_final_status(report_steps),
            "next_action": _next_action(report_steps),
            "input": task_input,
            "context": context_data,
            "blocked": blocked,
            "blocked_reason": blocked_reason,
            "steps": [asdict(s) for s in report_steps],
            "pending_fix_list": pending_fixes,
            "step_results_dir": str(contract_steps_dir),
            "failure_records_path": str(contract_failures_path),
            "contract_summary": _build_contract_summary(),
        }
        _write_text(state_path, json.dumps(payload, ensure_ascii=False, indent=2))

    def _status_error_code(status: str) -> str:
        s = str(status or "")
        if s == "failed":
            return "STEP_FAILED"
        if s == "manual_review":
            return "STEP_MANUAL_REVIEW"
        if s == "stopped":
            return "STEP_STOPPED"
        if s == "skipped":
            return "STEP_SKIPPED"
        return ""

    def _to_contract_result(result: StepResult) -> ContractStepResult:
        status = str(result.status or "")
        passed = status == "success"
        next_step: str | None = None
        if passed and int(result.step) < 10:
            next_step = f"run_step_{int(result.step) + 1}"
        step_idx = int(result.step)
        step_data = (context_data.get("step_contract_data") or {}).get(str(step_idx), {})

        contract_input = {
            "account_id": str(args.account or ""),
            "search_keyword": str(args.keyword or ""),
            "product_target": dict(product_target),
            "run_mode": run_mode,
        }
        contract_output = {
            "reason": str(result.reason or ""),
            "logs": list(result.logs or []),
            "files": list(result.files or []),
            "functions": list(result.functions or []),
            "fix_suggestion": str(result.fix_suggestion or ""),
        }
        success_items = ["status_is_success"] if passed else [f"status_is_{status}"]

        if step_idx == 1:
            contract_input = {
                "task_id": run_id,
                "account_id": str(args.account or ""),
                "product_image_path": str(Path(args.product_image).resolve()),
                "product_name": str(product_target.get("product_name") or ""),
                "product_id": str(product_target.get("product_id") or ""),
                "search_keyword": str(args.keyword or ""),
                "run_mode": run_mode,
            }
            contract_output = {
                "validated_product_image_path": str(step_data.get("validated_product_image_path") or ""),
                "input_problems": list(step_data.get("input_problems") or []),
                "image_ext": str(step_data.get("image_ext") or ""),
                "image_size_bytes": int(step_data.get("image_size_bytes") or 0),
                "input_check_log_path": str(step_data.get("input_check_log_path") or ""),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 2:
            contract_input = {
                "account_id": str(args.account or ""),
                "search_keyword": str(args.keyword or ""),
                "sort_by": XHS_SORT_BY,
                "note_type": XHS_NOTE_TYPE,
                "publish_time": XHS_PUBLISH_TIME,
                "host": str(args.host or ""),
                "port": int(args.port),
            }
            contract_output = {
                "search_result_count": int(step_data.get("search_result_count") or 0),
                "search_result_list_path": str(step_data.get("search_result_list_path") or ""),
                "search_log_path": str(step_data.get("search_log_path") or ""),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 3:
            ref_list = list(step_data.get("reference_image_path_list") or [])
            contract_input = {
                "account_id": str(args.account or ""),
                "search_keyword": str(args.keyword or ""),
                "max_reference_images": 2,
                "host": str(args.host or ""),
                "port": int(args.port),
            }
            contract_output = {
                "reference_image_path_list": ref_list,
                "reference_image_path": str(ref_list[0]) if ref_list else "",
                "reference_image_count": int(step_data.get("reference_image_count") or len(ref_list)),
                "reference_summary_json_path": str(step_data.get("reference_summary_json_path") or ""),
                "reference_download_log_path": str(step_data.get("reference_download_log_path") or ""),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 4:
            ref_list = list(step_data.get("reference_image_path_list") or [])
            contract_input = {
                "account_id": str(args.account or ""),
                "reference_source_summary_path": str(step_data.get("reference_source_summary_path") or ""),
                "host": str(args.host or ""),
                "port": int(args.port),
            }
            contract_output = {
                "reference_image_path_list": ref_list,
                "reference_image_count": int(step_data.get("reference_image_count") or len(ref_list)),
                "step4to7_summary_path": str(step_data.get("step4to7_summary_path") or ""),
                "step4to7_log_path": str(step_data.get("step4to7_log_path") or ""),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 5:
            product_list = list(step_data.get("product_material_path_list") or [])
            contract_input = {
                "account_id": str(args.account or ""),
                "product_image_path": str(step_data.get("product_image_path") or ""),
                "prompt_text": str(step_data.get("prompt_text") or ""),
                "host": str(args.host or ""),
                "port": int(args.port),
            }
            contract_output = {
                "product_material_path_list": product_list,
                "product_material_count": int(step_data.get("product_material_count") or len(product_list)),
                "step4to7_summary_path": str(step_data.get("step4to7_summary_path") or ""),
                "step4to7_log_path": str(step_data.get("step4to7_log_path") or ""),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 6:
            generated_list = list(step_data.get("generated_image_path_list") or [])
            contract_input = {
                "account_id": str(args.account or ""),
                "prompt_text": str(step_data.get("prompt_text") or ""),
                "generate_timeout_sec": int(step_data.get("generate_timeout_sec") or 360),
                "host": str(args.host or ""),
                "port": int(args.port),
            }
            contract_output = {
                "generated_image_path_list": generated_list,
                "generated_image_count": int(step_data.get("generated_image_count") or len(generated_list)),
                "step4to7_summary_path": str(step_data.get("step4to7_summary_path") or ""),
                "step4to7_log_path": str(step_data.get("step4to7_log_path") or ""),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 7:
            colored_list = list(step_data.get("colored_image_path_list") or [])
            contract_input = {
                "account_id": str(args.account or ""),
                "generated_image_path_list": list(step_data.get("generated_image_path_list") or []),
                "autocolor_enabled": bool(step_data.get("autocolor_enabled")),
                "host": str(args.host or ""),
                "port": int(args.port),
            }
            contract_output = {
                "colored_image_path_list": colored_list,
                "colored_image_count": int(step_data.get("colored_image_count") or len(colored_list)),
                "autocolor_ok": bool(step_data.get("autocolor_ok")),
                "step4to7_summary_path": str(step_data.get("step4to7_summary_path") or ""),
                "step4to7_log_path": str(step_data.get("step4to7_log_path") or ""),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 8:
            contract_input = {
                "account_id": str(args.account or ""),
                "search_keyword": str(step_data.get("search_keyword") or ""),
                "product_name": str(step_data.get("product_name") or ""),
                "copy_provider": str(step_data.get("copy_provider") or ""),
                "candidate_image_path": str(step_data.get("candidate_image_path") or ""),
                "dry_run": bool(step_data.get("dry_run")),
            }
            contract_output = {
                "title_file": str(step_data.get("title_file") or ""),
                "content_file": str(step_data.get("content_file") or ""),
                "body_chars_no_newline": int(step_data.get("body_chars_no_newline") or 0),
                "banned_hits": list(step_data.get("banned_hits") or []),
                "copy_quality_json_path": str(step_data.get("copy_quality_json_path") or ""),
                "copy_log_path": str(step_data.get("copy_log_path") or ""),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 9:
            contract_input = {
                "account_id": str(args.account or ""),
                "title_file": str(step_data.get("title_file") or ""),
                "content_file": str(step_data.get("content_file") or ""),
                "image_file": str(step_data.get("image_file") or ""),
                "dry_run": bool(step_data.get("dry_run")),
                "host": str(args.host or ""),
                "port": int(args.port),
            }
            contract_output = {
                "fill_status_ready_to_publish": bool(step_data.get("fill_status_ready_to_publish")),
                "fill_preview_log_path": str(step_data.get("fill_preview_log_path") or ""),
                "fill_command_return_code": int(step_data.get("fill_command_return_code") or 0),
            }
            success_items = list(step_data.get("success_items") or success_items)
        elif step_idx == 10:
            contract_input = {
                "account_id": str(args.account or ""),
                "product_name": str(step_data.get("product_name") or ""),
                "product_id": str(step_data.get("product_id") or ""),
                "title_file": str(step_data.get("title_file") or ""),
                "content_file": str(step_data.get("content_file") or ""),
                "image_file": str(step_data.get("image_file") or ""),
                "dry_run": bool(step_data.get("dry_run")),
                "host": str(args.host or ""),
                "port": int(args.port),
            }
            contract_output = {
                "product_select_verified": bool(step_data.get("product_select_verified")),
                "product_select_manual_review": bool(step_data.get("product_select_manual_review")),
                "product_select_legacy_selected": bool(step_data.get("product_select_legacy_selected")),
                "product_match_rule": str(step_data.get("product_match_rule") or ""),
                "product_match_reason": str(step_data.get("product_match_reason") or ""),
                "product_select_log_path": str(step_data.get("product_select_log_path") or ""),
                "product_select_evidence": dict(step_data.get("product_select_evidence") or {}),
            }
            success_items = list(step_data.get("success_items") or success_items)

        contract = ContractStepResult(
            task_id=run_id,
            step_id=f"step{step_idx}",
            step_name=str(result.name or ""),
            status=status,
            evidence=StepEvidence(
                screenshot=str(result.screenshot or ""),
            ),
            artifacts={
                "input": contract_input,
                "output": contract_output,
                "next_step": next_step,
                "updated_state": {
                    "current_step": int(result.step),
                    "current_status": status,
                },
            },
            success_check=StepSuccessCheck(
                passed=passed,
                items=success_items,
            ),
            error=StepErrorPayload(
                code=_status_error_code(status),
                message=("" if passed else str(result.reason or "")),
                detail=("" if passed else {"status": status}),
            ),
            retry_count=0,
            created_files=list(result.logs or []),
        )
        return contract

    def add_step(result: StepResult) -> None:
        report_steps.append(result)
        if result.status in TERMINAL_BLOCK_STATUSES:
            _append_pending_fix(result.step, result.name, result.reason)
        contract = _to_contract_result(result)
        step_json = contract_steps_dir / f"step_{int(result.step):02d}_{str(result.status)}.json"
        write_step_result(step_json, contract)
        if str(result.status) in TERMINAL_BLOCK_STATUSES:
            rec = build_failure_record(
                task_id=run_id,
                step_index=int(result.step),
                step_name=str(result.name or ""),
                error_code=_status_error_code(str(result.status)),
                error_message=str(result.reason or ""),
                error_detail={
                    "logs": list(result.logs or []),
                    "files": list(result.files or []),
                    "functions": list(result.functions or []),
                },
                status="pending_fix",
            )
            append_failure_record(contract_failures_path, rec)
        _write_state_snapshot()

    # Step 1: input validate
    step = 1
    name = "鐢ㄦ埛杈撳叆鏍￠獙"
    img = Path(args.product_image)
    problems: list[str] = []
    if not img.is_file():
        problems.append("浜у搧鍥句笉瀛樺湪")
    else:
        if img.suffix.lower() not in ALLOWED_IMAGE_EXTS:
            problems.append("Unsupported image format")
        if img.stat().st_size <= 10 * 1024:
            problems.append("鍥剧墖鏂囦欢灏忎簬10KB")
        if not _is_readable_image(img):
            problems.append("鍥剧墖涓嶅彲璇诲彇")
    if not product_target["product_name"] and not product_target["product_id"]:
        problems.append("鍟嗗搧鍚嶇О鍜?鍟嗗搧ID鑷冲皯涓€椤归渶鎻愪緵")
    if not (args.keyword or "").strip():
        problems.append("Keyword is empty")

    step1_log = run_dir / "step1_input_check.json"
    _write_text(
        step1_log,
        json.dumps(
            {
                "product_image": str(img),
                "product_name": args.product_name,
                "keyword": args.keyword,
                "problems": problems,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    context_data["step_contract_data"]["1"] = {
        "validated_product_image_path": str(img.resolve()) if img.exists() else "",
        "input_problems": list(problems),
        "image_ext": str(img.suffix.lower()) if img.exists() else "",
        "image_size_bytes": (int(img.stat().st_size) if img.exists() else 0),
        "input_check_log_path": str(step1_log),
        "success_items": (
            ["file_exists", "format_ok", "size_gt_10kb", "readable", "product_target_ok", "keyword_ok"]
            if not problems
            else ["input_validation_failed"]
        ),
    }
    if problems:
        reason = "; ".join(problems)
        add_step(
            StepResult(
                step=step,
                name=name,
                status="failed",
                reason=reason,
                logs=[str(step1_log)],
                files=[str(img)],
                functions=["scripts/dryrun_step1_10_runner.py::run_strict_dryrun"],
                screenshot=str(img) if img.exists() else None,
                fix_suggestion="Complete required inputs, then rerun Step1.",
            )
        )
        block(step, name, reason)
    else:
        add_step(
            StepResult(
                step=step,
                name=name,
                status="success",
                reason="Input is complete and readable.",
                logs=[str(step1_log)],
                files=[str(img)],
                functions=["scripts/dryrun_step1_10_runner.py::run_strict_dryrun"],
                screenshot=str(img),
            )
        )

    # Step 2: search keyword
    step = 2
    name = "灏忕孩涔︽悳绱㈠叧閿瘝"
    gate_reason = should_skip_next_step()
    if gate_reason:
        add_step(_skip(step, name, gate_reason))
    else:
        step2_log = run_dir / "step2_search.log"
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "cdp_publish.py"),
            "--account",
            args.account,
            "--host",
            args.host,
            "--port",
            str(args.port),
            "search-feeds",
            "--keyword",
            args.keyword,
            "--sort-by",
            XHS_SORT_BY,
            "--note-type",
            XHS_NOTE_TYPE,
            "--publish-time",
            XHS_PUBLISH_TIME,
        ]
        cp = _run(cmd, step2_log, timeout=300)
        parsed = _parse_search_result(cp.stdout or "")
        feed_count = int((parsed or {}).get("count", 0))
        parsed_path = run_dir / "step2_search_result.json"
        _write_text(parsed_path, json.dumps(parsed or {}, ensure_ascii=False, indent=2))
        context_data["step_contract_data"]["2"] = {
            "search_result_count": int(feed_count),
            "search_result_list_path": str(parsed_path),
            "search_log_path": str(step2_log),
            "success_items": (
                ["search_command_ok", "search_result_count_gt_zero"]
                if (cp.returncode == 0 and feed_count > 0)
                else ["search_failed_or_empty"]
            ),
        }
        if cp.returncode != 0 or feed_count <= 0:
            reason = f"鎼滅储澶辫触(returncode={cp.returncode}, feed_count={feed_count})"
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="failed",
                    reason=reason,
                    logs=[str(step2_log), str(parsed_path)],
                    files=[],
                    functions=["scripts/cdp_publish.py::search_feeds"],
                    fix_suggestion="Check login state, keyword, and page risk-control conditions before retry.",
                )
            )
            block(step, name, reason)
        else:
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="success",
                    reason=f"Search succeeded, got {feed_count} results.",
                    logs=[str(step2_log), str(parsed_path)],
                    files=[],
                    functions=["scripts/cdp_publish.py::search_feeds"],
                )
            )

    # Step 3: filter + download refs
    step = 3
    name = "绛涢€夊苟涓嬭浇鍙傝€冨浘"
    step3_refs = run_dir / "step3_refs"
    step3_summary = run_dir / "step3_summary.json"
    gate_reason = should_skip_next_step()
    if gate_reason:
        add_step(_skip(step, name, gate_reason))
    else:
        step3_log = run_dir / "step3_download.log"
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "xhs_images_to_picset.py"),
            "--keyword",
            args.keyword,
            "--sort-by",
            XHS_SORT_BY,
            "--publish-time",
            XHS_PUBLISH_TIME,
            "--note-type",
            XHS_NOTE_TYPE,
            "--limit-notes",
            "8",
            "--max-images",
            "2",
            "--output-dir",
            str(step3_refs),
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--account",
            args.account,
            "--skip-upload",
            "--strict-step-lock",
            "--summary-json",
            str(step3_summary),
        ]
        cp = _run(cmd, step3_log, timeout=600)
        ok_files: list[str] = []
        # xhs_images_to_picset in --skip-upload mode may not always emit summary-json.
        # Validate by actual downloaded files first, then merge summary paths when present.
        for pp in sorted(step3_refs.glob("*")):
            if pp.is_file() and pp.stat().st_size > 10 * 1024 and _is_readable_image(pp):
                ok_files.append(str(pp))
        if step3_summary.is_file():
            try:
                data = json.loads(step3_summary.read_text(encoding="utf-8"))
                for p in data.get("local_paths", []):
                    pp = Path(p)
                    sp = str(pp)
                    if pp.is_file() and pp.stat().st_size > 10 * 1024 and _is_readable_image(pp) and sp not in ok_files:
                        ok_files.append(sp)
            except Exception:
                pass
        if cp.returncode != 0 or not ok_files:
            reason = f"鍙傝€冨浘涓嬭浇/鏍￠獙澶辫触(returncode={cp.returncode}, valid_files={len(ok_files)})"
            context_data["step_contract_data"]["3"] = {
                "reference_image_path_list": list(ok_files),
                "reference_image_count": int(len(ok_files)),
                "reference_summary_json_path": str(step3_summary),
                "reference_download_log_path": str(step3_log),
                "success_items": ["reference_download_or_validation_failed"],
            }
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="failed",
                    reason=reason,
                    logs=[str(step3_log), str(step3_summary)],
                    files=ok_files,
                    functions=["scripts/xhs_images_to_picset.py::run_search_covers", "scripts/xhs_images_to_picset.py::main"],
                    fix_suggestion="Check download chain, image validation, and network stability.",
                )
            )
            block(step, name, reason)
        else:
            context_data["step_contract_data"]["3"] = {
                "reference_image_path_list": list(ok_files),
                "reference_image_count": int(len(ok_files)),
                "reference_summary_json_path": str(step3_summary),
                "reference_download_log_path": str(step3_log),
                "success_items": ["reference_files_exist", "reference_files_readable", "reference_count_gt_zero"],
            }
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="success",
                    reason=f"Downloaded and validated: valid_files={len(ok_files)}.",
                    logs=[str(step3_log), str(step3_summary)],
                    files=ok_files[:3],
                    functions=["scripts/xhs_images_to_picset.py::run_search_covers", "scripts/xhs_images_to_picset.py::main"],
                )
            )
            context_data["reference_images"] = ok_files

    # Step 4-7 combined generator run
    step4to7_summary = run_dir / "step4to7_summary.json"
    step4to7_log = run_dir / "step4to7_generate.log"
    step4_names = {
        4: "瀵煎叆鍙傝€冭璁″浘",
        5: "濉厖浜у搧绱犳潗涓庢彁绀鸿瘝",
        6: "鐢熸垚鏂扮殑鍥剧墖",
        7: "鑷姩璋冭壊",
    }
    step4to7_ok = False
    step4to7_data: dict[str, Any] = {}
    generate_prompt_text = "Use reference style plus product material to generate one clean and natural XHS detail image."
    context_data["step_contract_data"]["4"] = {
        "reference_source_summary_path": str(step4to7_summary),
        "reference_image_path_list": [],
        "reference_image_count": 0,
        "step4to7_summary_path": str(step4to7_summary),
        "step4to7_log_path": str(step4to7_log),
        "success_items": ["reference_import_not_executed_yet"],
    }
    context_data["step_contract_data"]["5"] = {
        "product_image_path": str(img.resolve()),
        "prompt_text": generate_prompt_text,
        "product_material_path_list": [],
        "product_material_count": 0,
        "step4to7_summary_path": str(step4to7_summary),
        "step4to7_log_path": str(step4to7_log),
        "success_items": ["product_material_fill_not_executed_yet"],
    }
    context_data["step_contract_data"]["6"] = {
        "prompt_text": generate_prompt_text,
        "generate_timeout_sec": 360,
        "generated_image_path_list": [],
        "generated_image_count": 0,
        "step4to7_summary_path": str(step4to7_summary),
        "step4to7_log_path": str(step4to7_log),
        "success_items": ["generate_action_not_executed_yet"],
    }
    context_data["step_contract_data"]["7"] = {
        "generated_image_path_list": [],
        "autocolor_enabled": True,
        "autocolor_ok": False,
        "colored_image_path_list": [],
        "colored_image_count": 0,
        "step4to7_summary_path": str(step4to7_summary),
        "step4to7_log_path": str(step4to7_log),
        "success_items": ["autocolor_not_executed_yet"],
    }
    gate_reason = should_skip_next_step()
    if gate_reason:
        for s in (4, 5, 6, 7):
            add_step(_skip(s, step4_names[s], gate_reason))
    else:
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "xhs_images_to_picset.py"),
            "--keyword",
            args.keyword,
            "--sort-by",
            XHS_SORT_BY,
            "--publish-time",
            XHS_PUBLISH_TIME,
            "--note-type",
            XHS_NOTE_TYPE,
            "--limit-notes",
            "6",
            "--max-images",
            "1",
            "--output-dir",
            str(run_dir / "step4_refs"),
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--account",
            args.account,
            "--product-images",
            str(img),
            "--generate",
            "--prompt",
            generate_prompt_text,
            "--generate-timeout",
            "360",
            "--max-download",
            "1",
            "--picset-batch-size",
            "1",
            "--photoshop-after-generate",
            "--strict-step-lock",
            "--summary-json",
            str(step4to7_summary),
        ]
        cp = _run(cmd, step4to7_log, timeout=1800)
        if cp.returncode == 0 and step4to7_summary.is_file():
            try:
                step4to7_data = json.loads(step4to7_summary.read_text(encoding="utf-8"))
                step4to7_ok = True
            except Exception:
                step4to7_ok = False

        if not step4to7_ok:
            reason = "Image generation chain failed; cannot split Step4~Step7 evidence clearly."
            add_step(
                StepResult(
                    step=4,
                    name=step4_names[4],
                    status="failed",
                    reason=reason,
                    logs=[str(step4to7_log), str(step4to7_summary)],
                    files=[],
                    functions=["scripts/xhs_images_to_picset.py::main"],
                    fix_suggestion="Fix generation chain first, then rerun Step4~Step7.",
                )
            )
            block(4, step4_names[4], reason)
            for s in (5, 6, 7):
                add_step(_skip(s, step4_names[s], blocked_reason))
        else:
            refs = [str(p) for p in step4to7_data.get("reference_local_paths", []) if Path(p).is_file()]
            products = [str(p) for p in step4to7_data.get("product_local_paths", []) if Path(p).is_file()]
            gens = [str(p) for p in step4to7_data.get("generated_local_paths", []) if Path(p).is_file()]
            auto = step4to7_data.get("photoshop_after_generate") or {}
            context_data["generated_local_paths"] = list(gens)
            context_data["step4to7_data_snapshot"] = dict(step4to7_data)
            context_data["state"]["step4to7_snapshot"] = dict(step4to7_data)
            auto_ok = bool(auto.get("autocolor_ok"))
            auto_outputs = [str(p) for p in auto.get("outputs", []) if Path(p).is_file()]
            context_data["step_contract_data"]["4"] = {
                "reference_source_summary_path": str(step4to7_summary),
                "reference_image_path_list": refs,
                "reference_image_count": len(refs),
                "step4to7_summary_path": str(step4to7_summary),
                "step4to7_log_path": str(step4to7_log),
                "success_items": (
                    ["reference_image_exists", "reference_image_readable", "reference_imported"]
                    if refs
                    else ["reference_import_failed"]
                ),
            }
            context_data["step_contract_data"]["5"] = {
                "product_image_path": str(img.resolve()),
                "prompt_text": generate_prompt_text,
                "product_material_path_list": products,
                "product_material_count": len(products),
                "step4to7_summary_path": str(step4to7_summary),
                "step4to7_log_path": str(step4to7_log),
                "success_items": (
                    ["product_material_filled", "prompt_filled", "inputs_on_same_page_filled"]
                    if products
                    else ["product_material_or_prompt_fill_failed"]
                ),
            }
            context_data["step_contract_data"]["6"] = {
                "prompt_text": generate_prompt_text,
                "generate_timeout_sec": 360,
                "generated_image_path_list": gens,
                "generated_image_count": len(gens),
                "step4to7_summary_path": str(step4to7_summary),
                "step4to7_log_path": str(step4to7_log),
                "success_items": (
                    ["generate_button_clicked", "generated_image_saved", "generated_image_readable"]
                    if gens
                    else ["generate_failed_or_no_output"]
                ),
            }
            context_data["step_contract_data"]["7"] = {
                "generated_image_path_list": gens,
                "autocolor_enabled": True,
                "autocolor_ok": auto_ok,
                "colored_image_path_list": auto_outputs,
                "colored_image_count": len(auto_outputs),
                "step4to7_summary_path": str(step4to7_summary),
                "step4to7_log_path": str(step4to7_log),
                "success_items": (
                    ["autocolor_execute_ok", "autocolor_output_exists", "autocolor_output_readable"]
                    if (auto_ok and auto_outputs)
                    else ["autocolor_failed_or_no_output"]
                ),
            }

            add_step(
                StepResult(
                    step=4,
                    name=step4_names[4],
                    status="success" if refs else "failed",
                    reason="Reference design imported." if refs else "Reference import evidence is insufficient.",
                    logs=[str(step4to7_log), str(step4to7_summary)],
                    files=refs[:2],
                    functions=["scripts/picset_automation.py::_upload_reference_images_via_cdp"],
                    fix_suggestion="" if refs else "Check reference upload slot locator.",
                )
            )
            if not refs:
                block(4, step4_names[4], "Reference import failed")
            else:
                context_data["reference_images"] = refs

            if not blocked:
                add_step(
                    StepResult(
                        step=5,
                        name=step4_names[5],
                        status="success" if products else "failed",
                        reason="Product material and prompt filled." if products else "Product/prompt fill evidence is insufficient.",
                        logs=[str(step4to7_log), str(step4to7_summary)],
                        files=products[:2],
                        functions=[
                            "scripts/picset_automation.py::_upload_reference_images_via_cdp",
                            "scripts/picset_automation.py::_fill_prompt_and_generate",
                        ],
                        fix_suggestion="" if products else "Check product upload locator and prompt input locator.",
                    )
                )
                if not products:
                    block(5, step4_names[5], "Product material/prompt fill failed")

            if not blocked:
                add_step(
                    StepResult(
                        step=6,
                        name=step4_names[6],
                        status="success" if gens else "failed",
                        reason="Generate action triggered and new images were saved." if gens else "No generated image files detected.",
                        logs=[str(step4to7_log), str(step4to7_summary)],
                        files=gens[:2],
                        functions=[
                            "scripts/picset_automation.py::_fill_prompt_and_generate",
                            "scripts/picset_automation.py::_collect_generated_image_urls",
                        ],
                        fix_suggestion="" if gens else "Check generate button locator and generated result capture.",
                    )
                )
                if not gens:
                    block(6, step4_names[6], "Image generation failed")
                else:
                    context_data["generated_images"] = gens

            if not blocked:
                add_step(
                    StepResult(
                        step=7,
                        name=step4_names[7],
                        status="success" if (auto_ok and auto_outputs) else "failed",
                        reason="Autocolor produced valid outputs." if (auto_ok and auto_outputs) else "Autocolor did not produce valid files.",
                        logs=[str(step4to7_log), str(step4to7_summary)],
                        files=auto_outputs[:2],
                        functions=["scripts/xhs_image_autofix.py::pillow_post_adjust"],
                        fix_suggestion="" if (auto_ok and auto_outputs) else "Check autocolor input/output paths and Pillow processing chain.",
                    )
                )
                if not (auto_ok and auto_outputs):
                    block(7, step4_names[7], "Autocolor failed")

    # Step 8 copy generation quality gate
    step = 8
    name = "Generate XHS copy"
    step8_dir = run_dir / "step8_copy"
    step8_log = run_dir / "step8_copy.log"
    step8_quality = run_dir / "step8_quality.json"
    context_data["step_contract_data"]["8"] = {
        "search_keyword": str(args.keyword or ""),
        "product_name": str(args.product_name or ""),
        "copy_provider": "ark",
        "candidate_image_path": "",
        "dry_run": True,
        "title_file": str(step8_dir / "title.txt"),
        "content_file": str(step8_dir / "content.txt"),
        "body_chars_no_newline": 0,
        "banned_hits": [],
        "copy_quality_json_path": str(step8_quality),
        "copy_log_path": str(step8_log),
        "success_items": ["copy_generation_not_executed_yet"],
    }
    gate_reason = should_skip_next_step()
    if gate_reason:
        add_step(_skip(step, name, gate_reason))
    else:
        candidate_image = ""
        gen_paths = context_data.get("generated_local_paths", [])
        if gen_paths:
            candidate_image = str(gen_paths[0])
        context_data["step_contract_data"]["8"]["candidate_image_path"] = str(candidate_image or "")
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "douban_promo_copy.py"),
            "--provider",
            "ark",
            "--dry-run",
            "--seed-keyword",
            args.keyword,
            "--brief",
            f"Product: {args.product_name}. Used for XHS image-text publishing.",
            "--out-dir",
            str(step8_dir),
        ]
        if candidate_image and Path(candidate_image).is_file():
            cmd.extend(["--images", candidate_image])
        cp = _run(cmd, step8_log, timeout=180)
        title_file = step8_dir / "title.txt"
        content_file = step8_dir / "content.txt"
        body_len = 0
        banned_hit: list[str] = []
        if cp.returncode == 0 and title_file.is_file() and content_file.is_file():
            txt = content_file.read_text(encoding="utf-8")
            lines = [ln for ln in txt.splitlines() if ln.strip()]
            body = "\n".join(lines[:-1]) if len(lines) >= 2 else txt.strip()
            body_len = len(body.replace("\n", ""))
            banned_hit = [w for w in BANNED_TERMS if w in txt]
        _write_text(
            step8_quality,
            json.dumps(
                {
                    "title_file": str(title_file),
                    "content_file": str(content_file),
                    "body_chars_no_newline": body_len,
                    "banned_hits": banned_hit,
                    "target": "120~180",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        ok = cp.returncode == 0 and title_file.is_file() and content_file.is_file() and (120 <= body_len <= 180) and not banned_hit
        context_data["step_contract_data"]["8"] = {
            "search_keyword": str(args.keyword or ""),
            "product_name": str(args.product_name or ""),
            "copy_provider": "ark",
            "candidate_image_path": str(candidate_image or ""),
            "dry_run": True,
            "title_file": str(title_file),
            "content_file": str(content_file),
            "body_chars_no_newline": int(body_len),
            "banned_hits": list(banned_hit),
            "copy_quality_json_path": str(step8_quality),
            "copy_log_path": str(step8_log),
            "success_items": (
                ["copy_generated", "copy_length_120_180", "copy_no_banned_terms"]
                if ok
                else ["copy_quality_failed"]
            ),
        }
        if ok:
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="success",
                    reason=f"Copy quality passed (body chars={body_len}).",
                    logs=[str(step8_log), str(step8_quality)],
                    files=[str(title_file), str(content_file)],
                    functions=["scripts/douban_promo_copy.py::main"],
                )
            )
            context_data["copy"] = {
                "title_file": str(title_file),
                "content_file": str(content_file),
                "body_chars_no_newline": body_len,
            }
        else:
            reason = f"Copy quality failed (returncode={cp.returncode}, body_chars={body_len}, banned={banned_hit})"
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="failed",
                    reason=reason,
                    logs=[str(step8_log), str(step8_quality)],
                    files=[str(title_file), str(content_file)],
                    functions=["scripts/douban_promo_copy.py::main"],
                    fix_suggestion="Use real model generation with char-count retries, or improve dry-run template quality.",
                )
            )
            block(step, name, reason)

    # Step 9 publish page fill
    step = 9
    name = "濉厖灏忕孩涔﹀彂甯冮〉"
    step9_log = run_dir / "step9_fill_preview.log"
    context_data["step_contract_data"]["9"] = {
        "title_file": str(context_data.get("step_contract_data", {}).get("8", {}).get("title_file", "") or str(run_dir / "step8_copy" / "title.txt")),
        "content_file": str(context_data.get("step_contract_data", {}).get("8", {}).get("content_file", "") or str(run_dir / "step8_copy" / "content.txt")),
        "image_file": "",
        "dry_run": True,
        "fill_status_ready_to_publish": False,
        "fill_preview_log_path": str(step9_log),
        "fill_command_return_code": 0,
        "success_items": ["publish_fill_not_executed_yet"],
    }
    gate_reason = should_skip_next_step()
    if gate_reason:
        add_step(_skip(step, name, gate_reason))
    else:
        title_file = run_dir / "step8_copy" / "title.txt"
        content_file = run_dir / "step8_copy" / "content.txt"
        gen_file = ""
        for p in (context_data.get("generated_local_paths") or []):
            if Path(p).is_file():
                gen_file = p
                break
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "publish_pipeline.py"),
            "--title-file",
            str(title_file),
            "--content-file",
            str(content_file),
            "--images",
            gen_file,
            "--preview",
            "--account",
            args.account,
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--reuse-existing-tab",
        ]
        cp = _run(cmd, step9_log, timeout=900)
        ready = "FILL_STATUS: READY_TO_PUBLISH" in ((cp.stdout or "") + (cp.stderr or ""))
        context_data["step_contract_data"]["9"] = {
            "title_file": str(title_file),
            "content_file": str(content_file),
            "image_file": str(gen_file),
            "dry_run": True,
            "fill_status_ready_to_publish": bool(ready),
            "fill_preview_log_path": str(step9_log),
            "fill_command_return_code": int(cp.returncode),
            "success_items": (
                ["preview_fill_ok", "ready_to_publish_signal_found"]
                if (cp.returncode == 0 and ready)
                else ["preview_fill_failed_or_not_ready"]
            ),
        }
        if cp.returncode == 0 and ready:
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="success",
                    reason="Publish page preview fill succeeded.",
                    logs=[str(step9_log)],
                    files=[gen_file],
                    functions=["scripts/publish_pipeline.py::main", "scripts/cdp_publish.py::publish"],
                )
            )
        else:
            reason = f"鍙戝竷椤甸濉け璐?returncode={cp.returncode}, ready={ready})"
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="failed",
                    reason=reason,
                    logs=[str(step9_log)],
                    files=[gen_file],
                    functions=["scripts/publish_pipeline.py::main", "scripts/cdp_publish.py::publish"],
                    fix_suggestion="Check publish page navigation, upload control, and editor locator.",
                )
            )
            block(step, name, reason)

    # Step 10 add product + publish (dry-run preview only)
    step = 10
    name = "Add product and publish"
    step10_log = run_dir / "step10_add_product_preview.log"
    context_data["step_contract_data"]["10"] = {
        "product_name": str(product_target.get("product_name") or ""),
        "product_id": str(product_target.get("product_id") or ""),
        "title_file": str(context_data.get("step_contract_data", {}).get("8", {}).get("title_file", "") or str(run_dir / "step8_copy" / "title.txt")),
        "content_file": str(context_data.get("step_contract_data", {}).get("8", {}).get("content_file", "") or str(run_dir / "step8_copy" / "content.txt")),
        "image_file": "",
        "dry_run": True,
        "product_select_verified": False,
        "product_select_manual_review": False,
        "product_select_legacy_selected": False,
        "product_match_rule": "",
        "product_match_reason": "",
        "product_select_log_path": str(step10_log),
        "product_select_evidence": {},
        "success_items": ["product_select_not_executed_yet"],
    }
    gate_reason = should_skip_next_step()
    if gate_reason:
        add_step(_skip(step, name, gate_reason))
    else:
        title_file = run_dir / "step8_copy" / "title.txt"
        content_file = run_dir / "step8_copy" / "content.txt"
        gen_file = ""
        for p in (context_data.get("generated_local_paths") or []):
            if Path(p).is_file():
                gen_file = p
                break
        cmd = [
            sys.executable,
            str(SCRIPT_DIR / "publish_pipeline.py"),
            "--title-file",
            str(title_file),
            "--content-file",
            str(content_file),
            "--images",
            gen_file,
            "--preview",
            "--click-add-product",
            "--product-name",
            product_target["product_name"],
            "--product-id",
            product_target["product_id"],
            "--account",
            args.account,
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--reuse-existing-tab",
        ]
        cp = _run(cmd, step10_log, timeout=900)
        out = (cp.stdout or "") + "\n" + (cp.stderr or "")
        evidence = _parse_product_select_evidence(out)
        if evidence:
            ev_status = str(evidence.get("status") or "").strip().lower()
            verified = ev_status == "verified"
            manual_review = ev_status == "manual_review"
            legacy_selected = ev_status == "legacy_selected"
        else:
            verified = "PRODUCT_SELECT_STATUS: VERIFIED" in out
            manual_review = "PRODUCT_SELECT_STATUS: MANUAL_REVIEW" in out
            legacy_selected = "PRODUCT_SELECT_STATUS: LEGACY_SELECTED" in out
        context_data["product_match_evidence"] = {
            "verified": bool(verified),
            "manual_review": bool(manual_review),
            "legacy_selected": bool(legacy_selected),
            "product_name": product_target["product_name"],
            "product_id": product_target["product_id"],
            "raw": evidence or {},
        }
        context_data["evidence"]["product_match"] = {
            "verified": bool(verified),
            "manual_review": bool(manual_review),
            "legacy_selected": bool(legacy_selected),
            "product_name": product_target["product_name"],
            "product_id": product_target["product_id"],
            "raw": evidence or {},
        }
        ev_rule = str((evidence or {}).get("rule") or "").strip()
        ev_reason = str((evidence or {}).get("reason") or "").strip()
        if not ev_rule:
            if verified:
                ev_rule = "status_verified"
            elif manual_review:
                ev_rule = "status_manual_review"
            elif legacy_selected:
                ev_rule = "status_legacy_selected"
        context_data["step_contract_data"]["10"] = {
            "product_name": str(product_target.get("product_name") or ""),
            "product_id": str(product_target.get("product_id") or ""),
            "title_file": str(title_file),
            "content_file": str(content_file),
            "image_file": str(gen_file),
            "dry_run": True,
            "product_select_verified": bool(verified),
            "product_select_manual_review": bool(manual_review),
            "product_select_legacy_selected": bool(legacy_selected),
            "product_match_rule": ev_rule,
            "product_match_reason": ev_reason,
            "product_select_log_path": str(step10_log),
            "product_select_evidence": dict(evidence or {}),
            "success_items": (
                ["product_target_verified", "ready_to_publish_without_real_submit"]
                if (cp.returncode == 0 and verified)
                else (
                    ["product_select_manual_review_required"]
                    if cp.returncode == 0
                    else ["product_select_command_failed"]
                )
            ),
        }
        if cp.returncode == 0 and verified:
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="success",
                    reason="Product target verified by exact-match evidence.",
                    logs=[str(step10_log)],
                    files=[gen_file] if gen_file else [],
                    functions=[
                        "scripts/publish_pipeline.py::main",
                        "scripts/cdp_publish.py::select_product_with_match",
                    ],
                )
            )
        elif cp.returncode == 0:
            reason = (
                "Product target is not verifiable in current run; "
                "expected PRODUCT_SELECT_STATUS: VERIFIED."
            )
            if ev_reason:
                reason = f"{reason} detail={ev_reason}"
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="manual_review",
                    reason=reason,
                    logs=[str(step10_log)],
                    files=[gen_file] if gen_file else [],
                    functions=[
                        "scripts/publish_pipeline.py::main",
                        "scripts/cdp_publish.py::select_product_with_match",
                    ],
                    fix_suggestion="Provide exact product_id or product_name and rerun Step10.",
                )
            )
        else:
            add_step(
                StepResult(
                    step=step,
                    name=name,
                    status="failed",
                    reason=f"鍛戒护澶辫触(returncode={cp.returncode})",
                    logs=[str(step10_log)],
                    files=[gen_file] if gen_file else [],
                    functions=[
                        "scripts/publish_pipeline.py::main",
                        "scripts/cdp_publish.py::select_product_with_match",
                    ],
                    fix_suggestion="Fix publish command failure, then rerun Step10.",
                )
            )
        if cp.returncode != 0:
            block(step, name, f"Step10 execution failed rc={cp.returncode}")

    payload = {
        "run_id": run_id,
        "mode": run_mode,
        "created_at": _now(),
        "input": task_input,
        "context": context_data,
        "steps": [asdict(s) for s in report_steps],
        "pending_fix_list": pending_fixes,
        "final_status": _derive_final_status(report_steps),
    }
    report_path = run_dir / "step1_10_report.json"
    _write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2))
    _write_state_snapshot()
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict dry-run runner for Step1~Step10.")
    parser.add_argument("--product-image", required=True, help="Absolute path to product image.")
    parser.add_argument("--keyword", required=True, help="XHS search keyword.")
    parser.add_argument("--product-name", default="", help="Product name to attach.")
    parser.add_argument("--product-id", default="", help="Exact product id for strict Step10 match.")
    parser.add_argument("--account", default="runner")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9322)
    parser.add_argument(
        "--run-dir",
        default=str(REPO_ROOT / "tmp" / f"dryrun_step_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = run_strict_dryrun(args)
    report_path = Path(args.run_dir).resolve() / "step1_10_report.json"
    print(f"[dryrun-step10] report={report_path}")
    print(f"[dryrun-step10] final_status={payload.get('final_status')}")


if __name__ == "__main__":
    main()


