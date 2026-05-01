# 配图 + 话题打满 + 发布（闭环）与一条龙

## 话题怎么写

在 `content.txt` 末尾增加**最后一个非空行**，形如：

```text
正文段落……

#喝茶日常 #办公室茶饮 #好茶推荐
```

`publish_pipeline.py` 会把最后一行解析为话题列表，并在正文中**自动去掉**该行后去填编辑器，再按话题逐个选择（见脚本内 `_extract_topic_tags_from_last_line`）。

## 只做发布（文案已手写或已由文案 API 生成）

```powershell
Set-Location "C:\path\to\redbookskills"
python scripts/publish_pipeline.py --headless `
  --title-file "C:\path\xhs_promo_out\title.txt" `
  --content-file "C:\path\xhs_promo_out\content.txt" `
  --images "C:\path\cover1.jpg" "C:\path\cover2.jpg"
```

预览不发：加 **`--preview`**。远程 CDP / 复用标签页等同 [04-publish-image-and-video.md](04-publish-image-and-video.md)。

## 一步：豆包 / 自建 API 生成文案 + 同一批图发小红书

```powershell
$env:ARK_API_KEY = "从控制台复制"
$env:ARK_MODEL = "ep-你的接入点ID"
Set-Location "C:\path\to\redbookskills"
python scripts/promo_publish_one_shot.py --provider ark `
  --brief-file "C:\path\商品与卖点.txt" `
  --images "C:\path\a.jpg" "C:\path\b.jpg" `
  --seed-keyword "茶叶" `
  --headless
```

自建 HTTP 时改用 `--provider http`，并配置 `DOUBAN_PROMO_API_URL`。

可选 **`--promo-out-dir`** 固定生成目录。调试 API 可加 **`--dump-raw-response`**。**`--dry-run-promo`** 不请求真实 API、且**强制 `publish_pipeline` 为 `--preview`**。

## 与 Picset / 视觉编排串起来（建议顺序）

1. `visual_publish_pipeline.py` / `xhs_images_to_picset.py` → 桌面等目录得到**成品图**  
2. **`douban_promo_copy.py`** → `title.txt` / `content.txt`（话题在最后一行） — 详见 [10-douban-promo-copy-api.md](10-douban-promo-copy-api.md)
3. **`publish_pipeline.py`** → 上传同路径 `--images` 发布  

或步骤 2+3 合并为 **`promo_publish_one_shot.py`**。

## 一条龙（零追问模式：只给产品图即可）

```powershell
python scripts/full_stack_xhs_picset_publish.py `
  --product-images "D:\product.png" `
  --reference-count 4 `
  --max-download 4 `
  --generate-timeout 1200
```

可加 **`--photoshop-after-generate`**（须 PS + `pip install pywin32`）。

说明：不传 `--seed-keyword` 时会按产品图文件名自动推断关键词（兜底为「商品」）；不传 `--brief`/`--brief-file` 时自动生成 brief。  
本机需先设 **`ARK_API_KEY`** + **`ARK_MODEL`** 才会继续发布；未设置时脚本会直接报错退出。  
若你明确需要旧行为（无 ARK 也继续流程、只填不点），可显式加 **`--allow-placeholder-preview`**。
