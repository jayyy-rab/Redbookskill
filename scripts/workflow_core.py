"""
Shared workflow step framework — PRD v1.0 核心数据类。

层次：
  常量 / 枚举 → 异常 → 输入/配置 → 路径/产物/证据 → 步骤结果 → 任务结果
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from workflow_status import StepStatus


# ════════════════════════════════════════════════════════════
# 常量 / 枚举
# ════════════════════════════════════════════════════════════


class ErrorLevel(StrEnum):
    """错误等级（PRD v1.0）。

    P0: 可能误发/错发/错商品/错账号 → 立即停止，禁止发布
    P1: 当前账号无法继续 → 当前账号失败，进入下个账号
    P2: 可重试问题 → 按重试规则处理
    P3: 非关键问题 → 记录但不中断
    """
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


# ════════════════════════════════════════════════════════════
# 异常
# ════════════════════════════════════════════════════════════


class StepFailure(RuntimeError):
    """受控步骤失败。"""

    def __init__(
        self,
        message: str,
        *,
        code: int = 2,
        detail: dict[str, Any] | None = None,
        error_level: str = ErrorLevel.P2.value,
    ):
        super().__init__(message)
        self.code = int(code)
        self.detail = detail or {}
        self.error_level = str(error_level)


# ════════════════════════════════════════════════════════════
# 输入 / 配置
# ════════════════════════════════════════════════════════════


@dataclass
class TaskInput:
    """PRD v1.0 统一输入 — 对应 input.json。"""
    task_id: str = ""
    client_id: str = ""
    product_images: list[str] = field(default_factory=list)
    seed_keyword: str = ""
    product_name: str = ""
    product_id: str = ""
    brief: str = ""
    accounts: list[str] = field(default_factory=list)
    publish_mode: str = "preview"
    max_accounts: int = 100
    allow_no_product: bool = False
    allow_reference_fallback: bool = False
    allow_live_publish: bool = False
    risk_control: dict[str, Any] = field(default_factory=lambda: {
        "max_ui_retry": 2,
        "max_network_retry": 3,
        "max_generation_retry": 1,
        "stop_on_p0": True,
        "stop_account_on_p1": True,
    })
    content_policy: dict[str, Any] = field(default_factory=lambda: {
        "max_body_chars": 180,
        "min_body_chars": 80,
        "forbidden_words": [],
        "require_product_keyword": True,
    })

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskInput":
        return cls(
            task_id=str(d.get("task_id", "")),
            client_id=str(d.get("client_id", "")),
            product_images=list(d.get("product_images", [])),
            seed_keyword=str(d.get("seed_keyword", "")),
            product_name=str(d.get("product_name", "")),
            product_id=str(d.get("product_id", "")),
            brief=str(d.get("brief", "")),
            accounts=list(d.get("accounts", [])),
            publish_mode=str(d.get("publish_mode", "preview")),
            max_accounts=int(d.get("max_accounts", 100)),
            allow_no_product=bool(d.get("allow_no_product", False)),
            allow_reference_fallback=bool(d.get("allow_reference_fallback", False)),
            allow_live_publish=bool(d.get("allow_live_publish", False)),
            risk_control=dict(d.get("risk_control", {})),
            content_policy=dict(d.get("content_policy", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeConfig:
    """运行配置（从 TaskInput.risk_control + content_policy 派生）。"""
    ui_retry: int = 2
    network_retry: int = 3
    generation_retry: int = 1
    stop_on_p0: bool = True
    stop_account_on_p1: bool = True
    max_body_chars: int = 180
    min_body_chars: int = 80
    forbidden_words: list[str] = field(default_factory=list)
    require_product_keyword: bool = True
    cdp_port: int = 9322

    @classmethod
    def from_task_input(cls, inp: TaskInput) -> "RuntimeConfig":
        rc = inp.risk_control or {}
        cp = inp.content_policy or {}
        return cls(
            ui_retry=int(rc.get("max_ui_retry", 2)),
            network_retry=int(rc.get("max_network_retry", 3)),
            generation_retry=int(rc.get("max_generation_retry", 1)),
            stop_on_p0=bool(rc.get("stop_on_p0", True)),
            stop_account_on_p1=bool(rc.get("stop_account_on_p1", True)),
            max_body_chars=int(cp.get("max_body_chars", 180)),
            min_body_chars=int(cp.get("min_body_chars", 80)),
            forbidden_words=list(cp.get("forbidden_words", [])),
            require_product_keyword=bool(cp.get("require_product_keyword", True)),
        )


# ════════════════════════════════════════════════════════════
# 路径 / 产物 / 证据 注册表
# ════════════════════════════════════════════════════════════


@dataclass
class PathRegistry:
    """统一路径注册表 — 所有步骤通过此对象读写文件路径。

    目录结构：
      runs/{task_id}/
        input/        → input_dir
        accounts/{account_id}/
          logs/       → logs_dir
          screenshots/ → screenshots_dir
          artifacts/  → artifacts_dir
          evidence/   → evidence_dir
    """
    run_root: str = ""           # runs/
    task_dir: str = ""           # runs/{task_id}/
    input_dir: str = ""          # runs/{task_id}/input/
    account_dir: str = ""        # runs/{task_id}/accounts/{account_id}/
    logs_dir: str = ""           # runs/{task_id}/accounts/{account_id}/logs/
    screenshots_dir: str = ""    # runs/{task_id}/accounts/{account_id}/screenshots/
    artifacts_dir: str = ""      # runs/{task_id}/accounts/{account_id}/artifacts/
    evidence_dir: str = ""       # runs/{task_id}/accounts/{account_id}/evidence/


@dataclass
class ArtifactRegistry:
    """统一产物注册表 — 跨步骤传递图片/文案产物路径。"""
    product_images: list[str] = field(default_factory=list)
    reference_images: list[str] = field(default_factory=list)
    generated_images: list[str] = field(default_factory=list)
    final_images: list[str] = field(default_factory=list)
    title_text: str = ""
    body_text: str = ""
    topics_text: str = ""
    copywriting_json_path: str = ""


@dataclass
class EvidenceRegistry:
    """统一证据注册表 — 截图/日志/DOM 快照路径收集。"""
    screenshots: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    dom_snapshots: list[str] = field(default_factory=list)


@dataclass
class ErrorRegistry:
    """统一错误注册表 — 按 error_level 收集 P0/P1/P2/P3 错误。"""
    errors: list[StepErrorPayload] = field(default_factory=list)

    def add(self, error: StepErrorPayload) -> None:
        self.errors.append(error)

    @property
    def has_p0(self) -> bool:
        return any(e.error_level == ErrorLevel.P0.value for e in self.errors)

    @property
    def has_p1(self) -> bool:
        return any(e.error_level == ErrorLevel.P1.value for e in self.errors)


@dataclass
class MetricsRegistry:
    """统一指标注册表 — 步骤耗时 / 重试次数 / 成本。"""
    step_durations_ms: dict[str, int] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    image_generation_count: int = 0
    copywriting_generation_count: int = 0
    estimated_cost: float = 0.0


@dataclass
class PipelineState:
    """任务/账号运行时状态跟踪。"""
    task_status: str = "pending"        # pending | running | completed | failed | cancelled
    account_status: str = ""            # pending | running | success | failed | manual_review
    current_step: int = 0
    current_step_name: str = ""
    step_status: str = ""
    retry_count: int = 0
    failure_reason: str = ""
    error_level: str = ""
    cancel_requested: bool = False
    checkpoint: dict[str, Any] = field(default_factory=dict)


# ════════════════════════════════════════════════════════════
# 步骤结果
# ════════════════════════════════════════════════════════════


@dataclass
class StepSuccessCheck:
    passed: bool = False
    items: list[str] = field(default_factory=list)


@dataclass
class StepErrorPayload:
    code: str = ""
    message: str = ""
    detail: Any = ""
    error_level: str = ErrorLevel.P3.value
    action: str = ""          # stop_task | stop_account | retry | log_only
    input_snapshot: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepEvidence:
    """PRD v1.0 步骤证据。"""
    screenshot: str = ""
    url: str = ""
    uploaded_image: str = ""
    title_text: str = ""
    body_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    """PRD v1.0 统一步骤结果 — 每个 run_stepN_xxx 的返回值。"""

    task_id: str = ""
    account_id: str = ""
    step_id: str = ""                      # "step1_validate_input"
    step_name: str = ""                    # "校验输入"
    status: str = StepStatus.PENDING.value
    started_at: str = ""
    finished_at: str = ""
    retry_count: int = 0
    evidence: StepEvidence = field(default_factory=StepEvidence)
    artifacts: dict[str, Any] = field(default_factory=dict)
    created_files: list[str] = field(default_factory=list)
    error: StepErrorPayload | None = None
    success_check: StepSuccessCheck = field(default_factory=StepSuccessCheck)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "account_id": self.account_id,
            "step_id": self.step_id,
            "step_name": self.step_name,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retry_count": int(self.retry_count),
            "evidence": asdict(self.evidence),
            "artifacts": dict(self.artifacts),
            "created_files": list(self.created_files),
            "error": asdict(self.error) if self.error else None,
            "success_check": asdict(self.success_check),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StepResult":
        ev = payload.get("evidence") or {}
        err = payload.get("error")
        sc = payload.get("success_check") or {}
        return cls(
            task_id=str(payload.get("task_id", "")),
            account_id=str(payload.get("account_id", "")),
            step_id=str(payload.get("step_id", "")),
            step_name=str(payload.get("step_name", "")),
            status=str(payload.get("status", StepStatus.PENDING.value)),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
            retry_count=int(payload.get("retry_count", 0)),
            evidence=StepEvidence(
                screenshot=str(ev.get("screenshot", "")),
                url=str(ev.get("url", "")),
                uploaded_image=str(ev.get("uploaded_image", "")),
                title_text=str(ev.get("title_text", "")),
                body_text=str(ev.get("body_text", "")),
                extra=dict(ev.get("extra", {})),
            ),
            artifacts=dict(payload.get("artifacts", {})),
            created_files=list(payload.get("created_files", [])),
            error=StepErrorPayload(
                code=str(err.get("code", "")),
                message=str(err.get("message", "")),
                detail=err.get("detail", ""),
                error_level=str(err.get("error_level", ErrorLevel.P3.value)),
                action=str(err.get("action", "")),
                input_snapshot=dict(err.get("input_snapshot", {})),
            ) if err else None,
            success_check=StepSuccessCheck(
                passed=bool(sc.get("passed", False)),
                items=list(sc.get("items", [])),
            ),
        )


# ════════════════════════════════════════════════════════════
# 任务 / 账号 聚合结果
# ════════════════════════════════════════════════════════════


@dataclass
class BugRecord:
    """PRD v1.0 bugs.json 单条错误。"""
    account_id: str = ""
    step_id: str = ""
    error_level: str = ErrorLevel.P3.value
    error_type: str = ""
    message: str = ""
    action: str = ""
    retry_count: int = 0
    url: str = ""
    screenshot: str = ""
    input_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "step_id": self.step_id,
            "error_level": self.error_level,
            "error_type": self.error_type,
            "message": self.message,
            "action": self.action,
            "retry_count": int(self.retry_count),
            "url": self.url,
            "screenshot": self.screenshot,
            "input_snapshot": dict(self.input_snapshot),
        }


@dataclass
class DiagnosticsEntry:
    """ActionTrace — diagnostics.json 单条记录（工程流 v1.1 冻结）。"""
    step_id: str = ""
    step_name: str = ""
    account_id: str = ""
    trace_type: str = ""             # error | manual_review | warning
    error_level: str = ""
    code: str = ""
    message: str = ""
    detail: Any = ""
    suggestion: str = ""
    input_snapshot: dict[str, Any] = field(default_factory=dict)
    screenshot: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_name": self.step_name,
            "account_id": self.account_id,
            "trace_type": self.trace_type,
            "error_level": self.error_level,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "suggestion": self.suggestion,
            "input_snapshot": dict(self.input_snapshot),
            "screenshot": self.screenshot,
            "timestamp": self.timestamp,
        }


@dataclass
class AccountResult:
    """PRD v1.0 result.json 单账号结果。"""
    account_id: str = ""
    status: str = "pending"        # pending | running | success | failed | manual_review | skipped
    current_step: str = ""
    post_url: str = ""
    preview_screenshot: str = ""
    failure_reason: str = ""
    steps: list[StepResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "status": self.status,
            "current_step": self.current_step,
            "post_url": self.post_url,
            "preview_screenshot": self.preview_screenshot,
            "failure_reason": self.failure_reason,
            "steps": [s.to_dict() for s in self.steps],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class TaskResult:
    """PRD v1.0 最终 result.json。"""
    task_id: str = ""
    client_id: str = ""
    status: str = "pending"
    publish_mode: str = "preview"
    total_accounts: int = 0
    success_count: int = 0
    failed_count: int = 0
    manual_review_count: int = 0
    started_at: str = ""
    finished_at: str = ""
    accounts: list[AccountResult] = field(default_factory=list)
    errors: list[BugRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "client_id": self.client_id,
            "status": self.status,
            "publish_mode": self.publish_mode,
            "total_accounts": int(self.total_accounts),
            "success_count": int(self.success_count),
            "failed_count": int(self.failed_count),
            "manual_review_count": int(self.manual_review_count),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "accounts": [a.to_dict() for a in self.accounts],
        }

    def to_bugs_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "bugs": [b.to_dict() for b in self.errors],
        }


# ════════════════════════════════════════════════════════════
# PipelineContext — 所有步骤的唯一上下文
# ════════════════════════════════════════════════════════════


@dataclass
class PipelineContext:
    """PRD v1.0 统一上下文 — 所有 run_stepN_xxx 函数的唯一参数。

    禁止散传参数。所有步骤从 ctx 取：
      ctx.input       → TaskInput
      ctx.paths       → PathRegistry
      ctx.config      → RuntimeConfig
      ctx.state       → PipelineState
      ctx.artifacts   → ArtifactRegistry
      ctx.evidence    → EvidenceRegistry
      ctx.metrics     → MetricsRegistry
    """
    task_id: str = ""
    client_id: str = ""
    account_id: str = ""

    input: TaskInput = field(default_factory=TaskInput)
    paths: PathRegistry = field(default_factory=PathRegistry)
    config: RuntimeConfig = field(default_factory=RuntimeConfig)
    state: PipelineState = field(default_factory=PipelineState)
    artifacts: ArtifactRegistry = field(default_factory=ArtifactRegistry)
    evidence: EvidenceRegistry = field(default_factory=EvidenceRegistry)
    metrics: MetricsRegistry = field(default_factory=MetricsRegistry)

    # 便利属性（从 input 委派）
    @property
    def seed_keyword(self) -> str:
        return self.input.seed_keyword

    @property
    def product_name(self) -> str:
        return self.input.product_name

    @property
    def product_id(self) -> str:
        return self.input.product_id

    @property
    def brief(self) -> str:
        return self.input.brief

    @property
    def publish_mode(self) -> str:
        return self.input.publish_mode


# ════════════════════════════════════════════════════════════
# 旧兼容层 — WorkflowStep / WorkflowRunner / StepReport
# ════════════════════════════════════════════════════════════


@dataclass
class StepReport:
    name: str
    status: str
    started_at: str
    ended_at: str
    duration_ms: int
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None


@dataclass
class WorkflowContext:
    run_id: str
    strict: bool = True
    artifacts: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


class WorkflowStep:
    """Base class for all workflow steps (legacy)."""
    name = "unnamed_step"

    def run(self, ctx: WorkflowContext) -> dict[str, Any]:
        raise NotImplementedError


class WorkflowRunner:
    """Sequential step runner with structured reports (legacy)."""

    def __init__(self, *, strict: bool = True):
        self.strict = bool(strict)

    def execute(self, steps: list[WorkflowStep], ctx: WorkflowContext) -> tuple[int, list[StepReport]]:
        reports: list[StepReport] = []
        exit_code = 0

        for step in steps:
            started = time.time()
            started_iso = datetime.now(timezone.utc).isoformat()
            status = "success"
            data: dict[str, Any] = {}
            error: dict[str, Any] | None = None

            try:
                data = step.run(ctx) or {}
            except StepFailure as exc:
                status = "failed"
                exit_code = exc.code or 2
                error = {
                    "type": "StepFailure",
                    "message": str(exc),
                    "detail": exc.detail,
                    "code": exit_code,
                }
            except Exception as exc:  # noqa: BLE001
                status = "failed"
                exit_code = 2
                error = {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "detail": {},
                    "code": exit_code,
                }

            ended = time.time()
            ended_iso = datetime.now(timezone.utc).isoformat()
            reports.append(
                StepReport(
                    name=step.name,
                    status=status,
                    started_at=started_iso,
                    ended_at=ended_iso,
                    duration_ms=int((ended - started) * 1000),
                    data=data,
                    error=error,
                )
            )

            if status != "success" and self.strict:
                break

        return exit_code, reports

    @staticmethod
    def write_report(path: str | Path, ctx: WorkflowContext, reports: list[StepReport], exit_code: int) -> None:
        out = Path(path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": ctx.run_id,
            "strict": ctx.strict,
            "exit_code": int(exit_code),
            "meta": ctx.meta,
            "artifacts": ctx.artifacts,
            "steps": [asdict(r) for r in reports],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ════════════════════════════════════════════════════════════
# emit (用于 subprocess 通信)
# ════════════════════════════════════════════════════════════


def emit_step_result(result: StepResult) -> None:
    """输出标准步骤结果 JSON 到标准输出，供编排器读取。"""
    output = json.dumps(result.to_dict(), ensure_ascii=False)
    print(f"STEP_RESULT:{output}")
    sys.stdout.flush()
