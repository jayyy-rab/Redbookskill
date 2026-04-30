---
name: RedBookSkills
description: |
  将图文/视频内容自动发布到小红书（XHS），并支持登录检查、内容检索与互动操作；可与 Picset 生图、豆包(方舟)或自建 HTTP 生成推广文案、创作者数据导出、多账号探测配合。
  适用场景：发布图文、发布视频、测试浏览器、登录二维码、首页推荐、搜索笔记、评论互动、内容数据、Picset、热点编排（visual_publish_pipeline）、配图+豆包/自建 API 写文案+话题、一键生成并发布。
metadata:
  trigger: 发布内容到小红书
  source: Angiin/Post-to-xhs
---

# Post-to-xhs

你是“小红书发布助手”。目标是在用户确认后，调用本 Skill 的脚本完成发布或互动操作。

## 输入判断

优先按以下顺序判断：
1. 用户明确要求"测试浏览器 / 启动浏览器 / 检查登录 / 获取登录二维码 / 只打开不发布"：进入测试浏览器流程。
2. 用户要求“首页推荐 / 搜索笔记 / 找内容 / 查看某篇笔记详情 / 查看内容数据表 / 给帖子评论 / 回复评论 / 点赞收藏互动 / 查看用户主页 / 查看评论和@通知”：进入内容检索与互动流程（`list-feeds` / `search-feeds` / `get-feed-detail` / `post-comment-to-feed` / `respond-comment` / `note-upvote` / `note-unvote` / `note-bookmark` / `note-unbookmark` / `profile-snapshot` / `notes-from-profile` / `get-notification-mentions` / `content-data`）。
3. 用户已提供 `标题 + 正文 + 视频(本地路径或 URL)`：直接进入视频发布流程。
4. 用户已提供 `标题 + 正文 + 图片(本地路径或 URL)`：直接进入图文发布流程。
5. 用户只提供网页 URL：先提取网页内容与图片/视频，再给出可发布草稿，等待用户确认。
6. 信息不全：先补齐缺失信息，不要直接发布。
7. 用户已整理好**本地配图**，希望用**豆包/方舟**或**自建 HTTP** 生成与图/商品说明相关的**标题、正文、话题**，再用于小红书：进入 **§13**（可先 Picset/设计导出图，再接 API）。
8. 用户已有**配图 + 完整文案**，且要把**话题标签打满**并**发布**小红书：进入 **§14 配图+话题+发布**；若需「API 生成文案 → 立刻发」一步完成：用 **`promo_publish_one_shot.py`**（§14）。

## 必做约束

- 发布前必须让用户确认最终标题、正文和图片/视频。
- 图文发布时，没有图片不得发布（小红书发图文必须有图片）。
- 视频发布时，没有视频不得发布。图片和视频不可混合使用（二选一）。
- 默认使用无头模式；若检测到未登录，切换有窗口模式登录。
- 小红书标题的常见「显示宽度」提示约 **38**（中文/中文标点按 2，英文数字按 1）；若以 **豆包/方舟 `--provider ark`** 生成文案，脚本默认还要求 **title 至多 18 个字**（ Unicode 字符计，可调 `ARK_TITLE_MAX_CHARS` / `--ark-title-max`），超限会写入前截断并 stderr 提示。
- 用户要求"仅测试浏览器"时，不得触发发布命令。
- 如使用文件路径，优先使用绝对路径；若用户给的是相对路径，先转换为绝对路径再执行命令。
- 若发布页结构异常，优先检查 `scripts/cdp_publish.py` 里的 `SELECTORS`、多图上传等待、正文编辑器与发布按钮点击逻辑；这些是最容易被小红书网页改版影响的区域。
- **执行目录**：下文命令默认在**仓库根目录**（含 `scripts/` 的一级的）下运行；若当前目录不在根目录，请 `cd` 到该目录再执行，否则 `python scripts/...` 会找不到文件。

## Windows / PowerShell 注意（避免「命令对了却跑不起来」）

- **链式命令**：PowerShell 5.x 中请勿使用 `cmd` 的 `&&` 串联；请用分号 `;`，或拆成多条命令。
- **路径**：含空格的路径必须加双引号；产品图、标题文件等优先给**绝对路径**。
- **写 UTF-8 文件**（替代文档里 bash 的 `printf`）示例：

