# Known Issues

本文件记录所有已发现但尚未修复的问题（Open 状态），以及已修复问题中仍值得关注的 latent risk。

## Open Issues

### [RBK-0017] dry-run Picset 长时间静默无进度输出
- 风险等级: P3
- 发现时间: 2026-05-11
- 跟进建议: `_run_script` 增加超时/进度轮询机制，或 Step4 调用前打印预计时间范围

### Step10 live gate 多账号不可用
- 风险等级: P2（preview 模式不影响）
- 发现时间: 2026-05-11（审计发现）
- 跟进建议: 上线 live 前需在 `workflow_orchestrator.py` Phase 2 循环中将 Phase 1 evidence 复制到每个账号目录

## Fixed Issues (Residual Risk)

### diagnostics.json 未实现写入（已修复）
- 残留风险: 低 — diagnostics.json 在 P0/P1/manual_review/P3 时写入 task 级 + account 级
- 修复: `workflow_core.py` 新增 `DiagnosticsEntry` dataclass；`workflow_io.py` 新增 `save_diagnostics()`；`workflow_orchestrator.py:run_account()` 错误处理和 `run_task()` 完成时写入

## Fixed Issues (Residual Risk)

### Orchestrator 状态判定异常（已修复）
- 残留风险: 低 — `success_count=0 manual_review_count=1 failed_count=0` 现在输出 `partial` 而非 `failed`
- 修复: `workflow_orchestrator.py:L383` — `elif task_result.success_count > 0` → `elif task_result.success_count > 0 or task_result.manual_review_count > 0`

### [RBK-0014] Step7 缓存命中 URL 为空（已修复）
- 残留风险: 低 — evidence URL 显示 `"(cached - see screenshot)"`，仍有截图可确认
- 定位: `scripts/workflow_steps.py:L547, L573`

### [RBK-0015] Step8 manual_review 缺 error（已修复）
- 残留风险: 极低 — 补全了 StepErrorPayload

### [RBK-0016] Step9 manual_review 用 P3 编码（已修复）
- 残留风险: 极低 — error_level 改为 `""`，不影响 flow

## 回归易发区域

1. Step7 URL 校验 — 修改 `current_url` 变量会同时影响逻辑和 evidence，必须验证分离
2. Step8 重试耗尽后的 manual_review — 修改时必须补全 error/detail/suggestion
3. orchestrator 状态判定 — 新增 manual_review 分支时需同步更新状态汇总逻辑
