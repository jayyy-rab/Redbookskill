# Bug History Rules

## Purpose
This file records historical bugs and defines mandatory anti-regression rules.
Goal: prevent repeated failures and improve long-term stability.

## Mandatory Rules
1. Every production-impact bug must be recorded in this file within the same change cycle.
2. Every bug record must include root cause, fix scope, verification, and anti-regression checks.
3. No bug fix is complete without at least one explicit verification method.
4. If architecture weakness is involved, propose a design option first, then implement minimal safe change.
5. Avoid broad refactors in bug fixes unless explicitly approved.

## Severity Levels
- S0: data loss / publish corruption / account risk.
- S1: core flow failure (cannot publish / cannot login / cannot fetch key data).
- S2: partial feature failure with workaround.
- S3: non-blocking defect.

## Bug Record Template
Use one section per bug:

```md
### [BUG-ID] Short title
- Date:
- Severity: S0/S1/S2/S3
- Trigger/Scenario:
- Affected modules/files:
- Root cause:
- Fix strategy (minimal scope):
- Logging/exception/retry/result-check added:
- Verification steps:
- Regression checklist:
- Status: Open / Fixed / Monitoring
```

## Regression Checklist (Minimum)
1. Original scenario reproduced and verified fixed.
2. Neighboring core flow not broken.
3. Logs are sufficient for failure diagnosis.
4. Retry and timeout behavior are explicit where needed.
5. Result correctness has at least one concrete check.

## Bug Entries

### [RBK-0001] Placeholder entry
- Date: 2026-05-07
- Severity: S3
- Trigger/Scenario: Initialize bug-history governance file.
- Affected modules/files: docs/rules governance only.
- Root cause: Historical bug records were scattered and not standardized.
- Fix strategy (minimal scope): Add a dedicated bug history rule file and index entry.
- Logging/exception/retry/result-check added: N/A (documentation layer).
- Verification steps: Verify file exists and index link is present.
- Regression checklist: Future bug fixes must append real entries.
- Status: Monitoring

### [BUG-00] Step gate must hard-stop downstream after terminal failure
- Date: 2026-05-08
- Severity: S1
- Trigger/Scenario: In dry_run report, Step8 copy generation failed but Step9 and Step10 still executed.
- Affected modules/files: `scripts/dryrun_step1_10_runner.py` (pipeline orchestration / step gate).
- Root cause:
  1. Downstream gating relied on a mutable `blocked` flag only.
  2. Gate reason format was inconsistent (`blocked_by_step_x_failure`) and not aligned with strict flow rule.
  3. There was no final reconciliation to guarantee all terminal statuses (`failed/manual_review/stopped`) enter pending-fix list.
- Fix strategy (minimal scope):
  1. Keep fix inside `run_strict_dryrun` only; no module refactor.
  2. Add `should_skip_next_step()` to gate each major step entry by actual prior step statuses.
  3. Normalize downstream skip reason to `blocked_by_previous_failure`.
  4. Add `_append_pending_fix(...)` de-dup helper and final terminal-status reconciliation before report write.
- Logging/exception/retry/result-check added:
  1. Downstream blocked status is explicitly represented via step status `skipped` + reason `blocked_by_previous_failure`.
  2. `pending_fix_list` is guaranteed to include terminal steps.
- Verification steps:
  1. Run strict dry-run in a Step8 failure scenario (copy body length out of range).
  2. Confirm Step8=`failed`; Step9/Step10=`skipped`; reason=`blocked_by_previous_failure`.
  3. Confirm publish command path was not executed.
  4. Check report path: `tmp/bug00_verify_step8_fail/step1_10_report.json`.
- Regression checklist:
  1. Any terminal step status immediately blocks downstream.
  2. Block reason remains stable: `blocked_by_previous_failure`.
  3. Terminal steps always appear in `pending_fix_list`.
- Status: Fixed