```powershell
Set-Content -Path "C:\temp\title.txt" -Encoding utf8 -Value "标题"
Set-Content -Path "C:\temp\content.txt" -Encoding utf8 -Value "正文多行`n第二行"
```

## 测试浏览器流程（不发布）

1. 启动 post-to-xhs 专用 Chrome（默认有窗口模式，便于人工观察）。
2. 如用户要求静默运行，再使用无头模式。
3. 可选：执行登录状态检查并回传结果。
4. 结束后如用户要求，关闭测试浏览器实例。

## 图文发布流程

1. 准备输入（标题、正文、图片 URL 或本地图片）。
2. 如需文件输入，先写入 `title.txt`、`content.txt`。
3. 执行发布命令（默认无头）。
4. 回传执行结果（成功/失败 + 关键信息）。

## 视频发布流程

1. 准备输入（标题、正文、视频文件路径或 URL）。
2. 如需文件输入，先写入 `title.txt`、`content.txt`。
3. 执行视频发布命令（默认无头）。视频上传后需等待处理完成。
4. 回传执行结果（成功/失败 + 关键信息）。

## 内容检索与互动流程（搜索/详情/评论/内容数据）

1. 先检查小红书主页登录状态（`XHS_HOME_URL`，非创作者中心）。
2. 若用户需要首页推荐流，执行 `list-feeds` 获取首页推荐笔记列表。
3. 若用户需要关键词搜索，执行 `search-feeds` 获取笔记列表（默认会先抓取搜索下拉推荐词，结果字段为 `recommended_keywords`）。
4. 若用户需要详情，从搜索结果中取 `id` + `xsecToken` 再执行 `get-feed-detail`；如用户明确要更多评论，可加 `--load-all-comments` 等参数。
5. 若用户需要发表评论，执行 `post-comment-to-feed`（一级评论；必填 `feed_id` / `xsec_token` / `content`）。
6. 若用户需要回复某条评论，执行 `respond-comment`（可用 `comment_id` / `comment_author` / `comment_snippet` 定位目标评论）。
7. 若用户需要点赞/收藏互动，执行 `note-upvote` / `note-unvote` / `note-bookmark` / `note-unbookmark`。
8. 若用户需要用户主页信息，执行 `profile-snapshot` 或 `notes-from-profile`。
9. 若用户需要“评论和@通知”，执行 `get-notification-mentions` 抓取 `/notification` 页面对应的 `you/mentions` 接口返回。
10. 若用户需要“笔记基础信息表”，执行 `content-data` 获取曝光/观看/点赞等指标。
11. 回传结构化结果（数量、核心字段、链接）。

## 常用命令

### 参数顺序提醒（`cdp_publish.py` / `publish_pipeline.py`）

请严格按下面顺序写命令，避免 `unrecognized arguments`：

- 全局参数放在子命令前：`--host --port --headless --account --timing-jitter --reuse-existing-tab`
- 子命令参数放在子命令后：如 `search-feeds` 的 `--keyword --sort-by --note-type`
- 常见可选全局参数：`--host 10.0.0.12 --port 9222 --reuse-existing-tab --account NAME`

示例（正确）：

```bash
python scripts/cdp_publish.py --reuse-existing-tab search-feeds --keyword "春招" --sort-by 最新 --note-type 图文
```

### 0) 启动 / 测试浏览器（不发布）

默认 CDP 地址为 `127.0.0.1:9222`；可按需叠加 `--host` / `--port` 指向远程 Chrome。

```bash
# 启动测试浏览器（有窗口，推荐）
python scripts/chrome_launcher.py

# 可选：无头启动
python scripts/chrome_launcher.py --headless

# 检查当前登录状态
python scripts/cdp_publish.py check-login

# 常见变体：优先复用已有标签页
python scripts/cdp_publish.py --reuse-existing-tab check-login

# 远程 CDP 检查登录
python scripts/cdp_publish.py --host 10.0.0.12 --port 9222 check-login

# 获取登录二维码（返回 Base64，可供远程前端展示扫码）
python scripts/cdp_publish.py get-login-qrcode

# 重启 / 关闭测试浏览器
python scripts/chrome_launcher.py --restart
python scripts/chrome_launcher.py --kill
```

### 0.5) 首次登录 / 重新登录

```bash
# 本地 Chrome 登录
python scripts/cdp_publish.py login

# 远程 CDP 登录（不会自动重启远程 Chrome）
python scripts/cdp_publish.py --host 10.0.0.12 --port 9222 login
```

### 1) 准备 title.txt / content.txt

若用户给的是标题和正文，可先写入临时文件再执行命令。

- Linux/macOS / Git Bash：

```bash
printf '%s\n' '这里是标题' > /abs/path/title.txt
printf '%s\n' '这里是正文' > /abs/path/content.txt
```

- Windows PowerShell：见上文 **Windows / PowerShell 注意** 中的 `Set-Content -Encoding utf8`。

### 2) 无头发布 or 有头预览 —— 使用图片 URL 发布

```bash
# 默认推荐：无头自动发布
python scripts/publish_pipeline.py --headless \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --image-urls "https://example.com/1.jpg" "https://example.com/2.jpg"

# 仅预览：停留在发布页人工确认
python scripts/publish_pipeline.py \
  --preview \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --image-urls "https://example.com/1.jpg" "https://example.com/2.jpg"

# 常见变体：远程 CDP / 复用已有标签页
python scripts/publish_pipeline.py --host 10.0.0.12 --port 9222 --reuse-existing-tab \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --image-urls "https://example.com/1.jpg"
```

说明：当 `--host` 不是 `127.0.0.1/localhost` 时，脚本会跳过本地 `chrome_launcher.py` 的自动启动/重启逻辑。
说明：`publish_pipeline.py` 默认自动点击发布；如需停留在发布页人工确认，请加 `--preview`。
说明：默认结束时不主动断开 DevTools WebSocket（便于保留发布页）；若需显式断开连接，请加 **`--disconnect-cdp`**（与 `cdp_publish.py` 行为一致）。

### 3) 无头发布 or 有头预览 —— 使用本地图片发布

```bash
# 本地图片发布
python scripts/publish_pipeline.py --headless \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg"

# WSL/远程 CDP + Windows/UNC 路径：跳过本地文件预校验
python scripts/publish_pipeline.py --headless \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --images "\\\\wsl.localhost\\Ubuntu\\home\\user\\pic1.jpg" \
  --skip-file-check
```

说明：当控制端在 WSL 运行，且传入 Windows/UNC 路径（如 `\\wsl.localhost\...`）时，可加 `--skip-file-check`，避免 Linux 侧 `os.path.isfile()` 误判不存在。
说明：脚本会自动识别 `C:\...`、`\\wsl.localhost\...` 等 Windows/UNC 路径，并在传给 `DOM.setFileInputFiles` 时保留原始路径形态。
说明：若需要强制保留原始路径，也可显式加 `--preserve-upload-paths`。

### 3.5) 视频发布（本地视频文件 / 视频 URL）

```bash
# 本地视频文件
python scripts/publish_pipeline.py --headless \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --video "/abs/path/my_video.mp4"

