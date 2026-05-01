# Picset 生图联动与 Photoshop（MVP）

## 两条入口（不要混用默认域名习惯）

| 脚本 | 典型用途 | Picset 默认入口 |
|------|----------|-----------------|
| `picset_automation.py` | 本地参考/产品素材直传 Picset | 默认偏 **picsetai.com** 系（见脚本内 `--picset-url`） |
| `xhs_images_to_picset.py` | 小红书搜封面 → 下载 → Picset 参考槽 + 可选产品图 + 生成 | 默认 **https://picsetai.cn/** |

```bash
# A) 仅 Picset：本地素材 + prompt + 下载结果
python scripts/picset_automation.py \
  --prompt "奶油色电商风，主体居中，简洁背景，高级感" \
  --output-dir "C:/temp/picset_output"

# B) Picset 生图后直连发小红书（需标题/正文文件）
python scripts/picset_automation.py \
  --prompt-file /abs/path/prompt.txt \
  --publish-to-xhs \
  --title-file /abs/path/title.txt \
  --content-file /abs/path/content.txt \
  --headless

# C) 小红书封面作参考 + 产品图 + 生成（生成图默认在桌面「Picset生成图_日期-序号」）
python scripts/xhs_images_to_picset.py \
  --keyword "茶叶" \
  --product-images "C:/abs/path/product.png" \
  --generate \
  --generate-timeout 600 \
  --prompt "电商主图，4:5，留白标题区"
```

