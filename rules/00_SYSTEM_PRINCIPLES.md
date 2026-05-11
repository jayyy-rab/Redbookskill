# 00 — 系统核心原则

## 一句话原则

所有步骤只能读同一个上下文，所有结果只能写同一个结果结构，所有失败必须有证据，所有发布必须受规则保护。

## 五统一

1. **统一上下文 `ctx`** — 所有函数接收 `PipelineContext`，禁止散传变量
2. **统一变量字典** — 禁止创造同义变量，所有变量名见 `01_VARIABLE_DICTIONARY.md`
3. **统一函数字典** — 所有步骤函数固定命名，见 `02_FUNCTION_DICTIONARY.md`
4. **统一步骤契约** — 每一步返回 `StepResult`，见 `03_STEP_CONTRACTS.md`
5. **统一结果和错误输出** — `result.json` / `bugs.json`，见 `06_OUTPUT_SCHEMA.md`

## 架构边界

- **Dify / 表单** → 只负责收集输入和展示结果
- **FastAPI** → 只负责任务创建、保存 input.json、启动 Worker、查询结果
- **Python skill** → 负责真实自动化执行（Chrome / 小红书 / CDP）

**禁止：** Dify 直接操作 Chrome、小红书页面、CDP。

## 目录规范

所有任务输出在 `runs/{task_id}/`，按账号隔离。禁止写入 `tmp/`、桌面、下载目录。

## 安全红线

- 客户不能看到源码 / cookies / 账号密码
- 客户隔离：不同客户的任务目录、账号、截图、日志完全隔离
- live 发布必须 `allow_live_publish = true`
- 商品不匹配禁止 live 发布
- 账号未登录不继续执行
- 风控 / 验证码不绕过

## 版本路线

- v0.1：工程骨架（input.json / runs/ / result.json / bugs.json / StepResult）
- v0.2：单账号 preview（Step1-Step10 跑通，不点击发布）
- v0.3：多账号顺序执行
- v1.0：客户可用 MVP（Dify + FastAPI + Worker）
- v1.1：失败重跑 / 断点续跑
- v1.2：live 发布（需授权）