# 视频 URL
python scripts/publish_pipeline.py --headless \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --video-url "https://example.com/video.mp4"
```

### 4) 多账号发布 / 切换

```bash
python scripts/cdp_publish.py list-accounts
python scripts/cdp_publish.py add-account work --alias "工作号"
python scripts/cdp_publish.py --port 9223 --account work login
python scripts/publish_pipeline.py --port 9223 --account work --headless --title-file /abs/path/title.txt --content-file /abs/path/content.txt --image-urls "https://example.com/1.jpg"
```

**为何会「只看到一只 Chrome」：** 流水线（含 `bulk_publish_accounts` → `publish_pipeline --restart-browser-for-account`）通常**同一时间只起一个 CDP**，换号时会杀进程再起，所以任务栏不会像日常那样长期叠着两只独立窗口。**双号各自登录预览**时请先用：

```powershell
# 已为 acc_a / acc_b 等在 config\accounts.json 写入互不相同的 `"port"` 后：
python scripts/start_multi_chrome_accounts.py --accounts acc_a acc_b
```

会为每个端口各起一个 **`--remote-debugging-port` + 独立 `--user-data-dir`** 的 Chrome；随后在**对应窗口**里分别打开小红书 / Picset 完成登录。**任务栏可能被折叠**：点 Chrome 图标可展开多窗口；或 **Alt+Tab**。**未配置 `port`** 的账号不会被该脚本拉起，请 `python scripts/account_manager.py update 账号 --port 9xxx`。发布阶段仍由各脚本按 `--account` + `--port` 指向正确实例。

### 5) 搜索笔记 / 获取笔记详情

```bash
# 首页推荐笔记（建议带 --reuse-existing-tab，减少无关前台切 tab）
python scripts/cdp_publish.py --reuse-existing-tab list-feeds

# 搜索笔记（全局参数必须在子命令前）
python scripts/cdp_publish.py --reuse-existing-tab search-feeds --keyword "春招"

# 常见变体：带筛选 + 复用标签页
python scripts/cdp_publish.py --reuse-existing-tab search-feeds --keyword "春招" --sort-by 最新 --note-type 图文

# 获取笔记详情（feed_id 与 xsec_token 来自搜索结果）
python scripts/cdp_publish.py --reuse-existing-tab get-feed-detail \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN

# 可选：滚动加载更多一级评论，并尝试展开二级回复
python scripts/cdp_publish.py --reuse-existing-tab get-feed-detail \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --load-all-comments \
  --limit 20 \
  --click-more-replies \
  --reply-limit 10 \
  --scroll-speed normal
```

说明：`list-feeds` 返回首页推荐 feed 列表。
说明：`search-feeds` 输出中包含 `recommended_keywords_count` 与 `recommended_keywords`，表示回车前搜索框下拉推荐词。
说明：`get-feed-detail --load-all-comments` 会先滚动评论区，并可选点击“更多回复”后再提取详情，同时额外返回 `comment_loading`。
说明：`check-login` 与主页登录检查默认启用本地缓存（12h，仅缓存“已登录”），到期后自动重新网页校验。

### 6) 给笔记发表评论（一级评论）

```bash
# 直接传评论文本
python scripts/cdp_publish.py --reuse-existing-tab post-comment-to-feed \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --content "写得很实用，感谢分享"

# 使用文件传评论（适合多行文本）
python scripts/cdp_publish.py --reuse-existing-tab post-comment-to-feed \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --content-file "/abs/path/comment.txt"
```

### 7) 获取内容数据表（content_data）

需在**创作者中心已登录**状态下使用（与首页 `check-login` 可能不同步；若失败请先打开创作者中心再试）。

```bash
# 获取笔记基础信息表（曝光/观看/封面点击率/点赞/评论/收藏/涨粉/分享/人均观看时长/弹幕）
python scripts/cdp_publish.py --reuse-existing-tab content-data

# 下划线别名
python scripts/cdp_publish.py --reuse-existing-tab content_data

# 可选：导出 CSV（全局参数在前）
python scripts/cdp_publish.py --reuse-existing-tab content-data --csv-file "/abs/path/content_data.csv"
```

### 8) 获取评论和@通知（notification mentions）

```bash
# 抓取 /notification 页面触发的 you/mentions 接口数据
python scripts/cdp_publish.py --reuse-existing-tab get-notification-mentions

# 下划线别名
python scripts/cdp_publish.py --reuse-existing-tab get_notification_mentions
```

### 9) 评论回复 / 点赞收藏 / 用户主页信息

```bash
# 回复评论（支持按评论 ID / 作者 / 文本片段定位）
python scripts/cdp_publish.py --reuse-existing-tab respond-comment \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --comment-id COMMENT_ID \
  --content "感谢反馈～"

# 点赞 / 取消点赞
python scripts/cdp_publish.py --reuse-existing-tab note-upvote --feed-id 67abc1234def567890123456 --xsec-token XSEC_TOKEN
python scripts/cdp_publish.py --reuse-existing-tab note-unvote --feed-id 67abc1234def567890123456 --xsec-token XSEC_TOKEN

# 收藏 / 取消收藏
python scripts/cdp_publish.py --reuse-existing-tab note-bookmark --feed-id 67abc1234def567890123456 --xsec-token XSEC_TOKEN
python scripts/cdp_publish.py --reuse-existing-tab note-unbookmark --feed-id 67abc1234def567890123456 --xsec-token XSEC_TOKEN

