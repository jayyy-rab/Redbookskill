"""Shared workflow status and next-action helpers."""

from __future__ import annotations

from enum import Enum
from typing import Iterable


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    # 以下为已废弃（兼容保留），主流程不再使用
    RETRYING = "retrying"          # deprecated
    STOPPED = "stopped"            # deprecated
    PARTIAL_SUCCESS = "partial_success"  # deprecated


TERMINAL_BLOCK_STATUSES = {
    StepStatus.FAILED.value,
    StepStatus.MANUAL_REVIEW.value,
}


def derive_overall_status(
    statuses: Iterable[str],
    *,
    allow_skipped_as_success: bool,
) -> str:
    """Derive workflow status from step statuses."""
    vals = [str(s) for s in statuses]
    if not vals:
        return "pending"
    if any(s == "failed" for s in vals):
        return "failed"
    if any(s == "manual_review" for s in vals):
        return "manual_review"
    if any(s == "running" for s in vals):
        return "running"
    if allow_skipped_as_success:
        if all(s in {"success", "skipped"} for s in vals):
            return "success"
    else:
        if all(s == "success" for s in vals):
            return "success"
    return "pending"


def next_action_for_numbered_flow(
    *,
    has_steps: bool,
    last_step: int,
    last_status: str,
    start_step: int = 1,
    max_step: int = 10,
) -> str:
    """Compute next action for fixed-number workflow."""
    if not has_steps:
        return f"start_step_{int(start_step)}"
    if str(last_status) in TERMINAL_BLOCK_STATUSES:
        return f"wait_manual_then_resume_step_{int(last_step)}"
    if int(last_step) >= int(max_step):
        return "workflow_done"
    return f"run_step_{int(last_step) + 1}"


def next_action_for_named_flow(
    *,
    selected_step_names: list[str],
    completed_step_names: set[str],
    last_status: str | None,
    last_name: str | None,
) -> str:
    """Compute next action for named-step workflow."""
    if last_status is None:
        first = selected_step_names[0] if selected_step_names else "none"
        return f"run_{first}"
    if str(last_status) != "success":
        return f"fix_and_resume_{last_name or 'unknown'}"
    for name in selected_step_names:
        if name not in completed_step_names:
            return f"run_{name}"
    return "workflow_done"
