# Step10 添加商品流程规则

## 流程职责划分

### click_add_product
- 所属文件：`scripts/cdp_publish.py`
- 职责：只负责点击【添加商品】按钮并确认商品弹窗打开
- 入参：`strict=False`（是否抛异常）
- 返回：`bool`（弹窗是否打开）
- 不负责：搜索商品、选择商品、保存商品、发布

### select_product_with_match
- 所属文件：`scripts/cdp_publish.py`
- 职责：在商品弹窗已打开的前提下，搜索、匹配、选择、保存商品
- 入参：`product_name=""`、`product_id=""`、`strict=False`
- 返回：`dict`（包含匹配结果、选中计数、保存状态、弹窗状态、商品挂载状态）
- 前置条件：商品弹窗必须已打开（由 `click_add_product` 保证）
- 不负责：打开弹窗、点击发布

## 三阶段匹配规则

1. `product_id_exact`：商品 ID 精确匹配（最高优先级）
2. `product_name_exact`：商品名精确匹配
3. `product_name_fuzzy_contains`：商品名模糊包含匹配（最低优先级）

匹配结果非精确时，管道输出 `MANUAL_REVIEW` 状态。

## 调用链

```
ensure_chrome()                    # Step 1: 自动启动 Chrome
  → connect CDP                    # Step 2: 连接调试端口
  → click_add_product()            # 打开商品弹窗
    → select_product_with_match()  # 选择并保存商品
      → 最终校验                   # 检查商品卡片是否挂载
```

## 验证脚本规范

- 必须以 `chrome_launcher.ensure_chrome()` 开头
- 不能默认要求用户手动启动 Chrome
- Chrome 端口与 runner 配置一致（9322）
- 禁止点击最终发布按钮
- 每一步必须截图 + 日志

## 失败诊断检查清单

1. Chrome 是否自动启动？→ `ensure_chrome` 返回值
2. CDP 是否连接？→ WebSocket URL 是否获取成功
3. 是否在发布页？→ URL 是否含 `publish`
4. 是否已上传图片？→ 图片预览元素是否存在
5. 添加商品按钮是否存在？→ DOM 查询结果 + 截图
6. 弹窗是否打开？→ `click_add_product` 返回值 + 截图
7. 商品是否搜索到？→ `candidates` 列表长度
8. 商品是否选中？→ `selected_count_after` 是否 > 0
9. 保存是否成功？→ `save_clicked` + `modal_closed_after_save`
10. 商品卡片是否挂载？→ `product_attached_on_publish_page`

## 保存按钮三层 Fallback

1. **CDP 鼠标点击**：`_click_mouse(sx, sy)` + 轮询 6×0.5s 确认弹窗关闭
2. **JS .click() 回退**：`btns[i].click()` + 轮询 6×0.5s 确认弹窗关闭
3. **键盘 Enter 回退**：`Input.dispatchKeyEvent` Enter + 轮询 6×0.5s 确认弹窗关闭

点击前通过 `elementFromPoint(cx, cy)` 验证目标元素，非目标元素时仍尝试点击但不跳过。

## 商品已挂载幂等处理

`select_product_with_match` 在找不到"保存"按钮时，先检查发布页区域是否已有该商品名：
- 已挂载 → 跳过保存，返回成功
- 未挂载 → 返回 `save_button_not_found` 错误

## 证据要求

任何失败判断必须附带：
- 截图（点击前、点击后、失败时）
- 日志（函数名、行号、返回值）
- DOM 状态（弹窗、按钮、商品列表）

## CDP 连接自动恢复

`_send()` 在 `ws.send()` / `ws.recv()` 抛出异常时自动重连一次：
- `_reconnect_cdp()` 关闭旧 WS，重新获取标签页 WS URL，建立新连接
- 重连后重试当前命令（最多一次）
- 重连失败抛出 `CDPError`
