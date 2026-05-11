# Bug 修复标准操作规程

## 核心原则

1. 先诊断，不猜测修复。
2. 诊断必须有证据（日志、截图、DOM 状态、返回值）。
3. 没有证据，不允许改代码。
4. 每次只修一个问题。
5. 修复必须最小改动。
6. 不允许重构无关代码。
7. 不允许顺手优化。
8. 修复后先跑单点验证，再跑局部链路。
9. 单点验证不通过，不允许继续扩大修复范围。
10. 所有修复需记录：改了哪个文件、哪个函数、为什么改、怎么验证、是否影响其他步骤。

## Chrome 连接类 Bug 的诊断 SOP

当遇到 Chrome 端口不可达或 CDP 连接失败时：

1. **第一步：检查 chrome_launcher.ensure_chrome() 是否存在**
   - 文件：`scripts/chrome_launcher.py` 的 `ensure_chrome()`（L391）
   - 如果不存在，检查是否 git checkout 丢失

2. **第二步：确认当前诊断/验证脚本是否调用了 ensure_chrome()**
   - 如果脚本只用 `requests.get(...)` 做端口探测，这是不完整的
   - 必须先调用 `chrome_launcher.ensure_chrome(port, headless, account)` 尝试自动启动

3. **第三步：确认端口配置一致**
   - runner 账号 → `config/runner.json` 的 `runner_port` → 通常是 9322
   - `chrome_launcher.CDP_PORT` 默认 9222
   - `cdp_publish.CDP_PORT` 默认 9222
   - `publish_pipeline --port` 默认 9222，但通过 `get_default_account_and_port` 解析为 runner.json 的值
   - 验证脚本必须使用与 runner.json 一致的端口

4. **第四步：确认 profile 路径一致**
   - `config/runner.json` 的 `profile_dir`
   - `chrome_launcher.get_user_data_dir(account)` 从 account_manager 获取
   - 如果 verify 脚本硬编码路径，先确认与 runner.json 一致

5. **第五步：如果 ensure_chrome 启动成功但 CDP 仍连不上**
   - 检查端口是否被占用（其他进程）
   - 检查 Chrome 是否 crash
   - 检查 profile 是否被锁
   - 输出 Chrome 启动日志（stdout/stderr）

## Step10 添加商品 Bug 的诊断 SOP

当遇到添加商品流程失败时，按此顺序检查：

1. Chrome 是否自动启动 → 执行 `ensure_chrome(port=9322, headless=False, account="runner")`
2. CDP 是否连接 → 获取 WebSocket URL
3. 是否在发布页 → `window.location.href` 含 `publish`
4. 是否已上传图片 → DOM 中有图片预览元素
5. 【添加商品】按钮是否存在 → 多策略 DOM 查询（button 文本、class 选择器）
6. 弹窗是否打开 → `click_add_product` 返回值
7. 商品是否匹配 → `select_product_with_match` 的 candidates 和 matched
8. 复选框是否点击成功 → selected_count 是否变化
9. 保存是否成功 → modal_closed_after_save 状态
10. 商品卡片是否挂载 → product_attached_on_publish_page

每步检查必须附带截图和日志。不跳过、不猜测、不直接大改。

## Step10 保存按钮失败诊断 SOP

当 `select_product_with_match` 返回 `save_button_not_found` 时：

1. **检查弹窗是否存在**：DOM 中是否有 `[class*="goods-select"]` 或 `[class*="multi-goods"]`
2. **检查弹窗中是否有保存按钮**：`button` 文本含"保存"且可见
3. **检查商品是否已挂载**：发布页区域有无商品名（幂等场景）
4. **如果弹窗存在但保存按钮不存在**：
   - 弹窗可能非商品弹窗（如错误提示）
   - 截图 + 日志 + DOM 文本
5. **如果保存按钮存在但点击无效**：
   - `elementFromPoint` 检查按钮是否被遮挡
   - 三层 fallback（CDP 鼠标 → JS click → Enter）是否全部失败
   - 失败截图保存到 `tmp/save_btn_fail.png`

## CDP 断开诊断 SOP

当 `_send()` 抛出连接异常时：

1. 检查 `_reconnect_cdp()` 是否成功（重新获取 WS URL + 建立连接）
2. 如果重连失败：
   - Chrome 进程是否存活
   - `chrome_launcher.ensure_chrome()` 能否重新启动
   - 端口是否被占用
3. 重连后自动重试一次，不循环

## 修复后的验证流程

1. **单点验证**：只验证修改的函数（`python -c "import/unittest"`）
2. **局部链路**：只验证修改涉及的步骤（dry_run 单步骤）
3. **全链路**：仅当用户明确要求时
4. 验证失败 → 停止输出报告，不允许自动继续修第二轮

## 验证脚本编写规范

- 必须以 `chrome_launcher.ensure_chrome()` 开头
- 不能默认要求用户手动启动 Chrome
- 不能在 ensure_chrome 失败后直接退出，必须给出明确诊断信息
- 每一步必须有截图和日志
- 禁止点击最终发布按钮
- 禁止真实发布
- 禁止用"语法通过"代替"功能通过"