### [M-001] P0 cancel_requested 不跨账号传播
- Date: 2026-05-10
- Severity: P0
- Trigger/Scenario: 多账号编排时 P0 错误未阻止后续账号继续执行
- Affected modules/files: `scripts/workflow_orchestrator.py`
- Root cause: `run_account(acct_ctx)` 内设置 `acct_ctx.state.cancel_requested = True`，但 `run_task()` 循环检查的是首账号的 `ctx.state.cancel_requested`。两者是独立 PipelineContext 实例，P0 不传播。
- Fix strategy (minimal scope): 在 `run_task()` Phase 2 循环中，检查 `acct_result.steps` 是否有 P0 错误，有则设置 `ctx.state.cancel_requested = True` 并 break。
- Verification steps:
  1. `python -m py_compile scripts/workflow_orchestrator.py` → OK
  2. 检查代码：`acct_result.steps` P0 检查 + `ctx.state.cancel_requested = True`
- Regression checklist:
  1. P0 必须阻止所有后续账号
  2. 后续修改 orchestrator 时必须保留 cancel_requested 传播
- Status: Fixed

### [M-002] RuntimeConfig 不按 account_id 解析 CDP port
- Date: 2026-05-10
- Severity: P0
- Trigger/Scenario: 多账号时所有账号共用同一 CDP 端口
- Affected modules/files: `scripts/workflow_orchestrator.py`
- Root cause: `RuntimeConfig.from_task_input()` 不接收 account_id，`cdp_port` 始终为默认 9322。
- Fix strategy (minimal scope): 不在 `RuntimeConfig` 内修改。在 `run_task()` Phase 2 循环中调用 `core_config.resolve_account_port(account_id)` 获取账号端口，设置 `acct_ctx.config.cdp_port` 和 `os.environ["XHS_CDP_PORT"]`。
- Verification steps:
  1. `python -c "from core_config import resolve_account_port; print(resolve_account_port('acc_b'))"` → 9522
  2. 单账号 regression 不受影响（仍使用默认端口）
- Regression checklist:
  1. 各账号必须使用各自配置的 CDP 端口
  2. env var `XHS_CDP_PORT` 在 Phase 2 循环中被正确设置
- Status: Fixed

### [M-004] _run_script() 只为 cdp_publish.py 注入端口，缺少 publish_pipeline.py
- Date: 2026-05-10
- Severity: P1
- Trigger/Scenario: 多账号 Step8-Step10 调用 publish_pipeline.py 时使用默认端口而非账号端口
- Affected modules/files: `scripts/workflow_steps.py`
- Root cause: `_run_script()` 的 `if script_name == "cdp_publish.py":` 只对 cdp_publish.py 注入 `--port`。publish_pipeline.py 没有自动端口注入，导致 Phase 2 调用时使用内部默认端口(9222)而非账号端口。
- Fix strategy (minimal scope): 将条件改为 `if script_name in ("cdp_publish.py", "publish_pipeline.py"):`。配合 orchestrator 设置 `os.environ["XHS_CDP_PORT"]`，所有 Phase 2 子进程自动获得账号级端口。
- Verification steps:
  1. `python -m py_compile scripts/workflow_steps.py` → OK
  2. `python scripts/stability_guard.py --offline` → 19/19 PASS
  3. 单账号 regression 全 PASS
- Regression checklist:
  1. `_run_script()` 注入端口时不能破坏非端口脚本
  2. 新增需要端口注入的脚本时必须加入此 set
- Status: Fixed

### [M-005] config/accounts.json 中 acc_b 和 runner 端口冲突
- Date: 2026-05-10
- Severity: P2
- Trigger/Scenario: acc_b 和 runner 都配置为 9322，使用不同 Chrome profile 但同端口
- Affected modules/files: `config/accounts.json`
- Root cause: acc_b 和 runner 的 port 字段都是 9322。
- Fix strategy (minimal scope): acc_b 端口从 9322 改为 9522。
- Verification steps:
  1. `python -c "from core_config import resolve_account_port; print(resolve_account_port('acc_b'))"` → 9522