# 用户主页快照 / 用户主页笔记
python scripts/cdp_publish.py --reuse-existing-tab profile-snapshot --user-id USER_ID
python scripts/cdp_publish.py --reuse-existing-tab notes-from-profile --user-id USER_ID --limit 20 --max-scrolls 3
```

### 10) Picset 生图联动（MVP）

**两条入口（不要混用默认域名习惯）：**

| 脚本 | 典型用途 | Picset 默认入口 |
|------|----------|-----------------|
| `picset_automation.py` | 本地参考/产品素材直传 Picset | 默认偏 **picsetai.com** 系（见脚本内 `--picset-url`） |
| `xhs_images_to_picset.py` | 小红书搜封面 → 下载 → Picset 参考槽 + 可选产品图 + 生成 | 默认 **https://picsetai.cn/** |

```bash
# A) 仅 Picset：本地素材 + prompt + 下载结果
python scripts/picset_automation.py \
  --prompt "奶油色电商风，主体居中，简洁背景，高级感" \
  --output-dir "C:/temp/picset_output"

# B) Picset 生图后直连发小红书（需标题/正文文件）
python scripts/picset_automation.py \
  --prompt-file /abs/path/prompt.txt \
  --publish-to-xhs \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --headless

# C) 小红书封面作参考 + 产品图 + 生成（生成图默认在桌面「Picset生成图_日期-序号」）
python scripts/xhs_images_to_picset.py \
  --keyword "茶叶" \
  --product-images "C:/abs/path/product.png" \
  --generate \
  --generate-timeout 600 \
  --prompt "电商主图，4:5，留白标题区"
```

说明：Picset 为**网页自动化**，无官方 API；页面改版时优先查 `picset_automation.py` / `xhs_images_to_picset.py` 内「登录检测、上传槽、prompt、生成按钮」逻辑。  
说明：**Photoshop（PS）链路未下架**：对已下载的 **Picset 生成图**做「图像→自动色调/对比度/颜色」等价 JSX 批处理的仍是 **`--photoshop-after-generate`**（参见下文「本机约定（Photoshop）」），与是否使用无痕解析站**无关**。该步**默认不自动开启**，须在 `full_stack` / `bulk` / `zero_touch` / `visual`（且带 `--generate`）上显式加上该开关，并装好 PS + **`pip install pywin32`**。  
说明：**`full_stack_xhs_picset_publish.py` / `bulk_publish_accounts.py` / `zero_touch_xhs.py` / 默认参数的 `visual_publish_pipeline.py` 不包含**第三方「无痕清印解析站」步骤；流程是：**拉参考封面 → 直接进 Picset（与产品素材）生成**。若想对**小红书下载的封面**做预处理，推荐使用 **`--watermark-full-auto`**（本地右下角蒙版 inpaint + Pillow，可选 JSX，走 `postprocess/` 本地目录），或**不传**预处理则直接使用原参考图。**若必须坚持人工站外解析**，仅当你**手动**在同一条命令里传入 **`--watermark-post-workflow`** 等标志时生效（脚本**不能**替你完成网站内上传/框选）。  
说明：**（旧版人工站外）**：**`--watermark-post-workflow`**（会写说明并可打开 [无痕清印](https://wuhenqingyin.com.cn/#) …）/`--watermark-full-auto`/`--watermark-no-open-watermark-url`/`--watermark-photoshop` 等仍为 **直接调用** `xhs_images_to_picset`/`visual_publish_pipeline` 的高级选项。  
说明：Picset **生成参考图/主图**下载到本机后，若要再按 Photoshop 菜单「**图像 → 自动色调 / 自动对比度 / 自动颜色**」批量处理（与快捷键 Shift+Ctrl+L 等一致由 JSX 尽力调用），请加 **`--photoshop-after-generate`**（必须同时 **`--generate`**；需 PS + `pip install pywin32`）。处理后图片在生成图目录下的 `postprocess_ps/after_photoshop_autotcc/`，`publish_to_xhs` 与 summary 会优先使用该目录中的文件。  
说明：小红书图床 CDN 下载依赖正确 **Referer**（脚本已处理）；若仍 403，检查网络与 URL 是否过期。  
说明：生成结果采集偶发超时或 WebP 校验异常时，可提高 `--generate-timeout`；脚本已尽量兼容 Picset 产出的 WebP。  
说明：**默认生成 1 张**（`--max-download` 与 `--picset-batch-size` 默认均为 **1**）；脚本会在点击「生成」前**尽力把 Picset「生成数量」切到 N 张（含 1 张）**。若曾手动选过「4 张」，下次跑默认 1 张时也会尝试改回（见 `picset_automation._picset_try_set_batch_count`）。需要多张时在命令行显式传例如 `--max-download 4 --picset-batch-size 4`。  
说明：生成图会按文件内容 **SHA-256 去重**；若你设置 `--max-download` 为 K 而最终不足 K 张**不同图片**，相关逻辑会按脚本报错或重试（不会故意带重复图凑数）。  
说明：默认结束**不断开** DevTools（保留浏览器页）；需要显式断开时加 **`--disconnect-cdp`**（`cdp_publish` / `publish_pipeline` / Picset 相关脚本见各自 `--help`）。

**本机约定（Photoshop）：** 「画好的图 → 放进 Photoshop → **图像** 栏三步」这一套，流水线里已对齐为：**先把成品图拷贝到脚本指定的输入目录**，再 **COM/JSX 自动**对每张图执行与界面一致的一套命令——**图像 → 自动色调 → 自动对比度 → 自动颜色**（ExtendScript：`autoTone` / `autoContrast` / `autoColor`），并把结果写入输出目录。  
**固定执行标准（避免反复改）：** 对每张图按以下等价链路执行且顺序不可变：**进入 Photoshop → 打开(导入)生成图 → 图像 → 自动色调 → 自动对比度 → 自动颜色 → 保存到输出目录**。你无需再手动逐张点击，脚本会按该顺序批量处理。  
**目录约定：** Picset **`--photoshop-after-generate`**：`生成图目录/postprocess_ps/_staging_for_ps/`（拷贝入）→ 处理后的图在 **`.../after_photoshop_autotcc/`**。去水印全链路 **`--watermark-photoshop`**：暂存在 `postprocess/_staging_pillow/` → 成品 **`postprocess/02_ps导出/`**。若要**额外再复印一份**到自己常用的文件夹路径，成功后设环境变量 **`REDBOOK_PHOTOSHOP_MIRROR_FINAL_TO`**（可为绝对路径，支持 `%USERNAME%` 等展开）。手动打开 Photoshop 或安装入口时，可从开始菜单双击 **`C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Adobe Photoshop 2025.lnk`**（或「开始 → Adobe Photoshop 2025」）。**当 JSX 批处理成功结束**时，`xhs_image_autofix` 仍会**默认用系统打开上述快捷方式**（不想自动弹出设 `REDBOOK_PHOTOSHOP_NO_OPEN_AFTER_TASK=1`；自定义快捷方式设 `REDBOOK_PHOTOSHOP_STARTMENU_LNK`）。若 COM 异常，可先确认 Photoshop 已在系统中安装完毕，并执行 **`pip install pywin32`**。  
**为何好像「没开 PS」：** 脚本默认 **`Photoshop.Application.Visible = false`**，批处理在后台跑，界面可能一闪而过或看不见。若要看到 PS 前台逐张处理，运行前设 **`$env:REDBOOK_PHOTOSHOP_VISIBLE = "1"`**。终端会先打印 **`Photoshop COM 批处理开始`（含输入/输出目录与「图像菜单」说明）**，成功后可继续看到 **`Photoshop 批处理完成，已打开快捷方式：...`** 及按需的 **`已将 N 个成品复制到镜像目录`**。

**（可选用）无痕清印人工流程：** 仅当你在同一命令显式传入 **`--watermark-post-workflow`**（及可选 **`--wait-enter-after-watermark`**、**`--require-watermarked-references`**）时才走：`postprocess/watermark_and_ps_workflow.txt`、默认 **`--watermark-tool-url`** 为 **`https://wuhenqingyin.com.cn/#`**、落盘 **`01_去水印后`** 再上 Picset。这不是一键编排的默认内容。  

