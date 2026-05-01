# 运营与矩阵：互动 · 数据 · 检测 · 热点 · Picset/API 衔接

以下能力**不替代**平台规则与人工审核；自动回复/抓评需**频率与内容合规**。

| 需求 | 能力 / 命令 |
|------|-------------|
| 抓评论、互动 | 见 [06-feeds-search-comments-data.md](06-feeds-search-comments-data.md)：搜索/详情、评论回复；`get-feed-detail` 带评论；`post-comment-to-feed` / `respond-comment` |
| 自动回复 | 用 `respond-comment` 或 `post-comment-to-feed` 由你方定时任务/机器人循环调用；**无**官方「自动回评」无限制模式 |
| 多账号 | `account_manager` 与 `add-account`；`--account` 配 `cdp_publish` / `publish_pipeline`；**一机多号**需不同 profile（`accounts.json` 里不同 `profile_dir`）+ **互不相同的 `port`**；需**同时并排打开两只 Chrome** 做登录时请用 **`scripts/start_multi_chrome_accounts.py`** |
| 检测账号是否仍登录 | `python scripts/ops_accounts_check.py`（对 `accounts.json` 中各账号跑 `check-login`） |
| 热点/推荐词 | `search-feeds` 结果中的 `recommended_keywords`；`visual_publish_pipeline` 的 `recommended_first` 已用其首条 |
| 每日图文数据 / 推送 | `python scripts/daily_creator_digest.py` 导出 `content-data` 为**桌面 CSV**；可用系统**任务计划程序**每日跑，再发邮件/企微需自接 |
| 自动推送数据 | 本仓库只提供**拉数+落盘**；推送到 IM/邮件需外接 n8n、系统发信等 |
| 配图 + 文案 API → 标题/正文/话题 | [10-douban-promo-copy-api.md](10-douban-promo-copy-api.md) `douban_promo_copy.py` |
| 配图 + 话题行 + 发图文（闭环） | [11-topics-publish-closed-loop.md](11-topics-publish-closed-loop.md)；一步生成发布：`promo_publish_one_shot.py` |
| 多账号批量轮发 | [13-bulk-zero-touch-publish.md](13-bulk-zero-touch-publish.md) `bulk_publish_accounts.py` |

**通知与 @**（抓评类）见「获取评论和 @ 通知」的 `get-notification-mentions`。