- Regression checklist:
  1. 每个账号的端口必须唯一
  2. 新增账号时不能复用已有端口
- Status: Fixed
- Date: 2026-05-10
- Severity: S1（核心流程阻断）
- Trigger/Scenario: 首次 dry-run，Step2 search-feeds 调用报 `unrecognized arguments: --account`
- Affected modules/files: `scripts/workflow_steps.py`
- Root cause: `_run_script("cdp_publish.py", [...])` 把 `--account` 放在了子命令（`search-feeds`/`check-login`/`screenshot`）之后。argparse 父解析器参数必须放在子命令名称之前，否则不识别。共 7 处相同模式。
- Fix strategy (minimal scope): 只调换 `--account` 在 args list 中的顺序，移到子命令之前。不动 cdp_publish.py，不动业务逻辑。
- Logging/exception/retry/result-check added: N/A（参数顺序调整，不涉及日志/异常/重试）
- Verification steps:
  1. `python -m py_compile scripts/workflow_steps.py` → OK
  2. `python scripts/cdp_publish.py --port 9322 --account runner search-feeds --keyword "测试"` → 不再报 unrecognized arguments
  3. 全流程 dry-run → Step2 通过，进入 Step3
- Regression checklist:
  1. 所有 `_run_script("cdp_publish.py", [...])` 调用必须 `--account` 在子命令前
  2. 新增 `cdp_publish.py` 子命令调用时必须检查参数顺序
- Status: Fixed

### [RBK-0003] Step3 --summary-json 参数不存在导致子进程崩溃
- Date: 2026-05-10
- Severity: S1（核心流程阻断）
- Trigger/Scenario: dry-run Step3 调用 step3_select_and_download_refs.py 时报 `unrecognized arguments: --summary-json`
- Affected modules/files: `scripts/step3_select_and_download_refs.py`
- Root cause: workflow_steps.py 传递 `--summary-json <path>` 给 step3 脚本，但 step3 脚本的 argparse 从未定义该参数。argparse 遇到未定义参数直接 exit(2)。
- Fix strategy (minimal scope): 只在 step3 脚本的 `main()` 中添加：
  1. `--summary-json` argparse 参数定义
  2. 执行成功后，将 result（含 selected/local_paths/downloaded_paths/reference_image_path）写入指定路径
- Logging/exception/retry/result-check added: 写入 summary JSON 供编排器读取；兼容 local_paths/downloaded_paths 两种键名
- Verification steps:
  1. `python -m py_compile scripts/step3_select_and_download_refs.py` → OK
  2. dry-run Step3 exit code 0，不再报 unrecognized arguments
  3. `{artifacts_dir}/refs/summary.json` 存在且含 local_paths
- Regression checklist:
  1. 所有 `_run_script()` 传递的参数必须被目标 argparse 定义
  2. 子进程参数必须与目标 argparse 保持契约一致
- Status: Fixed

### [RBK-0004] Step10 --live 参数不被 publish_pipeline.py 支持
- Date: 2026-05-10
- Severity: S1（核心流程阻断 — live 发布路径完全不可用）
- Trigger/Scenario: Step10 live 模式调用 publish_pipeline.py 时报 `unrecognized arguments: --live`
- Affected modules/files: `scripts/workflow_steps.py`
- Root cause: workflow_steps.py Step10 live 分支传了 `"--live"`，但 publish_pipeline.py 的 argparse 从未支持该参数。publish_pipeline.py 的设计是 `--preview` = 只填不发布，无 `--preview` = 填写+发布。
- Fix strategy (minimal scope): 只在 workflow_steps.py 中删除 live_args 里的 `"--live"`。不传 `--preview` 时 publish_pipeline.py 默认会执行发布。
- Logging/exception/retry/result-check added: N/A
- Verification steps:
  1. `python -m py_compile scripts/workflow_steps.py` → OK
  2. live gate 全部通过后调用 publish_pipeline.py 不再传未知参数