**全流程若要在 Picset 生成图之后、豆包与发布之前**对「生成图」做 Photoshop「**图像 → 自动色调 / 对比度 / 颜色**」，在 **`full_stack`** / **`bulk`** / **`zero_touch`** / **`visual`**（需 `--generate`）上加 **`--photoshop-after-generate`**（JSX 等价、需 **`pip install pywin32`**）；快捷方式默认值见前文 **`REDBOOK_PHOTOSHOP_STARTMENU_LNK`**。

补充：更完整的安装与背景见 `README.md`；**代理执行以本文件命令为准**。

### 11) 全链路：热点/赛道词 → 参考图 → Picset（产品+参考）→ 生成 → 发图文

**目标流程（与 `visual_publish_pipeline.py` 对齐）**

1. **找与产品同赛道的「热点/推荐词」**（无视觉模型，用搜索词 + 平台推荐词近似「风格/话题」）  
2. **下载笔记封面**到本机（作为参考图）  
3. **Picset**：参考图进「参考设计图」、产品图进「产品素材图」、填提示词、点生成；生成图默认在**桌面** `Picset生成图_日期-序号\`（由 `xhs_images_to_picset` 控制）  
4. **（可选）发图文**：`--publish-to-xhs` + 标题/正文  

**推荐入口（一步编排）**

```bash
python scripts/visual_publish_pipeline.py \
  --product-images "/abs/product.png" \
  --seed-keyword "茶叶" \
  --keyword-strategy recommended_first \
  --max-reference-covers 1 \
  --sort-by 综合
```

- `seed`：始终用种子词搜索。`recommended_first`：若 `search-feeds` 有**下拉推荐词**则取第一条作实际搜索词（更贴热点，需你选准种子词以匹配产品）。  
- **只选 1 张参考封面**：`--max-reference-covers 1 --limit-notes 1`。

**仍可直接用底层脚本**（与上表第 3 步一致）：

```bash
python scripts/xhs_images_to_picset.py --keyword "茶叶" --product-images "/abs/product.png" \
  --generate --generate-timeout 600 --prompt "…"
```

- 生成图默认目录见 **§10**（桌面 `Picset生成图_*`）；自定义目录用 **`--generated-output-dir`**。  
- **`--publish-to-xhs`** 须同时带 **`--generate`**，并提供 **`--title`/`--content`**（或对应 `-file`）。

**第六步：发图文**（在 `visual_publish_pipeline` 上叠加）  

```bash
python scripts/visual_publish_pipeline.py \
  --product-images "/abs/product.png" \
  --seed-keyword "茶叶" \
  --publish-to-xhs \
  --title "标题" \
  --content "正文" \
  --preview
