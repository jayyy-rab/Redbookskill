# 小红书自动发布系统 — 工程逻辑图 v1.1（冻结版）

冻结日期：2026-05-10
冻结范围：架构 / 流程顺序 / 函数签名 / 数据结构
代码实现冻结：否（可优化实现细节，不可改架构契约）

---

## 一、系统总架构

### 四层分工

| 层 | 模块 | 负责 | 不负责 |
|----|------|------|--------|
| 入口层 | Dify / Web 表单 | 收集输入、参数校验、调用 FastAPI、展示结果 | 不操作 Chrome、不操作小红书、不拆 Step1-Step10、不做 CDP |
| 网关层 | FastAPI | 任务创建/查询/取消、文件状态驱动、结果聚合 | 不写小红书点击逻辑、不写图片生成逻辑、不写商品选择逻辑、不只内存 dict 存状态 |
| 执行层 | Worker | 读取 input.json、创建 ctx、按账号顺序执行 Step1-Step10、StepResult 路由 | 不直接操作 CDP |
| 自动化层 | Python Steps | 真正小红书自动化、Chrome/CDP、画图软件、豆包文案 | 不跨过 Worker 直接暴露给 Dify/FastAPI |

### 证据流

```
Step1-Step10
  ↓
StepResult（每个 Step 都返回）
  ↓
统一写入（每个 Step 都可能写全部）：
  ├─ result.json
  ├─ bugs.json
  ├─ diagnostics.json
  ├─ screenshots/
  ├─ logs/
  ├─ artifacts/
  └─ evidence/
```

**任何 Step 不绑定到特定产物类型。每个 Step 都可能写以上全部。**

---

## 二、Step1-Step10 状态机

每个 Step 执行后进入 StepResult 判定：

| 状态 | 路由 |
|------|------|
| success | → 进入下一步 |
| failed + P0 | → 停止任务，禁止发布 |
| failed + P1 | → 当前账号失败，进入下个账号 |
| failed + P2 | → 重试，超限升级 P1 |
| manual_review | → preview 模式可继续，live 模式禁止 |
| skipped | → 判断是否合法，合法才继续 |

---

## 三、Step5 规则

```
generated_images 非空
  ↓
generated_image_path 文件存在且 > 1KB
  ↓
有真实调色脚本 color_adjust.py?
  ├─ 是 → 调用，成功则 fallback=false
  └─ 否 → fallback 复制 generated_image → final_image
  ↓
写 color_adjust_report.json（含 fallback 标记）
  ↓
复制到 screenshots/step5_final_image.png
  ↓
更新 ctx.artifacts.final_images
```

失败：
- GENERATED_IMAGE_MISSING → P1
- GENERATED_IMAGE_NOT_FOUND → P1  
- GENERATED_IMAGE_TOO_SMALL → P1
- COLOR_ADJUST_FAILED（无 fallback）→ P1
- FINAL_IMAGE_INVALID → P1

---

## 四、Step7 规则

```
Chrome/CDP 连接检查（ensure_chrome）
  ↓
check-login：导航到创作者页
  ↓
URL 是否含 creator.xiaohongshu.com/publish？
  ├─ 否 → P1 CREATOR_URL_INVALID
  ↓
是否已登录？
  ├─ 否 → P1 LOGIN_REQUIRED（等待 120s 后）
  ├─ 风控/验证码 → P1 RISK_CONTROL_REQUIRED
  ↓
写 login_status.json
  ↓
截图 step7_creator_page.png
```

---

## 五、Step8 规则

```
final_image_path 存在且可打开
  ↓
copywriting.json 存在且可读
  ↓
调用 publish_pipeline.py --preview
  ↓
缩略图未出现？
  ├─ upload_retry_count < 2 → 重试
  └─ ≥ 2 → P1 IMAGE_UPLOAD_FAILED
  ↓
标题回读不一致？
  ├─ title_retry_count < 2 → 重填
  └─ ≥ 2 → P1 TITLE_FILL_FAILED
  ↓
正文回读不一致？
  ├─ body_retry_count < 2 → 重填
  └─ ≥ 2 → P1 BODY_FILL_FAILED
  ↓
写 form_evidence.json
  ↓
截图 step8_publish_form_filled.png
```

---

## 六、Step9 规则

