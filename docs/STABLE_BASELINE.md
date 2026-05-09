# 稳定基线文档

## 当前稳定版本名称

`stable_pre_customer_dryrun`

基于 commit `ffacdd4`（chore(release): client skill freeze），后续有未提交改动。

## 当前已知通过项

| 检查项 | 状态 | 备注 |
|--------|------|------|
| 语法检查 | 通过 | `python -m py_compile` 全部通过 |
| 导入检查 | 通过 | `cdp_publish` / `publish_pipeline` / `chrome_launcher` 可 import |
| 函数存在性检查 | 通过 | 核心函数均存在且可调用 |
| 全流程 dry-run 契约验证 | 通过 | Step1-Step10 接口结构完整 |
| Step10 状态映射单元测试 | 通过 | product_ok / mounted / manual_review 映射 |
| CDP 重连契约检查 | 通过 | `_reconnect_cdp` 重连后自动导航到发布页 |
| 页面模式契约检查 | 通过 | 图文/视频/已完成三种状态可识别 |
| File input 单点定位 | 通过 | `_query_node_id({"depth": 1})` 返回 nodeId |
| Tab 点击后 file input 定位 | 通过 | JS click + 2s 后 `_query_node_id` 返回 nodeId |
| 商品选择幂等规则 | 通过 | Phase3 已挂载跳过、Phase4 三层 fallback |

## 当前未完全通过项

| 项 | 状态 | 阻塞原因 |
|----|------|---------|
| 客户级 dry-run | 未通过 | 上传图片后预览计数器返回 0 导致超时 |
| 真实发布 | 未通过 | 上传图片步骤因 file input 查询失败退出 |
| Step9/Step10 状态延续 | 有风险 | 两者独立 subprocess 执行，不共享运行时状态 |
| 上传图片预览检测 | 不稳定 | `_count_uploaded_images` 选择器可能不匹配当前页面 |
| CDP 重连稳定性 | 有风险 | 重连过程中可能丢失上传状态 |

## 当前禁止动的核心函数

| 函数 | 文件 | 原因 |
|------|------|------|
| `click_add_product` | `cdp_publish.py` | 商品弹窗入口，影响 Step10 |
| `select_product_with_match` | `cdp_publish.py` | 商品搜索/匹配/保存全流程，影响 Step10 |
| `_send` | `cdp_publish.py` | CDP 命令底层通道，含重连逻辑 |
| `_reconnect_cdp` | `cdp_publish.py` | WebSocket 重连 + target 选择 |
| `_click_mouse` | `cdp_publish.py` | CDP 鼠标事件，仅允许三个 Input 事件 |
| `_click_image_text_tab` | `cdp_publish.py` | 发布模式切换，含遗留内容处理逻辑 |
| `_query_node_id` | `cdp_publish.py` | DOM 节点查询，当前使用 depth=1 |
| `_upload_images` | `cdp_publish.py` | 图片上传入口，含事件派发和预览等待 |
| `publish_pipeline.py Step5.0 状态映射` | `publish_pipeline.py` | PRODUCT_SELECT_STATUS 和 exit_code 对齐 |

## 当前已知风险

### 检测脚本和主流程不一致
- 临时验证脚本有时用 CDP 鼠标点击，主流程用 JS `.click()`
- 临时验证脚本有时跳过 tab 点击，主流程必经 tab 点击
- 检测结论不能可靠代表主流程行为

### stdout 状态解析不如 JSON 稳定
- `PRODUCT_SELECT_STATUS: VERIFIED` 等 stdout 标记易被日志前缀干扰
- 后续改为主流程内 JSON 字段解析更可靠

### Step9/Step10 分开跑
- 两者各自起 subprocess 调用 `publish_pipeline.py`
- 不共享 CDP 连接、页面状态、上传缓存
- 效率低且状态延续无保障

### 页面 DOM 变化
- 小红书创作者平台是 SPA，频繁更新
- `_count_uploaded_images` 的 CSS 选择器容易过时
- `SELECTORS` 字典需要定期验证

### CDP target 变化
- `_find_or_create_tab` 依赖 URL 匹配查找 tab
- Chrome 新版本可能改变 tab 列表行为
- `reuse_existing_tab` 策略在各场景下行为不同

### File input 查询深度差异
- `DOM.getDocument` 默认 depth 在 Chrome 124+ 发生变化
- 当前强制 `{"depth": 1}` 以确保兼容
- 不同版本的 Chrome DevTools Protocol 表现不一致

### 临时脚本污染
- `tmp/` 目录有 16 个 Python 临时脚本
- `runs/` 目录有历史运行记录
- 这些不是主流程代码但可能被误当作主流程参考
