# 验收清单（最小回归）

## A. 分步与干跑
1. 命令：
```bash
python scripts/full_stack_orchestrated.py -h
```
期望：出现 `--from-step`、`--to-step`、`--dry-run`、`--generated-images`。

2. 命令：
```bash
python scripts/full_stack_orchestrated.py \
  --from-step b \
  --to-step c \
  --dry-run \
  --seed-keyword 茶叶 \
  --generated-images C:\path\to\one.jpg
```
期望：退出码 0，生成 `tmp/workflow_report_*.json`，Step C 显示 dry-run。

## B. 多账号轮番与失败续跑
1. 命令：
```bash
python scripts/bulk_publish_accounts.py \
  --skip-prepare \
  --preview \
  --accounts acc_a acc_b \
  --round-robin \
  --continue-on-failure \
  --title-file tmp/xhs_promo_out/title.txt \
  --content-file tmp/xhs_promo_out/content.txt \
  --images C:\path\to\one.jpg \
  --retries 0
```
期望：
1. 日志出现 window 轮换和 `acc_a -> acc_b` 顺序。
2. 任一账号失败时，后续账号继续执行。
3. 生成 `tmp/bulk_publish_report_*.json`。

## C. 一键最小冒烟
1. 命令：
```bash
python scripts/smoke_runner.py --accounts acc_a acc_b --image C:\path\to\one.jpg
```
期望：
1. 输出 `tmp/smoke_report_latest.json`。
2. 报告里包含 3 个 case：`login_check`、`orchestrated_dry_run`、`bulk_round_robin_preview`。

