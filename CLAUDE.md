# 项目规则

修改代码前必须先读取 `rules/ENGINEERING_BASELINE.md`（v1.1 基线），再按 `rules/00-INDEX.md` 索引读取相关规则文件，并严格遵守。

## Chrome 自动启动规则

1. Chrome 自动启动能力完整存在于 `scripts/chrome_launcher.py` 的 `ensure_chrome()` 函数。
2. 任何时候 Chrome 端口不可达，必须先调用 `chrome_launcher.ensure_chrome(port=..., headless=False, account=...)` 尝试自动启动。
3. 禁止用 `requests.get("http://127.0.0.1:9322/json")` 探测失败后直接要求用户手动启动 Chrome。
4. 手动启动只能作为 `ensure_chrome()` 返回 False 后的最后提示。

## 端口配置

- runner 账号固定端口：9322（定义在 `config/runner.json`）
- 默认端口：9222（定义在 `cdp_publish.py` 和 `publish_pipeline.py`）
- 诊断/验证脚本如果针对 runner 账号，必须使用 9322

## 验证脚本编写规则

1. 必须以 `chrome_launcher.ensure_chrome()` 开头，确保 Chrome 已启动。
2. 连接 CDP 后先检查页面状态（URL、发布页、图片上传）。
3. 每个验证步骤必须有截图和日志。
4. 禁止点击最终发布按钮。
5. 禁止在全流程 dry_run 前直接跑全链路。

## Step10 诊断链路（按顺序检查）

1. Chrome 是否自动启动（`ensure_chrome`）
2. CDP 是否连接成功
3. 是否在发布页（URL 含 `publish`）
4. 是否已上传图片
5. 添加商品按钮是否存在
6. 商品弹窗是否打开（`click_add_product`）
7. 商品是否搜索到并选中（`select_product_with_match`）
8. 商品卡片是否挂载到发布页

以上每一步失败都应输出对应截图和 DOM 证据，不能跳过诊断直接修复。

## CDP 连接可靠性规则

1. `_send()` 在 `ws.send()` 或 `ws.recv()` 抛出异常时，调用 `_reconnect_cdp()` 重连并重试一次（最多一次）。
2. `_reconnect_cdp()` 通过 `_find_or_create_tab()` 重新获取目标页 WebSocket URL，关闭旧连接，建立新连接。
3. 重连仅重试一次，不循环重试。重连失败则抛出 `CDPError`。

## CDP 鼠标事件规则

1. `_click_mouse()` 仅使用三个有效 CDP Input 事件：`mouseMoved`、`mousePressed`、`mouseReleased`。
2. 禁止使用 `pointerdown` / `pointerup`（CDP 不支持）。
3. 点击前调用 `elementFromPoint` 验证目标可命中。

## 商品选择幂等规则

1. `select_product_with_match` Phase 3：找不到"保存"按钮时，先检查商品是否已挂载到发布页。
2. 若已挂载（`already_mounted` 为 True），跳过 Phase 4，直接返回成功。
3. 若未挂载，返回 `{"ok": False, "reason": "save_button_not_found"}`。
4. 三阶段匹配优先级：`product_id_exact` > `product_name_exact` > `product_name_fuzzy_contains`。

## 保存按钮三层 Fallback 规则

1. 第一层：CDP 鼠标点击（`_click_mouse`），轮询 6×0.5s 确认弹窗关闭。
2. 第二层：JS `.click()` 回退，轮询 6×0.5s 确认弹窗关闭。
3. 第三层：键盘 Enter 回退，轮询 6×0.5s 确认弹窗关闭。
4. 仅当三层均失败时，保存失败。

## `attach_product_link` 兼容占位规则

1. `attach_product_link` 是 API 兼容占位函数，不会实际挂载商品。
2. 调用时输出警告日志。
3. 商品选择应使用 `click_add_product` + `select_product_with_match`。
