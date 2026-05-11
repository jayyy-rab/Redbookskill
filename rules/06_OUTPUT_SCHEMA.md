# 06 — 输出文件规范

## result.json

```json
{
  "task_id": "task_20260510_001",
  "client_id": "client_001",
  "status": "completed",
  "publish_mode": "preview",
  "total_accounts": 2,
  "success_count": 1,
  "failed_count": 1,
  "manual_review_count": 0,
  "started_at": "2026-05-10T10:00:00",
  "finished_at": "2026-05-10T10:30:00",
  "accounts": [
    {
      "account_id": "acc_001",
      "status": "success",
      "current_step": "completed",
      "post_url": "",
      "preview_screenshot": "accounts/acc_001/screenshots/step10_preview_ready.png",
      "failure_reason": "",
      "steps": []
    }
  ]
}
```

## bugs.json

```json
{
  "task_id": "task_20260510_001",
  "bugs": [
    {
      "account_id": "acc_002",
      "step_id": "step9_attach_product",
      "error_level": "P1",
      "error_type": "PRODUCT_NOT_FOUND",
      "message": "指定商品未搜索到",
      "action": "stop_account",
      "retry_count": 1,
      "url": "https://creator.xiaohongshu.com/...",
      "screenshot": "accounts/acc_002/screenshots/step9_product_not_found.png",
      "input_snapshot": {
        "product_name": "正宗湖南益阳张家塞旭蓝古法手工非遗酿造甜米酒"
      }
    }
  ]
}
```

## StepResult（单个步骤）

```json
{
  "step_id": "step8_fill_publish_form",
  "step_name": "上传图片和填写文案",
  "status": "success",
  "started_at": "2026-05-10T10:00:00",
  "finished_at": "2026-05-10T10:01:30",
  "retry_count": 0,
  "evidence": {
    "screenshot": "screenshots/step8_success.png",
    "url": "https://creator.xiaohongshu.com/...",
    "title_text": "古法甜米酒...",
    "body_text": "..."
  },
  "artifacts": {},
  "created_files": [],
  "error": null,
  "success_check": { "passed": true, "items": [] }
}
```

## 目录结构

```
runs/{task_id}/
  input/
    input.json
    product_001.jpg
  accounts/{account_id}/
    logs/
    screenshots/
    artifacts/
    evidence/
  result.json
  bugs.json
```
