# 回归验证清单

任何修改后必须按此顺序执行回归验证。

---

## 第 1 级：语法检查（必须）

```bash
python -m py_compile scripts/publish_pipeline.py scripts/cdp_publish.py scripts/chrome_launcher.py
```

目标：全部返回 exit code 0。

---

## 第 2 级：导入检查（必须）

```bash
python -c "from scripts.cdp_publish import XiaohongshuPublisher; print('cdp_publish OK')"
python -c "from scripts.publish_pipeline import main; print('publish_pipeline OK')"
python -c "from scripts.chrome_launcher import ensure_chrome; print('chrome_launcher OK')"
```

目标：全部无报错。

---

## 第 3 级：函数存在性和签名检查（必须）

检查以下函数存在且签名正确：

| 函数 | 位置 | 检查点 |
|------|------|--------|
| `XiaohongshuPublisher.__init__` | `cdp_publish.py` | 必须接收 `context_key` 参数 |
| `click_add_product` | `cdp_publish.py` | 必须存在，返回 bool |
| `select_product_with_match` | `cdp_publish.py` | 必须存在，返回 dict |
| `_query_node_id` | `cdp_publish.py` | 必须存在，返回 int |
| `_send` | `cdp_publish.py` | 必须存在 |
| `_reconnect_cdp` | `cdp_publish.py` | 必须存在 |
| `_click_mouse` | `cdp_publish.py` | 必须存在 |
| `ensure_chrome` | `chrome_launcher.py` | 必须存在，返回 bool |
| Step5.0 状态映射 | `publish_pipeline.py` | `emit_product_select_evidence` 必须存在 |

---

## 第 4 级：接口合同检查（必须）

### publish_pipeline.py → XiaohongshuPublisher 参数一致性

`publish_pipeline.py` 传给 `XiaohongshuPublisher.__init__` 的参数：
- `host`
- `port`
- `timing_jitter`
- `account_name`
- `context_key`
- `preserve_upload_paths`

每个参数必须在 `__init__` 签名中有对应形参。

### Step10 返回字段检查

`select_product_with_match` 返回的 dict 必须包含：
- `ok`（bool）
- `mounted`（bool）
- `product_attached_on_publish_page`（bool）
- `manual_review`（bool）
- `rule`（str）
- `matched`（dict 或 None）
- `target`（dict）
- `candidates`（list）
- `checkbox_found`（bool）
- `checkbox_clicked`（bool）
- `selected_count_before`（str）
- `selected_count_after`（str）
- `save_button_found`（bool）
- `save_clicked`（bool）
- `modal_closed_after_save`（bool）

### PRODUCT_SELECT_STATUS 和 exit_code 对齐

| PRODUCT_SELECT_STATUS | exit_code | 含义 |
|----------------------|-----------|------|
| VERIFIED | 0 | 商品已确认 |
| MANUAL_REVIEW | 4 | 需要人工审核 |
| FAILED | 4 | 失败 |
|（空） | 2 | 表单填写失败 |

---

## 第 5 级：主流程 dry-run（必须）

必须使用真实 `publish_pipeline.py`，不允许使用临时脚本替代。

```bash
python scripts/publish_pipeline.py \
  --title "测试标题" \
  --content "测试正文" \
  --images <测试图片路径> \
  --click-add-product \
  --product-name "<测试商品名>" \
  --account runner \
  --preview
```

检查点：
- Step1 Chrome 启动：成功
- Step2 登录检查：成功
- Step3 图片准备：成功
- Step4 表单填写：成功
- Step5.0 商品选择：VERIFIED 或通过
- 没有未捕获异常
- exit code = 0

---

## 第 6 级：客户级 dry-run（仅当前 5 级通过后才允许执行）

使用 `dryrun_step1_10_runner.py` 或 `publish_pipeline.py` 的完整入参执行。

必须添加 `--preview` 保护。
