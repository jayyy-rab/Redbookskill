# 多账号批量轮发 · zero_touch · 时间与分组

## 一句话全自动：`zero_touch_xhs.py`

用编排脚本 **`scripts/zero_touch_xhs.py`**：把活动说明写成**一段中文**（会作为豆包的 **brief**，并尽量自动抽出 **小红书/Picset 关键词**），再指明**产品素材图**。

```powershell
Set-Location "C:\path\to\redbookskills"
python scripts/zero_touch_xhs.py `
  --speech "关键词茶叶，要全账号铺量，语气真实种草，合规不夸大" `
  --product-images "D:\product_tea.png"
```

- **关键词怎么来**：优先识别「关键词 / 主题 / 搜索词 / 赛道」后的词，或正文里的 `#话题`；否则在常见类目词里做子串匹配；再不行兜底为 `好物`。想手工指定则加 **`--seed-keyword 茶叶`**。  
- **产品图**：必传 **`--product-images`**；也可设环境变量 **`REDBOOK_ZERO_TOUCH_PRODUCT_IMAGE`**（多个路径用**英文逗号**分隔）。  
- **只演练不点发布**：可把 `bulk_publish_accounts` 未识别的参数原样转发，例如追加 **`--preview`**、**`--accounts acc_a acc_b`** 等。  
- **`--dry-run`**：仅打印推导出的关键词、brief 路径与将要执行的命令。  
- **Picset→Photoshop→小红书**：在 `zero_touch` 或 `bulk` 上乘 **`--photoshop-after-generate`**。

仍须本机：**Chrome/CDP + 小红书与 Picset 已登录**，以及 **`ARK_API_KEY` + `ARK_MODEL`**。

## `bulk_publish_accounts.py`：账号与环境

先给账号配置 `proxy/port/group`：

```powershell
python scripts/cdp_publish.py add-account acc001 --alias "账号1" --proxy "http://127.0.0.1:9001" --port 9222 --group A
python scripts/cdp_publish.py add-account acc002 --alias "账号2" --proxy "http://127.0.0.1:9002" --port 9322 --group A
python scripts/cdp_publish.py list-accounts
python scripts/account_manager.py list
```

## 批量轮发示例

```powershell
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --max-download 4 `
  --picset-batch-size 4 `
  --step-a-retries 2
```

分组与时间窗示例：

```powershell
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --max-download 4 `
  --picset-batch-size 4 `
  --group-size 20 `
  --group-window-seconds 3600 `
  --sleep-min 8 `
  --sleep-max 20
```

只跑指定组 + 补偿重跑：

```powershell
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --only-groups A `
  --retry-failed-pass 1
```

按计划时间窗：

```powershell
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --group-window-plan-file "config\group_windows.json.example" `
  --retry-failed-pass 1
```

错过槽位不等待：

```powershell
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --group-window-plan-file "config\group_windows.json.example" `
  --slot-grace-seconds 120 `
  --no-wait-when-missed-slot `
  --retry-failed-pass 1
```

## `group_windows.json` 结构示例

```json
{
  "interval_seconds": 3600,
  "groups": {
    "A": { "offset_seconds": 0 },
    "B": { "offset_seconds": 1800 }
  },
  "default": { "offset_seconds": 0 }
}
```

## 常用参数

- `--accounts a1 a2 ...`
- `--only-groups g1 g2 ...`
- `--max-accounts N`
- `--preview`
- `--retries N` / `--retry-failed-pass N`
- `--sleep-min/--sleep-max`
- `--group-size N` / `--group-window-seconds S`
- `--group-window-plan-file FILE`
- `--slot-grace-seconds N` / `--no-wait-when-missed-slot`
- `--skip-prepare --title-file --content-file --images ...`

说明：

- 批量脚本会按账号配置里的 `port` 调 `publish_pipeline.py`；未配置时回退全局 `--port`。
- 浏览器拉起时会自动读取账号 `proxy` 并注入 `--proxy-server=...`，实现账号级网络出口隔离。
- 时间窗规则：`interval_seconds` 为循环周期；每个组的 `offset_seconds` 是周期内偏移秒数（从 Unix 时间轴对齐）。
- 脚本会在 `tmp/` 生成 `bulk_publish_report_*.json`，记录主流程成功/失败，以及补偿重跑结果与错误尾日志。