```
product_name 为空？
  ├─ allow_no_product=false → P0 PRODUCT_NAME_EMPTY
  └─ allow_no_product=true → 合法 skipped
  ↓
搜索商品 → 计算 match_score
  ↓
match_score ≥ 0.75？
  ├─ preview + < 0.75 → manual_review
  └─ live + < 0.75 → P0 禁止发布
  ↓
商品名不匹配预期？
  └─ P0 WRONG_PRODUCT_MOUNTED
  ↓
写 product_match_evidence.json
  ↓
截图 step9_product_card.png
  ↓
manual_review 必须写 diagnostics、suggestion、input_snapshot
```

---

## 七、Step10 规则

### preview 模式
- ❌ 不点击发布
- 检查页面停在发布前（图片/标题/正文/商品状态可见）
- 保存 step10_preview_ready.png

### live 模式 — 10 项串行安全检查

```
① publish_mode = live?
② allow_live_publish = true?
③ Step1-Step8 全部 success?
④ Step9 success 或合法 skipped?
⑤ 没有 P0/P1 错误?
⑥ final_image_path 存在?
⑦ title_text 存在?
⑧ body_text 存在?
⑨ 商品规则通过?
⑩ preview 已稳定通过过?
```

任一不满足 → P0 LIVE_NOT_ALLOWED，禁止发布，不点击发布。
全部满足 → 允许点击发布 → 等待 120s → published_success.png + post_url

---

## 八、retry_policy 总表

| 类型 | 最大次数/等待时间 | 检查间隔 | 超限处理 |
|---|---:|---:|---|
| Chrome/CDP 连接 | 3 次 | 5 秒 | P1 |
| 页面加载 | 3 次 | 5 秒 | P1 |
| 登录等待（Step2/Step7） | 120 秒 | 10 秒 | P1 LOGIN_REQUIRED |
| 画图软件登录等待（Step4） | 180 秒 | 10 秒 | P1 PICSET_LOGIN_REQUIRED |
| 生图等待（Step4） | 10-20 分钟，重试 1 次 | 15 秒 | P1 IMAGE_GENERATION_TIMEOUT |
| 图片上传（Step8） | 2 次 | 5 秒 | P1 IMAGE_UPLOAD_FAILED |
| 标题填写（Step8） | 2 次 | 即时回读 | P1 TITLE_FILL_FAILED |
| 正文填写（Step8） | 2 次 | 即时回读 | P1 BODY_FILL_FAILED |
| 商品搜索（Step9） | 60 秒 | 5 秒 | manual_review / live 禁止 |
| 商品卡片挂载（Step9） | 1 次 | 3 秒 | P1 PRODUCT_CARD_NOT_MOUNTED |
| 发布等待（Step10 live） | 120 秒 | 5 秒 | P1 PUBLISH_TIMEOUT |
| 文案模型调用（Step6） | 2 次 | 5 秒 | P1 MODEL_CALL_FAILED |
| 文案重写（Step6） | 2 次 | 即时校验 | P1 COPY_QUALITY_FAILED |
| 参考图下载（Step3） | 3 次（换候选） | 5 秒 | P1 REFERENCE_DOWNLOAD_FAILED |
| prompt 回读（Step4） | 2 次 | 即时回读 | P1 PROMPT_READBACK_MISMATCH |

---

## 九、manual_review 定义

```
manual_review ❌不是 success
manual_review ❌不是 failed
manual_review ✅是"需要人工确认"的状态

preview 模式：可以进入 Step10，但必须停在发布前，不点击发布
live 模式：禁止继续，等待人工确认后才能发布

必须做的事：
  ✓ 写 diagnostics.json
  ✓ 计入 manual_review_count
  ✓ 给出 suggestion（人工该做什么）
  ✓ 保存截图（按 {step_id}_{ERROR_TYPE}.png 命名）
  ✓ 保存 input_snapshot

触发场景：
  商品搜不到 → manual_review（preview 可继续，live 禁止）
  match_score < 0.75 → manual_review（preview 可继续，live 禁止）
```

---

## 十、FastAPI 定义

FastAPI 是 **文件状态驱动 / 轻状态任务网关**，不是"无状态 API"。

| 原则 | 说明 |
|------|------|
| 任务状态来源 | `runs/{task_id}/state.json`（文件持久化，非内存） |
| 任务结果来源 | `runs/{task_id}/result.json` |
| 失败来源 | `runs/{task_id}/bugs.json` |
| 诊断来源 | `runs/{task_id}/diagnostics.json` |
| 取消机制 | 写 `cancel.flag` 或更新 `state.json`，Worker 轮询检测 |
| ✅ 必须 | 所有持久状态写入文件系统 |
| ❌ 禁止 | 只用内存 dict 存 cancel/任务状态（进程重启会丢失） |

---

## 十一、错误等级

