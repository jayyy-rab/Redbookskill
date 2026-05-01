# 全链路视觉编排（热点/赛道 → 封面 → Picset → 生成 → 可选发布）

目标流程（与 `visual_publish_pipeline.py` 对齐）：

1. **找与产品同赛道的「热点/推荐词」**（无视觉模型，用搜索词 + 平台推荐词近似「风格/话题」）  
2. **下载笔记封面**到本机（作为参考图）  
3. **Picset**：参考图进「参考设计图」、产品图进「产品素材图」、填提示词、点生成；生成图默认在**桌面** `Picset生成图_日期-序号\`（由 `xhs_images_to_picset` 控制）  
4. **（可选）发图文**：`--publish-to-xhs` + 标题/正文  

## 推荐入口（一步编排）

```bash
python scripts/visual_publish_pipeline.py \
  --product-images "/abs/product.png" \
  --seed-keyword "茶叶" \
  --keyword-strategy recommended_first \
  --max-reference-covers 1 \
  --sort-by 综合
```

- `seed`：始终用种子词搜索。`recommended_first`：若 `search-feeds` 有**下拉推荐词**则取第一条作实际搜索词（更贴热点，需你选准种子词以匹配产品）。  
- **只选 1 张参考封面**：`--max-reference-covers 1 --limit-notes 1`。

## 直接用底层脚本

```bash
python scripts/xhs_images_to_picset.py --keyword "茶叶" --product-images "/abs/product.png" \
  --generate --generate-timeout 600 --prompt "…"
```

- 生成图默认目录见 [07-picset-integration-photoshop.md](07-picset-integration-photoshop.md)（桌面 `Picset生成图_*`）；自定义目录用 **`--generated-output-dir`**。  
- **`--publish-to-xhs`** 须同时带 **`--generate`**，并提供 **`--title`/`--content`**（或对应 `-file`）。

## 叠加发图文（在 visual_publish_pipeline 上）

```bash
python scripts/visual_publish_pipeline.py \
  --product-images "/abs/product.png" \
  --seed-keyword "茶叶" \
  --publish-to-xhs \
  --title "标题" \
  --content "正文" \
  --preview
```

与 `publish_pipeline.py` 相同规约：标题/正文需合规；`--preview` 为只填不点发布。
