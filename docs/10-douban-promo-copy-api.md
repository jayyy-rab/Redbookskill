# 文案生成：`douban_promo_copy.py`（自建 HTTP 或火山方舟 Ark）

脚本文件名仍为 `douban_promo_copy.py`（历史命名）。文案来源二选一：

| 模式 | 说明 |
|------|------|
| **`--provider http`**（默认） | 自建服务 POST JSON（见下文协议），需 **`DOUBAN_PROMO_API_URL`** |
| **`--provider ark`** | **豆包**：调用方舟 **Chat Completions**（与商品说明 + 配图多模态），需 **`ARK_API_KEY`** + **`ARK_MODEL`**（控制台「推理接入点」ID） |

**永远不要**把 API Key 写进仓库、Skill 或聊天记录；只放在本机环境变量或系统密钥库。密钥一旦泄露请在火山引擎控制台**轮换**。

## A) 火山方舟 · 豆包（推荐：与商品图对齐写种草文案）

| 变量 | 含义 |
|------|------|
| `ARK_API_KEY` | 方舟 API Key（`Bearer`） |
| `ARK_MODEL` | **推理接入点 ID**（在火山「方舟」控制台创建端点后复制，**不是**随便猜的模型名） |
| `ARK_BASE_URL` | 可选，默认 `https://ark.cn-beijing.volces.com/api/v3`（与控制台区域/端点一致） |
| `ARK_BODY_MIN_CHARS` / `ARK_BODY_MAX_CHARS` | 可选；正文 **body** 字数（默认约 **95～100 字**，上限 **100** 含标点与换行；超出会截断） |
| `ARK_TITLE_MAX_CHARS` / `--ark-title-max` | 可选；**title** 最长字数（默认 **18**，按 Unicode 字符计）；与提示词、写入前截断一致 |
| `DOUBAN_PROMO_TIMEOUT` | 与 http 模式共用，秒，默认 `120` |

```powershell
$env:ARK_API_KEY = "你的_KEY_从控制台复制"
$env:ARK_MODEL = "ep-xxxx"
$env:DOUBAN_PROMO_PROVIDER = "ark"

Set-Location "C:\path\to\redbookskills"
python scripts/douban_promo_copy.py --provider ark `
  --brief-file "C:\path\商品与卖点.txt" `
  --images "C:\path\主图1.jpg" "C:\path\主图2.jpg" `
  --seed-keyword "茶叶" `
  --out-dir "C:\path\xhs_promo_out"
```

无图时也可只传 `--brief` / `--brief-file`（纯文本生成）。`--dump-raw-response` 可保存 Ark 完整 JSON 便于排错。

## B) 自建 HTTP 推广服务

| 变量 | 含义 |
|------|------|
| `DOUBAN_PROMO_API_URL` | POST 接口地址（**`--provider http` 时必填**，除非 `--dry-run`） |
| `DOUBAN_PROMO_API_KEY` | 可选；有则带 `Authorization: Bearer …` |
| `DOUBAN_PROMO_TIMEOUT` | 可选；秒，默认 `120` |
| `DOUBAN_PROMO_VERIFY_SSL` | 可选；`0` / `false` 关闭 TLS 校验（仅本机调试） |

**请求 JSON（脚本自动发送）**

- `intent`: 固定 `"xhs_promo"`
- `brief`: 活动/产品/语气说明（`--brief` 或 `--brief-file` UTF-8）
- `seed_keyword`: 可选，与种子词、Picset 策略对齐
- `images`: 本地图转 `data_base64` + `mime_type` + `filename`；可 0 张（仅 brief）

**响应 JSON（脚本尽量兼容多种字段名）**

- **标题**：`title` / `subject` / `xhs_title` / `headline`
- **正文（不含话题行）**：`body` / `content` / `text` / `copy` / `article`
- **话题**：`tags` — 数组 `["话题A","话题B"]` 或字符串 `"#话题A #话题B"`；若 `content` **最后一整行**已是 `#a #b` 格式，则按 `publish_pipeline` 规则解析，不再拼 `tags`

写出文件：`title.txt`；`content.txt`：**正文 + 空行 + 最后一行话题**（最后一行必须是 `#标签1 #标签2` 空格分隔）。

```powershell
$env:DOUBAN_PROMO_API_URL = "https://your-api.example.com/v1/xhs-promo"
$env:DOUBAN_PROMO_API_KEY = "your-secret"

Set-Location "C:\path\to\redbookskills"
python scripts/douban_promo_copy.py --provider http `
  --brief-file "C:\path\campaign.txt" `
  --images "C:\path\cover1.jpg" "C:\path\cover2.jpg" `
  --seed-keyword "茶叶" `
  --out-dir "C:\path\xhs_promo_out"

python scripts/douban_promo_copy.py --dry-run --brief "占位" --out-dir "C:\temp\promo_try"
```

说明：stdout 含 `[douban_promo] OUT_DIR=...`，供 **`promo_publish_one_shot.py`** 解析。

**自建 HTTP（`http`）**标题过长时对「显示宽度」**stderr 警告**（约 **38**）；**豆包（`ark`）** 默认 **≤18 字**，模型仍超长则由脚本截断并警告。
