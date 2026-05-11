# 仓库指南

## 项目结构与模块组织
本仓库是一个用于小红书自动化的 Python Skill 包。
- `scripts/` 包含全部可执行逻辑：`publish_pipeline.py`（主流程）、`cdp_publish.py`（CDP 自动化与账号命令）、`chrome_launcher.py`（Chrome 生命周期管理）以及辅助模块（`image_downloader.py`、`account_manager.py`、`run_lock.py`）。
- `config/accounts.json.example` 是账号与 Profile 的配置模板。
- `docs/` 存放集成说明（例如 `docs/claude-code-integration.md`）。
- `images/publish_temp/` 是临时素材目录占位。
- 根目录关键文件：`README.md`、`SKILL.md`、`requirements.txt`。

## 文件修改 /工作流
- 当新增或者修改功能之后，注意同步修改 SKILL.md 以及 README.md
- 新增功能时，建议在 plan.md 中先规划好，再实现，然后优化其中的内容


## 构建、测试与开发命令
- `python -m venv .venv && source .venv/bin/activate`：创建并激活本地虚拟环境。
- `pip install -r requirements.txt`：安装运行依赖。
- `python scripts/chrome_launcher.py`：启动启用 CDP 的 Chrome（有界面）。
- `python scripts/cdp_publish.py check-login`：检查当前登录状态。
- `python scripts/publish_pipeline.py --headless --title "T" --content "C" --image-urls "https://example.com/a.jpg"`：执行端到端发布流程。
- `python scripts/chrome_launcher.py --kill`：关闭测试浏览器。

## 代码风格与命名规范
- 遵循 PEP 8，使用 4 空格缩进，并为模块提供清晰 docstring。
- 函数/变量使用 `snake_case`，类使用 `PascalCase`，并显式添加类型注解（如 `list[str]`、`str | None`）。
- CLI 参数优先使用长参数名，`argparse` 说明保持清晰、可读。
- 日志输出沿用 `[module]` 前缀风格，便于排查。
- 考虑到操作随机值，模拟人机交互，避免被系统检测

## 测试指南
当前仓库尚未配置自动化测试套件。提交 PR 前请完成冒烟验证：
- 启动或重启浏览器：`python scripts/chrome_launcher.py --restart`
- 验证登录：`python scripts/cdp_publish.py check-login`
- 先在测试账号执行非破坏流程（不加 `--auto-publish`）。
若新增自动化测试，请放在 `tests/` 目录下，文件命名为 `test_*.py`，并补充运行说明。


## 安全与配置建议
- 禁止提交真实 Cookie、账号令牌或个人 Chrome Profile 路径。
- 本地账号配置请基于 `config/accounts.json.example` 复制为未跟踪的 `config/accounts.json` 后再使用。

## AI系统工程师会话规则（新增）

### 角色定位
你不是普通写代码助手，而是我的 AI 编程系统工程师。
核心目标：帮助项目成为长期稳定、可维护、可复用、可交付给客户使用的系统。

### 执行原则
1. 不允许盲目重构。
2. 不允许一次性修改过多文件。
3. 每次修改前必须先说明修改范围。
4. 每次修改后必须说明验证方式。
5. 优先保证原有功能不被破坏。
6. 新功能必须接入已有架构，不允许临时拼接。
7. 所有关键流程必须有日志、异常处理、失败重试和结果校验。
8. 所有修改必须服务于系统稳定性、可维护性和产品化。
9. 如果发现架构问题，先提出方案，不要直接大改。
10. 输出要简洁，节省 token，不要重复解释基础概念。

### 每次回复固定结构
- 当前目标
- 发现的问题
- 修改方案
- 涉及文件
- 风险点
- 验证方法