说明：Picset 为**网页自动化**，无官方 API；页面改版时优先查 `picset_automation.py` / `xhs_images_to_picset.py` 内「登录检测、上传槽、prompt、生成按钮」逻辑。  
说明：**Photoshop（PS）链路未下架**：对已下载的 **Picset 生成图**做「图像→自动色调/对比度/颜色」等价 JSX 批处理的仍是 **`--photoshop-after-generate`**（参见下文「本机约定（Photoshop）」），与是否使用无痕解析站**无关**。该步**默认不自动开启**，须在 `full_stack` / `bulk` / `zero_touch` / `visual`（且带 `--generate`）上显式加上该开关，并装好 PS + **`pip install pywin32`**。  
说明：**`full_stack_xhs_picset_publish.py` / `bulk_publish_accounts.py` / `zero_touch_xhs.py` / 默认参数的 `visual_publish_pipeline.py` 不包含**第三方「无痕清印解析站」步骤；流程是：**拉参考封面 → 直接进 Picset（与产品素材）生成**。若想对**小红书下载的封面**做预处理，推荐使用 **`--watermark-full-auto`**（本地右下角蒙版 inpaint + Pillow，可选 JSX，走 `postprocess/` 本地目录），或**不传**预处理则直接使用原参考图。**若必须坚持人工站外解析**，仅当你**手动**在同一条命令里传入 **`--watermark-post-workflow`** 等标志时生效（脚本**不能**替你完成网站内上传/框选）。  
说明：**（旧版人工站外）**：**`--watermark-post-workflow`**（会写说明并可打开 [无痕清印](https://wuhenqingyin.com.cn/#) …）/`--watermark-full-auto`/`--watermark-no-open-watermark-url`/`--watermark-photoshop` 等仍为 **直接调用** `xhs_images_to_picset`/`visual_publish_pipeline` 的高级选项。  
说明：Picset **生成参考图/主图**下载到本机后，若要再按 Photoshop 菜单「**图像 → 自动色调 / 自动对比度 / 自动颜色**」批量处理（与快捷键 Shift+Ctrl+L 等一致由 JSX 尽力调用），请加 **`--photoshop-after-generate`**（必须同时 **`--generate`**；需 PS + `pip install pywin32`）。处理后图片在生成图目录下的 `postprocess_ps/after_photoshop_autotcc/`，`publish_to_xhs` 与 summary 会优先使用该目录中的文件。  
说明：小红书图床 CDN 下载依赖正确 **Referer**（脚本已处理）；若仍 403，检查网络与 URL 是否过期。  
说明：生成结果采集偶发超时或 WebP 校验异常时，可提高 `--generate-timeout`；脚本已尽量兼容 Picset 产出的 WebP。  
说明：**默认生成 1 张**（`--max-download` 与 `--picset-batch-size` 默认均为 **1**）；脚本会在点击「生成」前**尽力把 Picset「生成数量」切到 N 张（含 1 张）**。若曾手动选过「4 张」，下次跑默认 1 张时也会尝试改回（见 `picset_automation._picset_try_set_batch_count`）。需要多张时在命令行显式传例如 `--max-download 4 --picset-batch-size 4`。  
说明：生成图会按文件内容 **SHA-256 去重**；若你设置 `--max-download` 为 K 而最终不足 K 张**不同图片**，相关逻辑会按脚本报错或重试（不会故意带重复图凑数）。  
说明：默认结束**不断开** DevTools（保留浏览器页）；需要显式断开时加 **`--disconnect-cdp`**（`cdp_publish` / `publish_pipeline` / Picset 相关脚本见各自 `--help`）。

**本机约定（Photoshop）：** 「画好的图 → 放进 Photoshop → **图像** 栏三步」这一套，流水线里已对齐为：**先把成品图拷贝到脚本指定的输入目录**，再 **COM/JSX 自动**对每张图执行与界面一致的一套命令——**图像 → 自动色调 → 自动对比度 → 自动颜色**（ExtendScript：`autoTone` / `autoContrast` / `autoColor`），并把结果写入输出目录。  
**固定执行标准（避免反复改）：** 对每张图按以下等价链路执行且顺序不可变：**进入 Photoshop → 打开(导入)生成图 → 图像 → 自动色调 → 自动对比度 → 自动颜色 → 保存到输出目录**。  
**目录约定：** Picset **`--photoshop-after-generate`**：`生成图目录/postprocess_ps/_staging_for_ps/`（拷贝入）→ 处理后的图在 **`.../after_photoshop_autotcc/`**。去水印全链路 **`--watermark-photoshop`**：暂存在 `postprocess/_staging_pillow/` → 成品 **`postprocess/02_ps导出/`**。若要**额外再复印一份**到自己常用的文件夹路径，成功后设环境变量 **`REDBOOK_PHOTOSHOP_MIRROR_FINAL_TO`**（可为绝对路径，支持 `%USERNAME%` 等展开）。

手动打开 Photoshop 或安装入口时，可从开始菜单双击 **`C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Adobe Photoshop 2025.lnk`**（或「开始 → Adobe Photoshop 2025」）。**当 JSX 批处理成功结束**时，`xhs_image_autofix` 仍会**默认用系统打开上述快捷方式**（不想自动弹出设 `REDBOOK_PHOTOSHOP_NO_OPEN_AFTER_TASK=1`；自定义快捷方式设 `REDBOOK_PHOTOSHOP_STARTMENU_LNK`）。若 COM 异常，可先确认 Photoshop 已在系统中安装完毕，并执行 **`pip install pywin32`**。

**为何好像「没开 PS」：** 脚本默认 **`Photoshop.Application.Visible = false`**，批处理在后台跑，界面可能一闪而过或看不见。若要看到 PS 前台逐张处理，运行前设 **`$env:REDBOOK_PHOTOSHOP_VISIBLE = "1"`**。终端会先打印 **`Photoshop COM 批处理开始`（含输入/输出目录与「图像菜单」说明）**，成功后可继续看到 **`Photoshop 批处理完成，已打开快捷方式：...`** 及按需的 **`已将 N 个成品复制到镜像目录`**。

**（可选用）无痕清印人工流程：** 仅当你在同一命令显式传入 **`--watermark-post-workflow`**（及可选 **`--wait-enter-after-watermark`**、**`--require-watermarked-references`**）时才走：`postprocess/watermark_and_ps_workflow.txt`、默认 **`--watermark-tool-url`** 为 **`https://wuhenqingyin.com.cn/#`**、落盘 **`01_去水印后`** 再上 Picset。这不是一键编排的默认内容。

**全流程若要在 Picset 生成图之后、豆包与发布之前**对「生成图」做 Photoshop「**图像 → 自动色调 / 对比度 / 颜色**」，在 **`full_stack`** / **`bulk`** / **`zero_touch`** / **`visual`**（需 `--generate`）上加 **`--photoshop-after-generate`**（JSX 等价、需 **`pip install pywin32`**）。

补充：更完整的安装与背景见仓库 `README.md`；代理执行以本 Skill 的命令为准。
