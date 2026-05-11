# 01 — 统一变量字典

AI 不允许创造同义变量。所有变量从 `PipelineContext` 或其注册表取。

## 输入变量（ctx.input）

| 变量 | 类型 | 说明 |
|------|------|------|
| `task_id` | str | 任务唯一 ID |
| `client_id` | str | 客户 ID |
| `product_images` | list[str] | 产品图片路径列表 |
| `seed_keyword` | str | 小红书搜索关键词 |
| `product_name` | str | 需挂载的商品名 |
| `product_id` | str | 需挂载的商品 ID |
| `brief` | str | 文案补充提示词 |
| `accounts` | list[str] | 发布账号列表 |
| `publish_mode` | str | "preview" 或 "live" |
| `allow_no_product` | bool | 是否允许无商品 |
| `allow_reference_fallback` | bool | 是否允许参考图兜底 |
| `allow_live_publish` | bool | 是否授权 live 发布 |

## 配置变量（ctx.config / ctx.input.risk_control）

| 变量 | 默认 | 说明 |
|------|------|------|
| `ui_retry` | 2 | UI 操作最大重试 |
| `network_retry` | 3 | 网络请求最大重试 |
| `generation_retry` | 1 | 图片生成最大重试 |
| `stop_on_p0` | true | P0 错误时停止任务 |
| `stop_account_on_p1` | true | P1 错误时停止当前账号 |
| `max_body_chars` | 180 | 正文最大字数 |
| `min_body_chars` | 80 | 正文最小学数 |
| `forbidden_words` | [] | 违禁词列表 |

## 图片产物变量（ctx.artifacts）

| 变量 | 说明 |
|------|------|
| `product_images` | 客户原始产品图 |
| `reference_images` | 从小红书下载的参考图 |
| `generated_images` | Picset 生成的图片 |
| `final_images` | 调色后的最终上传图片 |

## 文案变量（ctx.artifacts）

| 变量 | 说明 |
|------|------|
| `title_text` | 小红书标题 |
| `body_text` | 小红书正文 |
| `topics_text` | 话题标签 |
| `copywriting_json_path` | 文案 JSON 文件路径 |

## 路径变量（ctx.paths）

| 变量 | 说明 |
|------|------|
| `run_root` | runs/ |
| `task_dir` | runs/{task_id}/ |
| `input_dir` | runs/{task_id}/input/ |
| `account_dir` | runs/{task_id}/accounts/{account_id}/ |
| `logs_dir` | .../logs/ |
| `screenshots_dir` | .../screenshots/ |
| `artifacts_dir` | .../artifacts/ |
| `evidence_dir` | .../evidence/ |

## 状态变量（ctx.state）

| 变量 | 说明 |
|------|------|
| `task_status` | pending / running / completed / failed / cancelled |
| `account_status` | pending / running / success / failed / manual_review |
| `current_step` | 当前步骤编号 |
| `current_step_name` | 当前步骤名 |
| `retry_count` | 当前重试次数 |
| `failure_reason` | 失败原因 |
| `error_level` | 当前错误等级 |
| `cancel_requested` | 是否已请求取消 |