- Regression checklist:
  1. publish_pipeline.py 只支持 `--preview`/无 `--preview` 两种模式，不支持 `--live`
  2. 所有向 publish_pipeline.py 传参的地方必须检查参数是否被目标支持
- Status: Fixed

### [RBK-0005] Step4 使用过期 StepResult(output=...) 字段导致 TypeError
- Date: 2026-05-10
- Severity: S1（核心流程阻断）
- Trigger/Scenario: dry-run Step4 调用 xhs_images_to_picset.py 时报 `TypeError: StepResult.__init__() got an unexpected keyword argument 'output'`
- Affected modules/files: `scripts/xhs_images_to_picset.py`
- Root cause: xhs_images_to_picset.py:1891 在 exit 时用 `StepResult(output=...)` 构造。但 `StepResult` dataclass 没有 `output` 字段，只有 `artifacts`（dict[str, Any]）可承载附加数据。
- Fix strategy (minimal scope): 将 `output=` 改为 `artifacts=`。StepResult 的 artifacts 字段是 dict[str, Any]，设计目的即承载任意附加数据。
- Logging/exception/retry/result-check added: N/A
- Verification steps:
  1. `python -m py_compile scripts/xhs_images_to_picset.py` → OK
  2. Step4 不再报 StepResult(output=...) TypeError
  3. exit code 从 1（TypeError）变为 2（下一级错误），证明 output→artifacts 修复生效
- Regression checklist:
  1. StepResult 的可用字段在 workflow_core.py 中定义，任何地方构造 StepResult 时不能使用未定义字段
  2. StepResult 的标准数据通道：evidence / artifacts / error / created_files，非标准字段名会导致 TypeError
- Status: Fixed

### [RBK-0009] Step4 P2 重试时重新打开 Picset 浪费积分
- Date: 2026-05-10
- Severity: S2（非阻断，但持续消耗付费积分）
- Trigger/Scenario: Step4 调用 Picset 成功后返回 P2（如 summary 解析失败），orchestrator 重试 Step4 时再次打开 Picset 生图。或跨任务重跑时 Step4 无缓存检查。
- Affected modules/files: `scripts/workflow_steps.py`
- Root cause: `run_step4_generate_image()` 没有检查 `ctx.artifacts.generated_images` 是否已有有效文件。每次被调用（包括 orchestrator P2 重试）都执行完整 Picset 自动化流程。
- Fix strategy (minimal scope): 在 Step4 入口添加 checkpoint：若 `ctx.artifacts.generated_images` 非空且至少 1 个文件存在且 >1KB，跳过 Picset 调用，直接返回 success（`skipped=True`）。
- Verification steps:
  1. `python scripts/stability_guard.py --offline` → 19/19 PASS
  2. 单元验证：构造含 `generated_images` 的 mock ctx，调用 Step4 → 输出 `[step4] reuse N existing generated image(s), skip Picset`，返回 `skipped=True`
- Regression checklist:
  1. Step4 后续重构时必须保留 checkpoint 检查
  2. checkpoint 跳过后 `generated_paths` 必须与原始 `generated_images` 一致
- Status: Fixed

### [RBK-0012] Step9/Step10 调用 publish_pipeline.py 时重复刷新发布页并重新填入图文
- Date: 2026-05-10
- Severity: P2
- Trigger/Scenario: Step8 填好表单后，Step9/Step10 调用 `publish_pipeline.py` 时 `publisher.publish()` 内部 `_navigate()` 强制刷新发布页，已填表单丢失后被重新填入。用户观察到"没点发布就刷新，然后又填入图文"
- Affected modules/files: `scripts/publish_pipeline.py`（Step 4 form fill）、`scripts/workflow_steps.py`（Step9/Step10 args）
- Root cause: `publish_pipeline.py` 的 Step 4 无条件执行完整表单填充（navigate + 上传 + 填标题正文），但 Step9/Step10 根本不需要重新填充表单。Step9 只需 click_add_product，Step10 只需预览校验
- Fix strategy (minimal scope):
  1. `publish_pipeline.py` 加 `--skip-form-fill` 参数，设置时跳过 Step 4（form fill）
  2. `workflow_steps.py` Step9/Step10 args 加 `--skip-form-fill`