| 等级 | 定义 | 处理 |
|------|------|------|
| P0 | 错账号/错商品/误发布风险/live 安全不满足 | 立即停止任务，禁止发布，写 bugs+diagnostics |
| P1 | 未登录/风控/页面结构变化/生成失败/商品搜不到 | 当前账号失败，进入下个账号，写 bugs+diagnostics |
| P2 | 页面慢/网络慢/按钮暂不可用/下载失败 | 按 retry_policy 重试，超限升级 P1 |
| P3 | 非关键截图失败/summary 失败/metrics 缺失 | 记录 warning，不阻断 |

---

## 十二、产物校验规则

每个 Step 必须检查真实产物，不能只看 subprocess returncode。

| Step | 必须检查的产物 | 缺失时等级 |
|------|---------------|-----------|
| Step1 | input_validated.json, task_meta.json | P0 |
| Step2 | search_result.json, feed_count > 0 | P1 |
| Step3 | reference_image_path, reference_evidence.json | P1 |
| Step4 | generated_image_path, generation_report.json | P1 |
| Step5 | final_image_path, color_adjust_report.json | P1 |
| Step6 | copywriting.json, title_text, body_text(80-180字) | P1 |
| Step7 | login_status.json, creator_url | P1 |
| Step8 | form_evidence.json, title_readback, body_readback | P1 |
| Step9 | product_match_evidence.json, match_score ≥ 0.75 | P0/P1/manual_review |
| Step10 | preview_ready.png / published_success.png, final_result.json | P1 |

---

## 十三、截图规范

### 关键截图

| Step | 截图内容 | 文件名 |
|------|---------|-------|
| Step2 | 搜索结果页 | step2_search_page.png |
| Step3 | 选中的参考图 | step3_reference.png |
| Step4 | 画图生成图 | step4_generated.png |
| Step5 | 调色后最终图 | step5_final_image.png |
| Step7 | 创作者发布页 | step7_creator_page.png |
| Step8 | 已填写的发布页 | step8_publish_form_filled.png |
| Step9 | 商品卡片 | step9_product_card.png |
| Step10 preview | preview 就绪 | step10_preview_ready.png |
| Step10 live | 发布成功 | published_success.png |

### 失败截图命名

格式：`{step_id}_{ERROR_TYPE}.png`

示例：
- `step2_LOGIN_REQUIRED.png`
- `step4_IMAGE_GENERATION_TIMEOUT.png`
- `step7_RISK_CONTROL_REQUIRED.png`
- `step8_IMAGE_UPLOAD_FAILED.png`
- `step9_PRODUCT_NOT_FOUND.png`
- `step9_MATCH_SCORE_TOO_LOW.png`
- `step10_LIVE_NOT_ALLOWED.png`

---

## 十四、目录结构

```
runs/{task_id}/
├─ input/
│  ├─ product_001.jpg
│  └─ input.json
├─ task_meta.json
├─ result.json
├─ bugs.json
├─ diagnostics.json
├─ cancel.flag
├─ summary.md
└─ accounts/{account_name}/
   ├─ state.json
   ├─ result.json
   ├─ bugs.json
   ├─ diagnostics.json
   ├─ logs/
   ├─ screenshots/
   ├─ artifacts/
   └─ evidence/
```

---

## 十五、冻结规则

1. **架构冻结** — Dify / FastAPI / Worker / Python Steps 四层不更改
2. **流程顺序冻结** — Step1-Step10 顺序不更改
3. **函数签名冻结** — `run_stepN_xxx(ctx: PipelineContext) -> StepResult`
4. **input.json 冻结** — 基础字段不更改
5. **result.json 冻结** — 结构不更改
6. **bugs.json 冻结** — 每笔含 error_level/error_type/message/action/suggestion/screenshot/timestamp
7. **diagnostics.json 冻结** — ActionTrace 数组格式固定
8. **产物校验不删除** — 每个 Step 必须检查真实产物
9. **截图不删除** — 关键截图 + 失败截图
10. **不默认 live** — 默认 preview，live 必须 10 项安全检查全部通过
11. **不为通过测试绕过失败** — 所有失败写 bugs.json + diagnostics.json + 截图
12. **任何架构变化必须先出变更单**

---

## 十六、Step6 文案校验（AND 逻辑）

必须全部通过才 success：
1. title 非空
2. body 非空
3. body 80-180 字
4. 无 forbidden_words
5. 含核心关键词/卖点

任一失败 → rewrite（最多 2 次）→ 仍失败 → P1 COPY_QUALITY_FAILED
