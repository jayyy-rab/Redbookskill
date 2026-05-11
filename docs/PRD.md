# 小红书自动化工程 PRD（稳定版）

## 1. 目标
- 降低改动后“东边改完西边坏”的概率。
- 支持定位问题到具体步骤，不再每次全流程重跑。
- 支持多账号轮番发布，单账号失败时自动切到下一个账号。

## 2. 范围
- 在现有脚本基础上补工程治理能力，不重写业务流程。
- 重点改造：
1. 分步执行：`--from-step` / `--to-step`
2. 干跑预检：`--dry-run`
3. 最小冒烟：`scripts/smoke_runner.py`
4. 统一报告：`tmp/workflow_report_*.json`、`tmp/bulk_publish_report_*.json`、`tmp/smoke_report_latest.json`

## 3. 非目标
- 不承诺解决平台页面所有动态变化问题。
- 不做云端调度系统和可视化后台。
- 不做反检测策略扩展。

## 4. 用户故事
1. 作为操作者，我要只重跑某一步，而不是每次重跑全链路。
2. 作为维护者，我要在 1 个报告里快速看到失败步骤和错误尾日志。
3. 作为交付方，我要在发版前一键做最小回归检查。

## 5. 功能需求
### FR-1 分步执行
- 文件：`scripts/full_stack_orchestrated.py`
- 新参数：
1. `--from-step {a,b,c}`
2. `--to-step {a,b,c}`
- 规则：`from <= to`，否则报错。

### FR-2 干跑预检
- 文件：`scripts/full_stack_orchestrated.py`
- 新参数：`--dry-run`
- 行为：
1. Step A 不调用外部生图流程。
2. Step B 生成占位标题/正文文件，校验产物链路。
3. Step C 不调用真实发布，仅返回预期命令。

### FR-3 多账号轮番显式开关
- 文件：`scripts/bulk_publish_accounts.py`
- 新参数：`--round-robin`
- 行为：等价于 `--group-size 1`，账号按窗口轮转执行。

### FR-4 最小冒烟脚本
- 文件：`scripts/smoke_runner.py`
- 默认检查：
1. 登录健康检查
2. 编排干跑（B->C）
3. 多账号轮番预览发布（不点最终发布）

## 6. 验收标准
1. `full_stack_orchestrated.py -h` 可看到新参数。
2. `--from-step b --to-step c --dry-run` 可输出 workflow 报告并返回 0。
3. `bulk_publish_accounts.py --round-robin ...` 可按账号轮换执行。
4. `scripts/smoke_runner.py` 可输出 `tmp/smoke_report_latest.json`。

## 7. 风险与缓解
- 风险：小红书页面改版导致 Step C 不稳定。
- 缓解：保留 `--preview` + `--continue-on-failure` + 尾日志报告，先保证“不中断全局批次”。

## 8. 发布与回滚
- 发布：先跑 `scripts/smoke_runner.py`，再跑真实批量任务。
- 回滚：保留上个稳定脚本版本并通过 git 回退。