- Logging/exception/retry/result-check added: `--skip-form-fill` 时跳过 Step 4，不输出 "FILL_STATUS: READY_TO_PUBLISH"（Step10 的 fill_ok 检查需注意）
- Verification steps:
  1. `python -m py_compile scripts/publish_pipeline.py` → OK
  2. `python -m py_compile scripts/workflow_steps.py` → OK
  3. `python scripts/stability_guard.py --offline` → 19/19 PASS
  4. 验证运行：Step8 正常导航+上传+填充，Step9 跳过 form fill 直接 click_add_product，Step10 跳过 form fill 直接预览
- Regression checklist:
  1. `publish_pipeline.py` 的 `--skip-form-fill` 和 `--preview` 不应影响发布安全 gate
  2. 新增 `publish_pipeline.py` 调用时必须考虑是否需要 form fill
- Status: Fixed

### [RBK-0013] Step7 CREATOR_URL_INVALID 因 check_login 导航到 Creator 首页
- Date: 2026-05-10
- Severity: S1（核心流程阻断）
- Trigger/Scenario: 用真实商品名运行 pipeline 时 Step7 报 CREATOR_URL_INVALID。此前单账号成功但因 Step2 的 tab 已先打开 XHS 页面绕过此问题。
- Affected modules/files: `scripts/cdp_publish.py`（XHS_CREATOR_LOGIN_CHECK_URL）
- Root cause: `XHS_CREATOR_LOGIN_CHECK_URL = "https://creator.xiaohongshu.com"` 只导航到 Creator 首页，登录后被重定向到 dashboard（`/new/home`）。Step7 URL validation 要求 `creator.xiaohongshu.com/publish`。此前单账号成功是因为 Step2 的 tab 已在 XHS 上，不会被重定向到 dashboard。
- Fix strategy (minimal scope): 1 行改动：`XHS_CREATOR_LOGIN_CHECK_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"`，与 `XHS_CREATOR_URL` 相同，导航后直接落在发布页。
- Logging/exception/retry/result-check added: check_login() 的 URL 日志 `[cdp_publish] Current URL: ...` 现在应显示 `/publish/publish` 而非 dashboard。
- Verification steps:
  1. `python -m py_compile scripts/cdp_publish.py` → OK
  2. 全流程 dry-run Step7 → 不报 CREATOR_URL_INVALID，URL 验证通过
- Regression checklist:
  1. check_login() 后 URL 必须包含 `publish`
  2. open_login_page()/get_login_qrcode() 不受影响（它们导航到 login page 时不依赖此 URL）
  3. 新增需要检查登录状态的代码必须导航到发布页而非 Creator 首页
- Status: Fixed