```

与 `publish_pipeline.py` 相同规约：标题/正文需合规；`--preview` 为只填不点发布。

### 12) 运营与多账号：评论、数据、检测、热点

以下能力**不替代**平台规则与人工审核；自动回复/抓评需**频率与内容合规**。

| 需求 | 能力 / 命令 |
|------|-------------|
| 抓评论、互动 | 见 **§5 搜索/详情**、**§9 评论回复**；`get-feed-detail` 带评论；`post-comment-to-feed` / `respond-comment` |
| 自动回复 | 用 `respond-comment` 或 `post-comment-to-feed` 由你方定时任务/机器人循环调用；**无**官方「自动回评」无限制模式 |
| 多账号 | `account_manager` 与 `add-account`；`--account` 配 `cdp_publish` / `publish_pipeline`；**一机多号**需不同 profile（`accounts.json` 里不同 `profile_dir`）+ **互不相同的 `port`**；需**同时并排打开两只 Chrome** 做登录时请用 **`scripts/start_multi_chrome_accounts.py`** |
| 检测账号是否仍登录 | `python scripts/ops_accounts_check.py`（对 `accounts.json` 中各账号跑 `check-login`） |
| 热点/推荐词 | `search-feeds` 结果中的 `recommended_keywords`；`visual_publish_pipeline` 的 `recommended_first` 已用其首条 |
| 每日图文数据 / 推送 | `python scripts/daily_creator_digest.py` 导出 `content-data` 为**桌面 CSV**；可用系统**任务计划程序**每日跑，再发邮件/企微需自接 |
| 自动推送数据 | 本仓库只提供**拉数+落盘**；推送到 IM/邮件需外接 n8n、系统发信等 |
| 配图 + 文案 API → 标题/正文/话题 | **§13** `douban_promo_copy.py`（自建 HTTP **或** **豆包/方舟 `--provider ark`**） |
| 配图 + 话题行 + 发图文（闭环） | **§14** `publish_pipeline.py`；一步生成并发布：**`promo_publish_one_shot.py`** |
| 多账号批量轮发 | `bulk_publish_accounts.py`：先生成一次素材/文案，再按账号清单串行发布（限速+重试+报告） |

**通知与@**（抓评类）见 **8) 获取评论和@通知** 的 `get-notification-mentions`。

### 13) 文案生成：自建 HTTP **或** 豆包（火山方舟 Ark）

脚本文件名仍为 `douban_promo_copy.py`（历史命名）。文案来源二选一：

| 模式 | 说明 |
|------|------|
| **`--provider http`**（默认） | 自建服务 POST JSON（见下文协议），需 **`DOUBAN_PROMO_API_URL`** |
| **`--provider ark`** | **豆包**：调用方舟 **Chat Completions**（与商品说明 + 配图多模态），需 **`ARK_API_KEY`** + **`ARK_MODEL`**（控制台「推理接入点」ID） |

**永远不要**把 API Key 写进仓库、Skill 或聊天记录；只放在本机环境变量或系统密钥库。密钥一旦泄露请在火山引擎控制台**轮换**。

#### A) 火山方舟 · 豆包（推荐：与商品图对齐写种草文案）

| 变量 | 含义 |
|------|------|
| `ARK_API_KEY` | 方舟 API Key（`Bearer`） |
| `ARK_MODEL` | **推理接入点 ID**（在火山「方舟」控制台创建端点后复制，**不是**随便猜的模型名） |
| `ARK_BASE_URL` | 可选，默认 `https://ark.cn-beijing.volces.com/api/v3`（与控制台区域/端点一致） |
| `ARK_BODY_MIN_CHARS` / `ARK_BODY_MAX_CHARS` | 可选；正文 **body** 字数（默认约 **95～100 字**，上限 **100** 含标点与换行；超出会截断） |
| `ARK_TITLE_MAX_CHARS` / `--ark-title-max` | 可选；**title** 最长字数（默认 **18**，按 Unicode 字符计）；与提示词、写入前截断一致 |
| `DOUBAN_PROMO_TIMEOUT` | 与 http 模式共用，秒，默认 `120` |

```powershell
# 仅本机当前窗口有效；勿把真实 Key 写进脚本文件
$env:ARK_API_KEY = "你的_KEY_从控制台复制"
$env:ARK_MODEL = "ep-xxxx"   # 按你方舟里实际接入点 ID 填写
$env:DOUBAN_PROMO_PROVIDER = "ark"   # 可选，默认若你常ark可设

Set-Location "C:\path\to\redbookskills"
python scripts/douban_promo_copy.py --provider ark `
  --brief-file "C:\path\商品与卖点.txt" `
  --images "C:\path\主图1.jpg" "C:\path\主图2.jpg" `
  --seed-keyword "茶叶" `
  --out-dir "C:\path\xhs_promo_out"
```

无图时也可只传 `--brief` / `--brief-file`（纯文本生成）。`--dump-raw-response` 可保存 Ark 完整 JSON 便于排错。

#### B) 自建 HTTP 推广服务

**环境变量（推荐，避免密钥出现在历史记录里）**

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

写出文件：

- `title.txt`：一行标题  
- `content.txt`：**正文 + 空行 + 最后一行话题**  
  最后一行必须是 `#标签1 #标签2`（空格分隔，与 **§3 `publish_pipeline`** 一致），发布时脚本会**逐条选择话题**。

```powershell
# 自建 HTTP 时先设置（示例）
$env:DOUBAN_PROMO_API_URL = "https://your-api.example.com/v1/xhs-promo"
$env:DOUBAN_PROMO_API_KEY = "your-secret"

# 配图 + brief 文件 → 指定目录生成 title.txt / content.txt
Set-Location "C:\path\to\redbookskills"
python scripts/douban_promo_copy.py --provider http `
  --brief-file "C:\path\campaign.txt" `
  --images "C:\path\cover1.jpg" "C:\path\cover2.jpg" `
  --seed-keyword "茶叶" `
  --out-dir "C:\path\xhs_promo_out"

# 不写真实 API 时：试生成文件结构
python scripts/douban_promo_copy.py --dry-run --brief "占位" --out-dir "C:\temp\promo_try"
```

说明：stdout 含 `[douban_promo] OUT_DIR=...`，供 **`promo_publish_one_shot.py`** 解析；也可你自己固定 `--out-dir`。**自建 HTTP（`http`）**标题过长时对「显示宽度」**stderr 警告**（约 **38**，中日文常计 2）；**豆包（`ark`）** 默认 **≤18 字**，模型仍超长则由脚本截断并警告。

### 14) 配图 + 话题打满 + 小红书发布（闭环）

**话题怎么写**：在 `content.txt` 末尾增加**最后一个非空行**，形如：

```text
正文段落……

#喝茶日常 #办公室茶饮 #好茶推荐
```

`publish_pipeline.py` 会把最后一行解析为话题列表，并在正文中**自动去掉**该行后去填编辑器，再按话题逐个选择（见脚本内 `_extract_topic_tags_from_last_line`）。

