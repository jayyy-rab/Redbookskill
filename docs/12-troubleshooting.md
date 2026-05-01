# 失败处理与排查

- **登录失败**：提示用户重新扫码登录并重试；若用户需要远程展示二维码，可改用 `get-login-qrcode`（见 [03-browser-session-login-test.md](03-browser-session-login-test.md)）。
- **图片/视频下载失败**：提示更换 URL 或改用本地文件；**小红书 CDN** 若出现 403，勿用手动裸请求旧链接；应使用本仓库 **`image_downloader`**（已带 `www.xiaohongshu.com` Referer）。
- **本地路径不可用**：优先改用绝对路径；若为 WSL/远程 CDP 的 Windows/UNC 路径，可先尝试 `--skip-file-check`，必要时再加 `--preserve-upload-paths`。
- **评论/回复目标未定位成功**：提示补充 `comment_id`，或改用 `comment_author` / `comment_snippet` 再试。
- **页面选择器失效**：提示检查 `scripts/cdp_publish.py` 中选择器并更新。
- **Picset**：工作台/上传失败 → 先在浏览器确认已登录并进入「风格复刻」；生成阶段「无 URL」或校验失败 → 增大 **`--generate-timeout`**。
- **CDP / 浏览器**：自动化异常可先重启 `chrome_launcher` 拉起实例，再单项重试。
- **`unrecognized arguments`**：**全局参数写在子命令之后**；改成例如 `python scripts/cdp_publish.py --reuse-existing-tab search-feeds ...`（见 [06-feeds-search-comments-data.md](06-feeds-search-comments-data.md)）。
- **自建 HTTP 文案服务**：4xx/5xx、超时、非 JSON → 查 `DOUBAN_PROMO_API_URL` 与返回体；`--dump-raw-response` 对照。
- **豆包 / 方舟 Ark**：`Invalid API Key` / 无 `choices` → 查 **`ARK_MODEL` 是否为当前控制台上的接入点 ID**；多模态需选**带视觉**的端点。
- **`promo_publish_one_shot` 找不到 OUT_DIR**：显式传 **`--promo-out-dir`**；或确认 `douban_promo_copy` 成功且 stdout 含 `[douban_promo] OUT_DIR=`。
- **话题未选中**：最后一行必须是 **`#词` 空格分隔**；勿用逗号代替空格；话题过多可分批发文或删减。