### [RBK-0014] Step7 缓存命中时 login_status.json URL 为空
- Date: 2026-05-11
- Severity: P2（证据不完整）
- Trigger/Scenario: Step7 check-login 缓存命中时，`Current URL:` 不在 stdout，导致 `login_status.json` 中 `url=""`，Step7 evidence.url 也为空
- Affected modules/files: `scripts/workflow_steps.py`（run_step7_open_creator_page）
- Root cause: cdp_publish.py 的 `_get_cached_login_status()` 在缓存命中时不执行 `_navigate()` 和 URL 输出。Step7 解析 stdout 拿不到 Current URL，证据记录为空。
- Fix strategy (minimal scope): 检测 stdout 中 `Login confirmed (cached)` 标志，追加 `cache_hit` 字段到 login_status.json。URL 为空时在 evidence 层记录 `"(cached - see screenshot)"`。
- ⚠️ 第一次修复引入了回归：将 current_url 赋值为非空字符串 broke 了 URL 校验 `if current_url and "publish" not in current_url`（空字符串是 falsy 跳过校验，非空字符串触发校验必失败）。
- 修正：保持 current_url 为空字符串（不干预逻辑流），仅在 login_status.json 的 evidence 展示层使用 `"(cached - see screenshot)"`。
- 教训：修复 evidence 层问题时，绝不能动逻辑层变量。evidence 变量必须与逻辑变量分离。
- Logging/exception/retry/result-check added: login_status.json 新增 `cache_hit` bool 字段，URL 为空时显式标记缓存命中。
- Verification steps:
  1. `python -m py_compile scripts/workflow_steps.py` → OK
  2. `python scripts/stability_guard.py --offline` → 19/19 PASS
- Regression checklist:
  1. login_status.json 向后兼容（新增字段不破坏读取方）
  2. Step7 URL 校验使用 `current_url` 原始值（空字符串），不受 evidence 层改动影响
- Status: Fixed（含一次回归修正）

### [RBK-0015] Step8 manual_review 分支缺少 error 对象
- Date: 2026-05-11
- Severity: P2（manual_review 无诊断细节）
- Trigger/Scenario: `publish_pipeline.py` 返回 `returncode=0` 但 stdout 中无 `FILL_STATUS: READY_TO_PUBLISH`，Step8 设置 MANUAL_REVIEW 但 error 为 None，人工无法排查问题
- Affected modules/files: `scripts/workflow_steps.py`（run_step8_fill_publish_form）
- Root cause: Step8 重试耗尽后 `returncode=0` 但无 FILL_STATUS 的分支只设了 `result.status=MANUAL_REVIEW`，没有构造 `StepErrorPayload`，无 detail/suggestion。
- Fix strategy (minimal scope): 在 MANUAL_REVIEW 分支添加 `result.error = StepErrorPayload(...)`，含 code/message/detail(suggestion)。
- Logging/exception/retry/result-check added: Step8 manual_review 的 error 新增 code=`FILL_NOT_CONFIRMED`，detail 含 `stdout_snippet` 和 `suggestion`。
- Verification steps:
  1. `python -m py_compile scripts/workflow_steps.py` → OK
  2. `python scripts/stability_guard.py --offline` → 19/19 PASS
- Regression checklist:
  1. Step8 成功路径不受影响（error 仍为 null）
  2. retry 耗尽后其他失败分支不受影响
- Status: Fixed

### [RBK-0016] Step9 manual_review 使用 P3 作为 error_level
- Date: 2026-05-11
- Severity: P2（语义不匹配工程图）
- Trigger/Scenario: Step9 两处 manual_review 分支（exit code 4 + match_score<0.75 preview）使用 `ErrorLevel.P3.value` 作为 error_level。工程图 v1.1 定义 manual_review 不是 P0-P3 中的任何一级。
- Affected modules/files: `scripts/workflow_steps.py`（run_step9_attach_product）
- Root cause: 编码时直接将 manual_review 映射为 P3（"非关键问题，记录不中断"），但 engineering flow 语义上 manual_review 是独立的第四种状态。
- Fix strategy (minimal scope): 两处 `error_level=ErrorLevel.P3.value` 改为 `error_level=""`。orchestrator 路由由 `result.status`（MANUAL_REVIEW）驱动，不依赖 error_level，所以改后 flow 不受影响。
- Verification steps:
  1. `python -m py_compile scripts/workflow_steps.py` → OK
  2. `python scripts/stability_guard.py --offline` → 19/19 PASS
- Regression checklist:
  1. orchestrator 的 P3 处理（`level == P3 → pass`）不改，不影响其他 P3 逻辑
  2. manual_review 流程由 `result.status == MANUAL_REVIEW` 驱动，不受 error_level 影响
