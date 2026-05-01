---
name: RedBookSkills
description: |
  将图文/视频内容自动发布到小红书（XHS），并支持登录检查、内容检索与互动操作；可与 Picset 生图、豆包(方舟)或自建 HTTP 生成推广文案、创作者数据导出、多账号探测配合。
  适用场景：发布图文、发布视频、测试浏览器、登录二维码、首页推荐、搜索笔记、评论互动、内容数据、Picset、热点编排（visual_publish_pipeline）、配图+豆包/自建 API 写文案+话题、一键生成并发布。
metadata:
  trigger: 发布内容到小红书
  source: Angiin/Post-to-xhs
---

# Post-to-xhs（小红书发布助手）

你是「小红书发布助手」。目标是在用户确认后，调用本 Skill 的脚本完成发布或互动操作。

本 Skill 已**按板块拆分为 `docs/` 下的模块化说明**。**先读本节路由表**，再根据任务 **`Read`** 对应的 `docs/*.md`，避免单列超长文件不便维护。**引用规则**：从本 `SKILL.md` 到 `docs/` 仅一层链接（渐进披露）。

---

## 快速路由：我该打开哪一章？

| 场景 | 打开的文档 |
|------|-------------|
| 输入优先级、全流程提要、硬性约束、`cd` 到仓库根 | [docs/01-overview-routing-constraints.md](docs/01-overview-routing-constraints.md) |
| Windows / PowerShell、UTF-8 写文件 | [docs/02-environment-windows-powershell.md](docs/02-environment-windows-powershell.md) |
| 启动浏览器、登录检查、二维码、不发布调试 | [docs/03-browser-session-login-test.md](docs/03-browser-session-login-test.md) |
| 图文 `publish_pipeline`、视频、URL/本地图、预览 | [docs/04-publish-image-and-video.md](docs/04-publish-image-and-video.md) |
| 多账号切换、多端 CDP、`start_multi_chrome_accounts` | [docs/05-multi-account-and-cdp-ports.md](docs/05-multi-account-and-cdp-ports.md) |
| 首页流、搜索、详情、评论、数据表、点赞收藏、主页、通知 | [docs/06-feeds-search-comments-data.md](docs/06-feeds-search-comments-data.md) |
| Picset、`--photoshop-after-generate`、水印与 Photoshop 约定 | [docs/07-picset-integration-photoshop.md](docs/07-picset-integration-photoshop.md) |
| `visual_publish_pipeline`、热点赛道、种子词策略 | [docs/08-visual-publish-pipeline-hotspot.md](docs/08-visual-publish-pipeline-hotspot.md) |
| 矩阵日常：检测登录、.digest、与各 doc 指针 | [docs/09-operations-matrix-notes.md](docs/09-operations-matrix-notes.md) |
| `douban_promo_copy.py`（HTTP / Ark）、环境变量、`OUT_DIR` | [docs/10-douban-promo-copy-api.md](docs/10-douban-promo-copy-api.md) |
| 话题末行、`promo_publish_one_shot`、`full_stack_*` | [docs/11-topics-publish-closed-loop.md](docs/11-topics-publish-closed-loop.md) |
| 报错与排查：`unrecognized arguments`、CDN、选题未选中等 | [docs/12-troubleshooting.md](docs/12-troubleshooting.md) |
| `bulk_publish_accounts`、`zero_touch_xhs`、组与时间窗 JSON | [docs/13-bulk-zero-touch-publish.md](docs/13-bulk-zero-touch-publish.md) |

---

## 执行任务时的阅读习惯

1. **判定入口**：对照 [docs/01-overview-routing-constraints.md](docs/01-overview-routing-constraints.md) 的输入判断与安全约束。
2. **载入细则**：对上表 **`Read`** 该任务对应的某一个 `docs/` 文件（通常只需一篇；跨流程再读链路中的下一篇）。
3. **失败时**：先看 [docs/12-troubleshooting.md](docs/12-troubleshooting.md)，再按指引回到具体命令文档核对参数顺序（尤以 `cdp_publish.py` 全局参数须在子命令**前**）。
4. **仓库根目录**：所有示例命令须在含 `scripts/` 的仓库根执行。

完整命令与长篇说明已从本文件迁出；请以 `docs/` 为准。**更多背景**仍可见仓库根部 `README.md`。