**只做发布（文案已手写或已由 §13 生成）**

```powershell
Set-Location "C:\path\to\redbookskills"
python scripts/publish_pipeline.py --headless `
  --title-file "C:\path\xhs_promo_out\title.txt" `
  --content-file "C:\path\xhs_promo_out\content.txt" `
  --images "C:\path\cover1.jpg" "C:\path\cover2.jpg"
```

预览不发：加 **`--preview`**。远程 CDP / 复用标签页等同上文 **§3**。

**一步：豆包 / 自建 API 生成文案 + 同一批图发小红书**

```powershell
# 豆包（Ark）
$env:ARK_API_KEY = "从控制台复制"
$env:ARK_MODEL = "ep-你的接入点ID"
Set-Location "C:\path\to\redbookskills"
python scripts/promo_publish_one_shot.py --provider ark `
  --brief-file "C:\path\商品与卖点.txt" `
  --images "C:\path\a.jpg" "C:\path\b.jpg" `
  --seed-keyword "茶叶" `
  --headless

# 自建 HTTP 时改用 --provider http，并配置 DOUBAN_PROMO_API_URL
```

可选 **`--promo-out-dir`** 固定生成目录。调试 API 可加 **`--dump-raw-response`**（在输出目录写 `api_response.json`）。**`--dry-run-promo`** 不请求真实 API、且**强制 `publish_pipeline` 为 `--preview`**（只填不点发布，避免误发占位标题/正文）。

**与 Picset / §11 串起来（建议顺序）**

1. `visual_publish_pipeline.py` / `xhs_images_to_picset.py` → 桌面等目录得到**成品图**  
2. **`douban_promo_copy.py`** → `title.txt` / `content.txt`（话题在最后一行）  
3. **`publish_pipeline.py`** → 上传同路径 `--images` 发布  

或步骤 2+3 合并为 **`promo_publish_one_shot.py`**。

**一条龙（零追问模式：只给产品图即可）**

```powershell
python scripts/full_stack_xhs_picset_publish.py `
  --product-images "D:\product.png" `
  --reference-count 4 `
  --max-download 4 `
  --generate-timeout 1200
```

可加 **`--photoshop-after-generate`**：Picset 下载的生成图先经 Photoshop JSX「自动色调 / 对比度 / 颜色」再写入 summary，再接豆包与发布（须 PS + `pip install pywin32`）。

说明：不传 `--seed-keyword` 时会按产品图文件名自动推断关键词（兜底为“商品”）；不传 `--brief`/`--brief-file` 时自动生成 brief。  
本机需先设 **`ARK_API_KEY`** + **`ARK_MODEL`** 才会继续发布；未设置时脚本会直接报错退出（避免“看似跑完但没点发布”）。  
若你明确需要旧行为（无 ARK 也继续流程、只填不点），可显式加 `--allow-placeholder-preview`。  

## 失败处理

- 登录失败：提示用户重新扫码登录并重试；若用户需要远程展示二维码，可改用 `get-login-qrcode`。
- 图片/视频下载失败：提示更换 URL 或改用本地文件；**小红书 CDN** 若出现 403，勿用手动裸请求旧链接；应使用本仓库 **`image_downloader`**（已带 `www.xiaohongshu.com` Referer）。
- 本地路径不可用：优先改用绝对路径；若为 WSL/远程 CDP 的 Windows/UNC 路径，可先尝试 `--skip-file-check`，必要时再加 `--preserve-upload-paths`。
- 评论/回复目标未定位成功：提示补充 `comment_id`，或改用 `comment_author` / `comment_snippet` 再试。
- 页面选择器失效：提示检查 `scripts/cdp_publish.py` 中选择器并更新。
- **Picset**：工作台/上传失败 → 先在浏览器确认已登录并进入「风格复刻」；生成阶段「无 URL」或校验失败 → 增大 **`--generate-timeout`**，并在 Picset 页查看是否排队或需人工确认。
- **CDP / 浏览器**：自动化异常可先重启 `chrome_launcher` 拉起实例，再单项重试。
- **`unrecognized arguments`**：多为**全局参数写在子命令之后**；按上文「参数顺序提醒」改为例如 `python scripts/cdp_publish.py --reuse-existing-tab search-feeds ...`。
- **自建 HTTP 文案服务**：4xx/5xx、超时、非 JSON → 查 `DOUBAN_PROMO_API_URL` 与返回体；`--dump-raw-response` 对照 `title`、正文、**`tags`**。
- **豆包 / 方舟 Ark**：`Invalid API Key` / 无 `choices` → 查 `ARK_API_KEY` 是否仍有效、**`ARK_MODEL` 是否为当前控制台上的接入点 ID**；多模态需选**带视觉**的端点。若只返回非 JSON 长文，可重试或换模型。
- **`promo_publish_one_shot` 找不到 OUT_DIR**：显式传 **`--promo-out-dir`**；或确认 `douban_promo_copy` 成功且 stdout 含 `[douban_promo] OUT_DIR=`。
- **话题未选中**：最后一行必须是 **`#词` 空格分隔**；勿用逗号代替空格；话题过多可分批发文或删减。

## 15) 多账号批量轮发（100 账号场景）

`bulk_publish_accounts.py` 支持：**先准备一次素材与文案**，再按账号列表轮发，带限速、失败重试，并支持**账号级代理/IP 出口隔离**与**分组窗口发布**。

### 一句话全自动（你只准备「一段话 + 产品图路径」）

用编排脚本 **`scripts/zero_touch_xhs.py`**：把你对活动的说明写成**一段中文**（会作为豆包的 **brief**，并尽量自动抽出 **小红书/Picset 关键词**），再指明**产品素材图**，即等价于替你调用 `bulk_publish_accounts.py`（含 §11 Picset→豆包→多账号发布的整条链）。

