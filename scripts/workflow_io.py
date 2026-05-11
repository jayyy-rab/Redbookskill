"""Unified IO helpers — StepResult serialization, directory setup, bug/screenshot recording."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflow_core import (
    BugRecord,
    DiagnosticsEntry,
    ErrorLevel,
    PathRegistry,
    PipelineContext,
    StepResult,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════
# StepResult 序列化（兼容新旧格式）
# ════════════════════════════════════════════════════════════


def write_step_result(path: str | Path, result: StepResult) -> Path:
    out = Path(path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def read_step_result(path: str | Path) -> StepResult:
    src = Path(path).resolve()
    payload = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("StepResult file must contain a JSON object.")
    return StepResult.from_dict(payload)


# ════════════════════════════════════════════════════════════
# 目录结构
# ════════════════════════════════════════════════════════════


def build_paths(task_id: str, account_id: str, *, run_root: str | Path = "") -> PathRegistry:
    """按 PRD 目录规范创建 PathRegistry。

    runs/{task_id}/
      input/
      accounts/{account_id}/
        logs/
        screenshots/
        artifacts/
        evidence/
    """
    root = Path(run_root).resolve() if run_root else Path.cwd() / "runs"
    task_dir = root / task_id
    account_dir = task_dir / "accounts" / account_id
    return PathRegistry(
        run_root=str(root),
        task_dir=str(task_dir),
        input_dir=str(task_dir / "input"),
        account_dir=str(account_dir),
        logs_dir=str(account_dir / "logs"),
        screenshots_dir=str(account_dir / "screenshots"),
        artifacts_dir=str(account_dir / "artifacts"),
        evidence_dir=str(account_dir / "evidence"),
    )


def ensure_task_dirs(ctx: PipelineContext) -> None:
    """创建任务目录树（幂等）。"""
    for key in ("task_dir", "input_dir", "account_dir", "logs_dir", "screenshots_dir",
                "artifacts_dir", "evidence_dir"):
        d = getattr(ctx.paths, key, "")
        if d:
            Path(d).mkdir(parents=True, exist_ok=True)


# ════════════════════════════════════════════════════════════
# 步骤结果保存
# ════════════════════════════════════════════════════════════


def save_step_result(ctx: PipelineContext, result: StepResult) -> Path:
    """保存步骤结果到 accounts/acc_id/evidence/{step_id}.json。"""
    step_dir = Path(ctx.paths.evidence_dir)
    step_dir.mkdir(parents=True, exist_ok=True)
    path = step_dir / f"{result.step_id or 'step'}.json"
    return write_step_result(path, result)


# ════════════════════════════════════════════════════════════
# 截图保存
# ════════════════════════════════════════════════════════════


def save_screenshot(ctx: PipelineContext, step_id: str, name: str, png_bytes: bytes | None = None) -> str:
    """保存截图到 accounts/acc_id/screenshots/，返回相对路径。"""
    shot_dir = Path(ctx.paths.screenshots_dir)
    shot_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{step_id}_{name}.png" if name else f"{step_id}.png"
    dest = shot_dir / filename
    if png_bytes:
        dest.write_bytes(png_bytes)
    else:
        # placeholder: touch file, caller is responsible for actual screenshot
        dest.touch()
    return str(dest)


# ════════════════════════════════════════════════════════════
# 错误 / bugs 记录
# ════════════════════════════════════════════════════════════


def build_failure_record(
    *,
    task_id: str,
    step_index: int,
    step_name: str,
    error_code: str,
    error_message: str,
    error_detail: Any = "",
    error_level: str = ErrorLevel.P3.value,
    error_type: str = "",
    account_id: str = "",
    action: str = "",
    screenshot: str = "",
    status: str = "pending_fix",
) -> dict[str, Any]:
    """Unified failure record shape (legacy format, backward-compatible)."""
    return {
        "task_id": str(task_id),
        "step_index": int(step_index),
        "step_name": str(step_name),
        "account_id": str(account_id),
        "error": {
            "code": str(error_code),
            "type": str(error_type or error_code),
            "message": str(error_message),
            "detail": error_detail,
        },
        "error_level": str(error_level),
        "action": str(action),
        "screenshot": str(screenshot),
        "status": str(status),
        "created_at": _now_iso(),
    }


def append_failure_record(path: str | Path, record: dict[str, Any]) -> Path:
    out = Path(path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    if out.is_file():
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows = [x for x in payload if isinstance(x, dict)]
        except Exception:
            rows = []
    rows.append(record)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def save_bug(ctx: PipelineContext, bug: BugRecord, *, bugs_path: str | Path | None = None) -> Path:
    """追加 BugRecord 到账号级 bugs.json。

    如果未指定 bugs_path，默认写入 task_dir/bugs.json。
    """
    if bugs_path:
        out = Path(bugs_path)
    else:
        out = Path(ctx.paths.task_dir) / "bugs.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    if out.is_file():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                rows = existing.get("bugs", [])
            elif isinstance(existing, list):
                rows = existing
        except Exception:
            rows = []

    rows.append(bug.to_dict())

    out.write_text(
        json.dumps({"task_id": ctx.task_id, "bugs": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def save_diagnostics(ctx: PipelineContext, entry: DiagnosticsEntry) -> list[Path]:
    """追加 ActionTrace 到 task 级 + account 级 diagnostics.json。

    写入位置：
      task_dir/diagnostics.json
      account_dir/diagnostics.json  （若 ctx.paths.account_dir 非空）
    返回写入的文件路径列表。
    """
    written: list[Path] = []

    def _append_one(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        if path.is_file():
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(content, list):
                    rows = content
            except Exception:
                rows = []
        rows.append(entry.to_dict())
        path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path)

    _append_one(Path(ctx.paths.task_dir) / "diagnostics.json")
    if ctx.paths.account_dir:
        _append_one(Path(ctx.paths.account_dir) / "diagnostics.json")

    return written


# ════════════════════════════════════════════════════════════
# 任务结果保存
# ════════════════════════════════════════════════════════════


def save_task_result(ctx: PipelineContext, result: Any) -> Path:
    """保存 TaskResult 到 task_dir/result.json。"""
    path = Path(ctx.paths.task_dir) / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict() if hasattr(result, "to_dict") else result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def read_task_result(task_dir: str | Path) -> dict[str, Any] | None:
    """读取 task_dir/result.json。"""
    path = Path(task_dir) / "result.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None
