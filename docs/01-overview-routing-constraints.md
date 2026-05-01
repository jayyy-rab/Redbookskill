# 总览：输入判断 · 约束 · 执行目录

## 输入判断

优先按以下顺序判断：

1. 用户明确要求"测试浏览器 / 启动浏览器 / 检查登录 / 获取登录二维码 / 只打开不发布"：进入测试浏览器流程（详见 [03-browser-session-login-test.md](03-browser-session-login-test.md)）。
2. 用户要求「首页推荐 / 搜索笔记 / 找内容 / 查看某篇笔记详情 / 查看内容数据表 / 给帖子评论 / 回复评论 / 点赞收藏互动 / 查看用户主页 / 查看评论和@通知」：进入内容检索与互动流程（详见 [06-feeds-search-comments-data.md](06-feeds-search-comments-data.md)）。
3. 用户已提供 `标题 + 正文 + 视频(本地路径或 URL)`：直接进入视频发布流程（详见 [04-publish-image-and-video.md](04-publish-image-and-video.md)）。
4. 用户已提供 `标题 + 正文 + 图片(本地路径或 URL)`：直接进入图文发布流程（同上）。
5. 用户只提供网页 URL：先提取网页内容与图片/视频，再给出可发布草稿，等待用户确认。
6. 信息不全：先补齐缺失信息，不要直接发布。
7. 用户已整理好**本地配图**，希望用**豆包/方舟**或**自建 HTTP** 生成与图/商品说明相关的**标题、正文、话题**，再用于小红书：见 [10-douban-promo-copy-api.md](10-douban-promo-copy-api.md)；可先 Picset 再 API。
8. 用户已有**配图 + 完整文案**，且要把**话题标签打满**并**发布**小红书：见 [11-topics-publish-closed-loop.md](11-topics-publish-closed-loop.md)；若需「API 生成文案 → 立刻发」一步完成：用 `promo_publish_one_shot.py`。

## 必做约束

- 发布前必须让用户确认最终标题、正文和图片/视频。
- 图文发布时，没有图片不得发布（小红书发图文必须有图片）。
- 视频发布时，没有视频不得发布。图片和视频不可混合使用（二选一）。
- 默认使用无头模式；若检测到未登录，切换有窗口模式登录。
- 小红书标题的常见「显示宽度」提示约 **38**（中文/中文标点按 2，英文数字按 1）；若以 **豆包/方舟 `--provider ark`** 生成文案，脚本默认还要求 **title 至多 18 个字**（ Unicode 字符计，可调 `ARK_TITLE_MAX_CHARS` / `--ark-title-max`），超限会写入前截断并 stderr 提示。
- 用户要求"仅测试浏览器"时，不得触发发布命令。
- 如使用文件路径，优先使用绝对路径；若用户给的是相对路径，先转换为绝对路径再执行命令。
- 若发布页结构异常，优先检查 `scripts/cdp_publish.py` 里的 `SELECTORS`、多图上传等待、正文编辑器与发布按钮点击逻辑；这些是最容易被小红书网页改版影响的区域。
- **执行目录**：下文命令默认在**仓库根目录**（含 `scripts/` 的一级）下运行；若当前目录不在根目录，请 `cd` 到该目录再执行，否则 `python scripts/...` 会找不到文件。

## 简明流程提要

### 测试浏览器流程（不发布）

1. 启动 post-to-xhs 专用 Chrome（默认有窗口模式，便于人工观察）。
2. 如用户要求静默运行，再使用无头模式。
3. 可选：执行登录状态检查并回传结果。
4. 结束后如用户要求，关闭测试浏览器实例。

### 图文发布流程

1. 准备输入（标题、正文、图片 URL 或本地图片）。
2. 如需文件输入，先写入 `title.txt`、`content.txt`。
3. 执行发布命令（默认无头）。
4. 回传执行结果（成功/失败 + 关键信息）。

### 视频发布流程

1. 准备输入（标题、正文、视频文件路径或 URL）。
2. 如需文件输入，先写入 `title.txt`、`content.txt`。
3. 执行视频发布命令（默认无头）。视频上传后需等待处理完成。
4. 回传执行结果（成功/失败 + 关键信息）。

### 内容检索与互动流程（搜索/详情/评论/内容数据）

1. 先检查小红书主页登录状态（`XHS_HOME_URL`，非创作者中心）。
2. 若用户需要首页推荐流，执行 `list-feeds` 获取首页推荐笔记列表。
3. 若用户需要关键词搜索，执行 `search-feeds` 获取笔记列表（默认会先抓取搜索下拉推荐词，结果字段为 `recommended_keywords`）。
4. 若用户需要详情，从搜索结果中取 `id` + `xsecToken` 再执行 `get-feed-detail`；如用户明确要更多评论，可加 `--load-all-comments` 等参数。
5. 若用户需要发表评论，执行 `post-comment-to-feed`（一级评论；必填 `feed_id` / `xsec_token` / `content`）。
6. 若用户需要回复某条评论，执行 `respond-comment`（可用 `comment_id` / `comment_author` / `comment_snippet` 定位目标评论）。
7. 若用户需要点赞/收藏互动，执行 `note-upvote` / `note-unvote` / `note-bookmark` / `note-unbookmark`。
8. 若用户需要用户主页信息，执行 `profile-snapshot` 或 `notes-from-profile`。
9. 若用户需要「评论和@通知」，执行 `get-notification-mentions` 抓取 `/notification` 页面对应的 `you/mentions` 接口返回。
10. 若用户需要「笔记基础信息表」，执行 `content-data` 获取曝光/观看/点赞等指标。
11. 回传结构化结果（数量、核心字段、链接）。
