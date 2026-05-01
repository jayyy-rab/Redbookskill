# 内容检索与互动（推荐流 · 搜索 · 详情 · 评论 · 数据 · 点赞收藏 · 主页 · 通知）

## 参数顺序提醒（`cdp_publish.py`）

请严格按下面顺序写命令，避免 `unrecognized arguments`：

- **全局参数放在子命令前**：`--host --port --headless --account --timing-jitter --reuse-existing-tab`
- **子命令参数放在子命令后**：如 `search-feeds` 的 `--keyword --sort-by --note-type`

示例（正确）：

```bash
python scripts/cdp_publish.py --reuse-existing-tab search-feeds --keyword "春招" --sort-by 最新 --note-type 图文
```

常见可选全局参数：`--host 10.0.0.12 --port 9222 --reuse-existing-tab --account NAME`。

## 5) 搜索笔记 / 获取笔记详情

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
说明：`get-feed-detail --load-all-comments` 会先滚动评论区，并可选点击「更多回复」后再提取详情，同时额外返回 `comment_loading`。  
说明：`check-login` 与主页登录检查默认启用本地缓存（12h，仅缓存「已登录」），到期后自动重新网页校验。

## 6) 给笔记发表评论（一级评论）

```bash
python scripts/cdp_publish.py --reuse-existing-tab post-comment-to-feed \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --content "写得很实用，感谢分享"

python scripts/cdp_publish.py --reuse-existing-tab post-comment-to-feed \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --content-file "/abs/path/comment.txt"
```

## 7) 获取内容数据表（content_data）

需在**创作者中心已登录**状态下使用（与首页 `check-login` 可能不同步；若失败请先打开创作者中心再试）。

```bash
python scripts/cdp_publish.py --reuse-existing-tab content-data
python scripts/cdp_publish.py --reuse-existing-tab content_data

python scripts/cdp_publish.py --reuse-existing-tab content-data --csv-file "/abs/path/content_data.csv"
```

## 8) 获取评论和 @ 通知（notification mentions）

```bash
python scripts/cdp_publish.py --reuse-existing-tab get-notification-mentions
python scripts/cdp_publish.py --reuse-existing-tab get_notification_mentions
```

## 9) 评论回复 / 点赞收藏 / 用户主页信息

```bash
python scripts/cdp_publish.py --reuse-existing-tab respond-comment \
  --feed-id 67abc1234def567890123456 \
  --xsec-token XSEC_TOKEN \
  --comment-id COMMENT_ID \
  --content "感谢反馈～"

python scripts/cdp_publish.py --reuse-existing-tab note-upvote --feed-id 67abc1234def567890123456 --xsec-token XSEC_TOKEN
python scripts/cdp_publish.py --reuse-existing-tab note-unvote --feed-id 67abc1234def567890123456 --xsec-token XSEC_TOKEN

python scripts/cdp_publish.py --reuse-existing-tab note-bookmark --feed-id 67abc1234def567890123456 --xsec-token XSEC_TOKEN
python scripts/cdp_publish.py --reuse-existing-tab note-unbookmark --feed-id 67abc1234def567890123456 --xsec-token XSEC_TOKEN

python scripts/cdp_publish.py --reuse-existing-tab profile-snapshot --user-id USER_ID
python scripts/cdp_publish.py --reuse-existing-tab notes-from-profile --user-id USER_ID --limit 20 --max-scrolls 3
```