- Status: Fixed

### [RBK-0018] Step7 未登录时无等待轮询，直接 P1 失败
- Date: 2026-05-11
- Severity: S1（多账号时每个未登录账号都会立即失败）
- Trigger/Scenario: 多账号发布时，Step7 检测到账号未登录立即返回 P1，用户无扫码登录的时间窗口
- Affected modules/files: `scripts/workflow_steps.py`（`run_step7_open_creator_page`）
- Root cause: retry_policy 写了"登录等待 120s，10s 间隔"，但 Step7 只做了一次 `check-login` 调用，未登录就立即返回 P1 LOGIN_REQUIRED。没有轮询等待逻辑。
- Fix strategy (minimal scope): 将 Step7 登录检查从"一次检查立即返回"改为：
  1. 首次 check-login 失败后，打印清晰提示（账号名、截止时间）
  2. 每 10s 轮询 check-login（最多 120s，通过 `XHS_LOGIN_WAIT` 环境变量配置）
  3. 登录成功 → 重新解析 current_url → 继续正常流程
  4. 超时 → P1 LOGIN_REQUIRED（与改动前一致）
- 改动文件：`scripts/workflow_steps.py`
- 改动行数：14 行（+1 行 `import time`，+13 行轮询逻辑）
- 新增环境变量：`XHS_LOGIN_WAIT`（默认 120）
- 注意点：风控检测在进入等待循环之前完成，轮询期间不再重复检测风控（简化设计）。如需要可在后续迭代中在轮询内增加风控检测。
- Verification steps:
  1. `python -m py_compile scripts/workflow_steps.py` → OK
  2. `python scripts/stability_guard.py --offline` → 19/19 PASS
  3. 已登录账号：行为不变，0 延迟直接通过
  4. 未登录账号：打印提示 → 每 10s 轮询 → 扫码后自动继续
  5. 超时 120s：返回 P1 LOGIN_REQUIRED
- Regression checklist:
  1. 已登录账号必须 0 延迟通过（不进入轮询）
  2. 风控检测必须先于登录等待执行（不改风险控制逻辑）
  3. 登录成功后的 login_status.json / 截图 / URL 校验不受影响
  4. 超时后的 P1 行为与改动前一致
  5. 后续修改 Step7 时必须保留登录等待轮询逻辑
- Status: Fixed

### [RBK-0017] dry-run Picset 长时间静默无进度输出
- Date: 2026-05-11
- Severity: P3（工程流程问题）
- Trigger/Scenario: Picset 生图等待 10-20 分钟期间，dry-run stdout 只打印 `[task] Phase 1: Step1-Step6 (runner)`，无任何中间进度输出。用户检查输出时误以为进程卡死。
- Affected modules/files: `scripts/workflow_steps.py`（run_step4_generate_image），`_run_script` 同步等待模式
- Root cause: Step4 调用 `xhs_images_to_picset.py` 子进程使用 `subprocess.run()` 同步阻塞。子进程自身不输出进度到 stdout，父进程不轮询，导致 15+ 分钟无任何输出。违反"可观测、可中断、可汇报"原则。
- Fix strategy (minimal scope): 方案 A（推荐）— `_run_script` 增加超时参数，超过预计时间（Step4 设为 600s）打印警告并允许取消。方案 B（最小本步）— 在 Step4 Picset 调用前打印预计时间范围。
- 教训：
  1. 任何执行超过 5 分钟的 Step 必须有中间进度输出。
  2. 子进程同步等待期间父进程不能静默。
  3. 长时间运行命令必须设可观测的超时阈值。
- Verification steps:
  1. `python -m py_compile scripts/workflow_steps.py` → OK
  2. 本 bug 仅记录不修复，需下轮跟进
- Regression checklist:
  1. Step4 Picset 必须在 stdout 输出进度标记
  2. 超过 retry_policy 上限必须主动汇报而非静默等待
- Status: Open（建议下轮跟进修复）
