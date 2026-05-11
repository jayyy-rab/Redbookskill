# 03 — 统一步骤契约

## 通用约束

- 所有步骤输入：`PipelineContext`
- 所有步骤输出：`StepResult`
- 禁止步骤之间通过全局变量通信
- 产物写入 `ctx.artifacts`，路径使用 `ctx.paths`

## Step1 — 校验输入

- **目标**：确认客户输入完整、合法、可执行
- **校验项**：商品图存在且可打开、关键词非空、商品名非空、文案非空、账号列表合法
- **输出**：`valid_images` → `ctx.artifacts.product_images`
- **失败**：校验不通过 → P1，stop_account

## Step2 — 小红书搜索关键词

- **目标**：通过 CDP 搜索小红书关键词
- **成功标准**：搜索结果页打开、URL 记录、至少发现帖子
- **产物**：feed_count → `result.artifacts`
- **失败**：搜索失败 → P2 retry；超时 → P2 retry

## Step3 — 筛选参考帖子

- **目标**：筛选品类/风格相近的帖子，下载参考图
- **成功标准**：`ctx.artifacts.reference_images` 非空、图片可打开
- **证据**：保存参考图 URL、选中理由
- **失败**：下载失败 → P2 retry

## Step4 — 生成图片

- **目标**：Picset AI 生成商品展示图
- **成功标准**：`ctx.artifacts.generated_images` 非空、图片可打开
- **约束**：固定模型版本 `nova_1_0`
- **失败**：生成失败 → P2 retry；生成无结果 → P1 stop_account

## Step5 — 自动调色

- **目标**：对生成图统一调色，输出最终上传图
- **成功标准**：generated_images 非空 → 文件存在且 > 1KB → 调用 color_adjust.py（存在时）或 fallback 复制 → 写 color_adjust_report.json → 截图 step5_final_image.png
- **约束**：不允许覆盖 `generated_image`，必须输出 `final_images`
- **失败**：`GENERATED_IMAGE_MISSING` → P1；`COLOR_ADJUST_FAILED`（无 fallback）→ P1；`FINAL_IMAGE_INVALID` → P1

## Step6 — 生成文案

- **目标**：生成标题+正文，校验字数/关键词/风格
- **成功标准**：标题非空、正文 80-180 字、无明显违规词
- **约束**：禁止绝对化表达、禁止医疗功效承诺、禁止敏感词
- **失败**：生成失败 → P2 retry；结果为空 → P1 stop_account

## Step7 — 打开创作者发布页

- **目标**：打开小红书创作者发布页，检查登录态
- **成功标准**：URL 是 `creator.xiaohongshu.com/publish`、登录态正常、无风控
- **关键**：URL 不含 `/publish` → `CREATOR_URL_INVALID` P1；未登录 → `LOGIN_REQUIRED` P1；风控/验证码 → `RISK_CONTROL_REQUIRED` P1
- **证据**：写 `login_status.json`，截图 `step7_creator_page.png`
- **失败**：以上任一 → P1 stop_account

## Step8 — 上传图片和填写文案

- **目标**：上传图片、填写标题和正文
- **成功标准**：FILL_STATUS: READY_TO_PUBLISH
- **重试**：upload_retry_count < 2 / title_retry_count < 2 / body_retry_count < 2（全调用重试）
- **证据**：写 `form_evidence.json`，截图 `step8_publish_form_filled.png`
- **失败**：重试耗尽 → `IMAGE_UPLOAD_FAILED` / `TITLE_FILL_FAILED` / `BODY_FILL_FAILED` → P1 stop_account

## Step9 — 添加商品

- **目标**：搜索并挂载客户指定商品
- **成功标准**：商品搜索成功、match_score ≥ 0.75、卡片挂载
- **match_score 映射**：`product_id_exact` → 1.0；`product_name_exact` → 0.9；`product_name_fuzzy_contains` → 0.75
- **证据**：写 `product_match_evidence.json`，截图 `step9_product_card.png`
- **约束**：商品名不匹配预期 → `WRONG_PRODUCT_MOUNTED` P0；match_score < 0.75 + live → P0；match_score < 0.75 + preview → manual_review；manual_review 必须写 diagnostics/suggestion/input_snapshot
- **失败**：搜索失败 → P1 stop_account

## Step10 — 预览/发布

**preview 模式**：不点击发布，FILL_STATUS 确认 → 截图 step10_preview_ready.png

**live 模式 — 10 项串行安全检查**（全部通过才允许发布）：
1. `publish_mode = live`
2. `allow_live_publish = true`
3. Step1-Step8 全部 success
4. Step9 success 或合法 skipped
5. 没有 P0/P1 错误（bugs.json 无 P0/P1 记录）
6. `final_image_path` 存在且文件可达
7. `title_text` 存在
8. `body_text` 存在
9. 商品规则通过
10. preview 已稳定通过过（Step8 成功即视为已验证）

任一不满足 → `P0 LIVE_NOT_ALLOWED`（含具体哪个 gate 失败）

全部满足 → publish_pipeline.py --live → 等待 120s → 截图 published_success.png + 提取 post_url

**失败**：gate 失败 → P0 stop_task；发布失败 → P1 stop_account；超时 → P1 PUBLISH_TIMEOUT
