"""PRD v1.0 任务编排器 — run_task() + run_account() 多账号状态机。

入口：
  task_result = run_task(TaskInput(...))

流程：
  run_task
    ├─ 创建任务目录 / 保存 input.json
    ├─ 顺序执行每个账号 run_account
    │    ├─ Step1  validate_input
    │    ├─ Step2  search_xhs_keyword
    │    ├─ Step3  select_reference_post
    │    ├─ Step4  generate_image
    │    ├─ Step5  adjust_image_color
    │    ├─ Step6  generate_copywriting
    │    ├─ Step7  open_creator_page
    │    ├─ Step8  fill_publish_form
    │    ├─ Step9  attach_product
    │    └─ Step10 preview_or_publish
    ├─ 汇总 result.json
    └─ 汇总 bugs.json
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflow_core import (
    AccountResult,
    BugRecord,
    DiagnosticsEntry,
    ErrorLevel,
    PipelineContext,
    PipelineState,
    RuntimeConfig,
    StepErrorPayload,
    StepResult,
    StepStatus,
    TaskInput,
    TaskResult,
)
from workflow_io import build_paths, ensure_task_dirs, save_bug, save_diagnostics, save_step_result, save_task_result, _now_iso
from workflow_steps import (
    run_step1_validate_input,
    run_step2_search_xhs_keyword,
    run_step3_select_reference_post,
    run_step4_generate_image,
    run_step5_adjust_image_color,
    run_step6_generate_copywriting,
    run_step7_open_creator_page,
    run_step8_fill_publish_form,
    run_step9_attach_product,
    run_step10_preview_or_publish,
)
from core_config import resolve_account_port


# ── Step 列表 ──

STEPS: list[tuple[str, str, Any]] = [
    ("step1_validate_input",      "校验输入",          run_step1_validate_input),
    ("step2_search_xhs_keyword",  "小红书搜索关键词",    run_step2_search_xhs_keyword),
    ("step3_select_reference_post", "筛选参考帖子",     run_step3_select_reference_post),
    ("step4_generate_image",      "画图软件生成新图",    run_step4_generate_image),
    ("step5_adjust_image_color",  "自动调色",          run_step5_adjust_image_color),
    ("step6_generate_copywriting", "豆包生成文案",      run_step6_generate_copywriting),
    ("step7_open_creator_page",   "打开创作者发布页",    run_step7_open_creator_page),
    ("step8_fill_publish_form",   "上传图片和填写文案",  run_step8_fill_publish_form),
    ("step9_attach_product",      "添加商品",          run_step9_attach_product),
    ("step10_preview_or_publish", "预览/发布",         run_step10_preview_or_publish),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_diagnostics_entry(result: StepResult, ctx: PipelineContext, trace_type: str, suggestion: str = "") -> DiagnosticsEntry:
    """从 StepResult 构建 ActionTrace 条目。"""
    err = result.error
    return DiagnosticsEntry(
        step_id=result.step_id,
        step_name=result.step_name,
        account_id=ctx.account_id,
        trace_type=trace_type,
        error_level=err.error_level if err else "",
        code=err.code if err else "",
        message=err.message if err else "",
        detail=err.detail if err else "",
        suggestion=suggestion,
        input_snapshot=err.input_snapshot if err else {},
        screenshot=result.evidence.screenshot,
        timestamp=_now(),
    )


# ════════════════════════════════════════════════════════════
# 单账号执行
# ════════════════════════════════════════════════════════════


def run_account(ctx: PipelineContext, step_range: tuple[int, int] | None = None) -> AccountResult:
    """顺序执行指定步骤范围，返回 AccountResult。

    step_range=None → 全部 10 步（向后兼容）。
    step_range=(0,6) → Step1-Step6。
    step_range=(6,10) → Step7-Step10。
    """
    steps = STEPS[slice(*step_range)] if step_range else STEPS

    account_result = AccountResult(
        account_id=ctx.account_id,
        status="running",
        started_at=_now(),
    )

    ctx.state.account_status = "running"

    for step_id, step_name, step_fn in steps:
        # 检查取消信号
        if ctx.state.cancel_requested:
            account_result.status = "cancelled"
            account_result.failure_reason = "任务已取消"
            ctx.state.account_status = "cancelled"
            break

        ctx.state.current_step_name = step_id

        # 执行步骤
        try:
            result = step_fn(ctx)
        except Exception as exc:
            result = StepResult(
                task_id=ctx.task_id, account_id=ctx.account_id,
                step_id=step_id, step_name=step_name,
                status=StepStatus.FAILED.value,
                started_at=_now(), finished_at=_now(),
                error=StepErrorPayload(
                    code="UNHANDLED_EXCEPTION",
                    message=f"{type(exc).__name__}: {exc}",
                    error_level=ErrorLevel.P2.value,
                    action="retry",
                ),
            )

        # 填充 step_id/name（有些步骤函数可能缺省）
        if not result.step_id:
            result.step_id = step_id
        if not result.step_name:
            result.step_name = step_name
        if not result.started_at:
            result.started_at = _now()
        if not result.finished_at:
            result.finished_at = _now()

        # 保存步骤结果
        save_step_result(ctx, result)
        account_result.steps.append(result)

        # 重试逻辑（P2）
        max_retry = ctx.config.ui_retry if result.error and "TIMEOUT" in result.error.code else ctx.config.network_retry
        retry_count = 0
        while result.status == StepStatus.FAILED.value and result.error and result.error.error_level == ErrorLevel.P2.value:
            if result.error.action == "stop_account":
                break
            if retry_count >= max_retry:
                # 降级为 P1: 重试耗尽
                result.error.error_level = ErrorLevel.P1.value
                result.error.action = "stop_account"
                result.error.message += f" (重试{max_retry}次后失败)"
                save_step_result(ctx, result)
                break
            retry_count += 1
            result.retry_count = retry_count
            result.started_at = _now()
            try:
                result = step_fn(ctx)
            except Exception as exc:
                result = StepResult(
                    task_id=ctx.task_id, account_id=ctx.account_id,
                    step_id=step_id, step_name=step_name,
                    status=StepStatus.FAILED.value,
                    started_at=_now(), finished_at=_now(),
                    error=StepErrorPayload(
                        code="UNHANDLED_EXCEPTION",
                        message=f"{type(exc).__name__}: {exc}",
                        error_level=ErrorLevel.P2.value, action="retry",
                    ),
                )
            result.step_id = step_id
            result.step_name = step_name
            result.finished_at = _now()
            save_step_result(ctx, result)
            # update last entry
            if account_result.steps:
                account_result.steps[-1] = result
            else:
                account_result.steps.append(result)

        # 根据错误等级决定流程
        if result.status != StepStatus.SUCCESS.value:
            err = result.error
            if err:
                level = err.error_level

                if level == ErrorLevel.P0.value:
                    # P0: 立即停止整个任务，禁止发布
                    save_diagnostics(ctx, _build_diagnostics_entry(result, ctx, "error"))
                    account_result.status = "failed"
                    account_result.failure_reason = err.message
                    ctx.state.account_status = "failed"
                    ctx.state.task_status = "failed"
                    ctx.state.cancel_requested = True  # 停止后续账号
                    break

                elif level == ErrorLevel.P1.value:
                    # P1: 当前账号失败，进入下个账号
                    save_diagnostics(ctx, _build_diagnostics_entry(result, ctx, "error"))
                    account_result.status = "failed"
                    account_result.failure_reason = err.message
                    ctx.state.account_status = "failed"
                    break

                elif level == ErrorLevel.P3.value:
                    # P3: 记录但继续
                    save_diagnostics(ctx, _build_diagnostics_entry(result, ctx, "warning"))

            if result.status == StepStatus.MANUAL_REVIEW.value:
                suggestion = result.error.message if result.error else "需人工确认发布页状态"
                save_diagnostics(ctx, _build_diagnostics_entry(result, ctx, "manual_review", suggestion=suggestion))
                account_result.status = "manual_review"
                account_result.failure_reason = result.error.message if result.error else "需人工确认"

    # 如果全跑完且没有失败，标记成功
    if account_result.status == "running":
        account_result.status = "success"
        ctx.state.account_status = "success"
        ctx.state.current_step_name = "completed"

    account_result.current_step = ctx.state.current_step_name
    account_result.finished_at = _now()
    return account_result


# ════════════════════════════════════════════════════════════
# 任务级编排
# ════════════════════════════════════════════════════════════


def run_task(task_input: TaskInput, *, run_root: str | Path = "") -> TaskResult:
    """完整任务入口。

    1. 创建任务目录 / 保存 input.json
    2. Phase 1: Step1-Step6（首账号，仅一次）
    3. Phase 2: Step7-Step10（每个账号循环，共享 Phase 1 产物）
    4. 汇总 result.json / bugs.json
    """
    task_result = TaskResult(
        task_id=task_input.task_id,
        client_id=task_input.client_id,
        status="running",
        publish_mode=task_input.publish_mode,
        total_accounts=len(task_input.accounts),
        started_at=_now(),
    )

    first_account = task_input.accounts[0] if task_input.accounts else "acc_unknown"

    # 创建任务级 ctx（用于目录和 task_dir 级输出）
    ctx = PipelineContext(
        task_id=task_input.task_id,
        client_id=task_input.client_id,
        account_id=first_account,
        input=task_input,
        config=RuntimeConfig.from_task_input(task_input),
        state=PipelineState(task_status="running"),
    )
    ctx.paths = build_paths(task_input.task_id, first_account, run_root=run_root)
    ensure_task_dirs(ctx)

    # 保存 input.json
    input_path = Path(ctx.paths.input_dir) / "input.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(
        json.dumps(task_input.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ════════════════════════════════════════════════════════════
    # Phase 1: Step1-Step6（首账号，仅一次）
    # ════════════════════════════════════════════════════════════
    print(f"[task] Phase 1: Step1-Step6 ({first_account})", flush=True)

    phase1_ctx = PipelineContext(
        task_id=task_input.task_id,
        client_id=task_input.client_id,
        account_id=first_account,
        input=task_input,
        config=RuntimeConfig.from_task_input(task_input),
        state=PipelineState(task_status="running", account_status="running"),
    )
    phase1_ctx.paths = build_paths(task_input.task_id, first_account, run_root=run_root)
    ensure_task_dirs(phase1_ctx)
    phase1_ctx.artifacts.product_images = list(task_input.product_images)

    phase1_result = run_account(phase1_ctx, step_range=(0, 6))

    # Phase 1 P0/P1 → 无法继续
    if phase1_result.status in ("failed", "cancelled"):
        task_result.status = "failed"
        for step_result in phase1_result.steps:
            if step_result.error and step_result.error.error_level in (ErrorLevel.P0.value, ErrorLevel.P1.value):
                _save_bug_for_step(task_result, phase1_ctx, first_account, step_result)
        save_task_result(ctx, task_result)
        print(f"[task] Phase 1 失败 ({phase1_result.status}), 任务终止", flush=True)
        return task_result

    # ════════════════════════════════════════════════════════════
    # Phase 2: Step7-Step10 按账号循环，共享 Phase 1 产物
    # ════════════════════════════════════════════════════════════
    for idx, account_id in enumerate(task_input.accounts):
        if ctx.state.cancel_requested:
            break

        # 每个账号独立 ctx
        acct_ctx = PipelineContext(
            task_id=task_input.task_id,
            client_id=task_input.client_id,
            account_id=account_id,
            input=task_input,
            config=RuntimeConfig.from_task_input(task_input),
            state=PipelineState(task_status="running", account_status="running"),
        )
        acct_ctx.paths = build_paths(task_input.task_id, account_id, run_root=run_root)
        ensure_task_dirs(acct_ctx)

        # 注入 Phase 1 共享产物（同一套图文、同一商品）
        acct_ctx.artifacts.product_images = list(task_input.product_images)
        acct_ctx.artifacts.generated_images = list(phase1_ctx.artifacts.generated_images)
        acct_ctx.artifacts.final_images = list(phase1_ctx.artifacts.final_images)
        acct_ctx.artifacts.title_text = phase1_ctx.artifacts.title_text
        acct_ctx.artifacts.body_text = phase1_ctx.artifacts.body_text

        # M-002: 账号级 CDP 端口（通过 env var 传递到 _run_script 子进程）
        port = resolve_account_port(account_id, fallback=9322)
        acct_ctx.config.cdp_port = port
        os.environ["XHS_CDP_PORT"] = str(port)

        print(f"[task] Phase 2: account {idx+1}/{len(task_input.accounts)}: "
              f"{account_id} (port={port})", flush=True)

        acct_result = run_account(acct_ctx, step_range=(6, 10))

        # 首账号合并 Step1-6 + Step7-10
        if idx == 0:
            phase1_result.steps.extend(acct_result.steps)
            phase1_result.status = acct_result.status
            phase1_result.current_step = acct_result.current_step
            task_result.accounts.append(phase1_result)
        else:
            task_result.accounts.append(acct_result)

        # 统计
        if acct_result.status == "success":
            task_result.success_count += 1
        elif acct_result.status == "failed":
            task_result.failed_count += 1
        elif acct_result.status == "manual_review":
            task_result.manual_review_count += 1

        # 收集 bugs（P0 / P1）
        for step_result in acct_result.steps:
            if step_result.error and step_result.error.error_level in (ErrorLevel.P0.value, ErrorLevel.P1.value):
                task_result.errors.append(BugRecord(
                    account_id=account_id,
                    step_id=step_result.step_id,
                    error_level=step_result.error.error_level,
                    error_type=step_result.error.code,
                    message=step_result.error.message,
                    action=step_result.error.action,
                    retry_count=step_result.retry_count,
                ))

        # 保存 bugs.json（每个账号累加到 task 级）
        task_bugs_path = Path(acct_ctx.paths.task_dir) / "bugs.json"
        for bug in task_result.errors:
            save_bug(acct_ctx, bug, bugs_path=task_bugs_path)

        # M-001: P0 传播 — 阻止后续账号
        if any(s.error and s.error.error_level == ErrorLevel.P0.value
               for s in acct_result.steps if s.error):
            print(f"[task] P0 on account {account_id}: stopping subsequent accounts", flush=True)
            ctx.state.cancel_requested = True

    # 汇总任务状态
    if ctx.state.cancel_requested:
        task_result.status = "cancelled"
    elif task_result.failed_count == 0 and task_result.manual_review_count == 0:
        task_result.status = "completed"
    elif task_result.success_count > 0 or task_result.manual_review_count > 0:
        task_result.status = "partial"
    else:
        task_result.status = "failed"

    task_result.finished_at = _now()

    # 保存 result.json（task 级）
    save_task_result(ctx, task_result)

    # 写入任务级 diagnostics 总结
    task_diag = DiagnosticsEntry(
        step_id="task_complete",
        step_name="任务完成",
        account_id="",
        trace_type="task_result",
        error_level="",
        code="",
        message=f"任务状态: {task_result.status} (成功={task_result.success_count} 失败={task_result.failed_count} 需人工={task_result.manual_review_count})",
        detail={
            "success_count": task_result.success_count,
            "failed_count": task_result.failed_count,
            "manual_review_count": task_result.manual_review_count,
        },
        timestamp=_now(),
    )
    save_diagnostics(ctx, task_diag)

    print(f"[task] 完成: {task_result.status} "
          f"成功={task_result.success_count} 失败={task_result.failed_count} "
          f"需人工={task_result.manual_review_count}", flush=True)

    return task_result


def _save_bug_for_step(
    task_result: TaskResult,
    ctx: PipelineContext,
    account_id: str,
    step_result: StepResult,
) -> None:
    """Helper: record a single bug and persist to task_dir/bugs.json."""
    bug = BugRecord(
        account_id=account_id,
        step_id=step_result.step_id,
        error_level=step_result.error.error_level,
        error_type=step_result.error.code,
        message=step_result.error.message,
        action=step_result.error.action,
        retry_count=step_result.retry_count,
    )
    task_result.errors.append(bug)
    task_bugs_path = Path(ctx.paths.task_dir) / "bugs.json"
    save_bug(ctx, bug, bugs_path=task_bugs_path)