```powershell
Set-Location "C:\path\to\redbookskills"
python scripts/zero_touch_xhs.py `
  --speech "关键词茶叶，要全账号铺量，语气真实种草，合规不夸大" `
  --product-images "D:\product_tea.png"
```

- **关键词怎么来**：优先识别「关键词 / 主题 / 搜索词 / 赛道」后的词，或正文里的 `#话题`；否则在常见类目词里做子串匹配（如含「茶叶」→ 用 `茶叶`）；再不行兜底为 `好物`。想手工指定则加 **`--seed-keyword 茶叶`**。  
- **产品图**：必传 **`--product-images`**；也可设环境变量 **`REDBOOK_ZERO_TOUCH_PRODUCT_IMAGE`**（多个路径用**英文逗号**分隔）。  
- **只演练不点发布**：脚本会把 `bulk_publish_accounts` 未识别的参数原样转发，例如追加 **`--preview`**、**`--accounts acc_a acc_b`** 等（与 `bulk_publish_accounts.py --help` 一致）。  
- **`--dry-run`**：仅打印推导出的关键词、brief 路径与将要执行的命令。  
- **Picset→Photoshop→小红书**：在 `zero_touch` 或 `bulk` 上乘 **`--photoshop-after-generate`**（经 `bulk`→`full_stack`→`visual` 传到 **`xhs_images_to_picset`**）：生成图会先走 Photoshop JSX「**图像→自动色调/对比度/颜色**」再进豆包与多账号发布；需 **PS + pywin32**；「开始菜单 .lnk」为辅助打开 Photoshop，**真正把图塞进流程的是 `_staging`/after 目录**，不是双击快捷方式拖拽。快捷方式默认路径同上节 **`REDBOOK_PHOTOSHOP_STARTMENU_LNK`**。  
仍须本机：**Chrome/CDP + 小红书与 Picset 已登录**，以及 **`ARK_API_KEY` + `ARK_MODEL`**（与 `full_stack`/豆包链路一致）。

先给账号配置 `proxy/port/group`（`port` 建议每号唯一，避免 CDP 端口冲突）：

```powershell
python scripts/cdp_publish.py add-account acc001 --alias "账号1" --proxy "http://127.0.0.1:9001" --port 9222 --group A
python scripts/cdp_publish.py add-account acc002 --alias "账号2" --proxy "http://127.0.0.1:9002" --port 9322 --group A
python scripts/cdp_publish.py add-account acc003 --alias "账号3" --proxy "http://127.0.0.1:9003" --port 9422 --group B
python scripts/cdp_publish.py list-accounts
```

```powershell
# 先看账号
python scripts/account_manager.py list

# 轮发到全部账号（默认串行，账号间随机等待 6~16 秒）
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --max-download 4 `
  --picset-batch-size 4 `
  --step-a-retries 2
```

按窗口分组轮发（例如每 20 个账号一组，组间隔 1 小时）：

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

只跑指定组（例如只跑 A 组），并在主流程后对失败账号做 1 轮补偿重跑：

```powershell
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --max-download 4 `
  --picset-batch-size 4 `
  --only-groups A `
  --retry-failed-pass 1
```

按“组时间窗计划”执行（示例：A 组每小时整点、B 组每小时 +30 分）：

```powershell
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --max-download 4 `
  --picset-batch-size 4 `
  --group-window-plan-file "config\group_windows.json.example" `
  --retry-failed-pass 1
```

紧急补发（启用时间窗配置，但错过槽位不等待，直接发）：

```powershell
python scripts/bulk_publish_accounts.py `
  --product-images "D:\product.png" `
  --seed-keyword "绿茶" `
  --max-download 4 `
  --picset-batch-size 4 `
  --group-window-plan-file "config\group_windows.json.example" `
  --slot-grace-seconds 120 `
  --no-wait-when-missed-slot `
  --retry-failed-pass 1
```

`group_windows.json` 结构示例（可复制 `config/group_windows.json.example` 修改）：

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

常用参数：

- `--accounts a1 a2 ...`：只发指定账号；不传则用全部账号
- `--only-groups g1 g2 ...`：只发指定分组账号（按账号 `group` 字段过滤）
- `--max-accounts N`：只跑前 N 个，便于灰度
- `--preview`：所有账号只填不发
- `--retries N`：单账号失败重试次数
- `--retry-failed-pass N`：主流程结束后，对失败账号再做 N 轮补偿重跑
- `--sleep-min/--sleep-max`：账号间等待区间（秒）
- `--group-size N`：按 N 个账号切分发布窗口
- `--group-window-seconds S`：窗口间等待 S 秒（配合 `--group-size`）
- `--group-window-plan-file FILE`：按组循环时间窗发布（每个账号发布前按其组等待到时间槽）
- `--slot-grace-seconds N`：时间窗容错秒数（例如 `120` 表示迟到 120 秒内仍按当前槽位处理）
- `--no-wait-when-missed-slot`：配合 `--group-window-plan-file`，若已错过当前组槽位则不等待下个槽位，立即执行
- `--skip-prepare --title-file --content-file --images ...`：跳过准备阶段，直接用现成素材批量发

说明：

- 批量脚本会按账号配置里的 `port` 调 `publish_pipeline.py`；未配置时回退全局 `--port`。
- 浏览器拉起时会自动读取账号 `proxy` 并注入 `--proxy-server=...`，实现账号级网络出口隔离。
- 时间窗规则：`interval_seconds` 为循环周期；每个组的 `offset_seconds` 是周期内偏移秒数（从 Unix 时间轴对齐）。
- 脚本会在 `tmp/` 生成 `bulk_publish_report_*.json`，记录主流程成功/失败，以及补偿重跑结果与错误尾日志。
