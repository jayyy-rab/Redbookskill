"""
After Xiaohongshu reference images are downloaded:

- **Manual** (--watermark-post-workflow): README + optional open browser (无痕清印) + Explorer.
- **Full auto** (--watermark-full-auto): local inpaint corner + Pillow auto-enhance +
  optional Photoshop COM batch (`--watermark-photoshop`); no third-party site.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from pipeline_debug import pipeline_debug_log


def open_watermark_tool_url(
    url: str | None = None,
    *,
    label: str = "[postprocess]",
) -> bool:
    """
    Open the 无痕清印 (or other) watermark tool URL in the default browser.
    On Windows, webbrowser.open is unreliable — try ShellExecuteW / cmd start fallback.
    """
    u = (url or "").strip() or "https://wuhenqingyin.com.cn/#"
    errs: list[str] = []

    try:
        if webbrowser.open(u):
            print(f"{label} 已在默认浏览器打开: {u}")
            return True
    except Exception as e:
        errs.append(str(e))

    if sys.platform == "win32":
        try:
            import ctypes  # noqa: PLC0415

            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "open", u, None, None, 1
            )  # SW_SHOWNORMAL
            if int(rc) > 32:
                print(f"{label} 已通过 ShellExecute 打开浏览器: {u}")
                return True
        except Exception as e:
            errs.append(str(e))
        try:
            subprocess.run(
                ["cmd", "/c", "start", "", u],
                check=False,
                shell=False,
            )
            print(f"{label} 已通过 cmd start 请求打开: {u}")
            return True
        except Exception as e:
            errs.append(str(e))

    print(
        f"{label} Warning: 无法自动打开浏览器 ({'; '.join(errs) or 'unknown'})，请手动在浏览器访问: {u}",
        file=sys.stderr,
    )
    return False


def print_watermark_site_human_gate(
    watermark_url: str,
    *,
    clean_dir: str | Path,
    label: str = "[解析站门禁]",
) -> None:
    """
    Terminal checklist before Picset regenerate: user must verify the third-party parse
    site (default 无痕清印) login and exported images under postprocess/01_去水印后.
    """
    u = (watermark_url or "").strip() or "https://wuhenqingyin.com.cn/#"
    d = Path(clean_dir)
    print("", flush=True)
    print(f"{label} ==============================================", flush=True)
    print(f"{label} 下一站才是 Picset 生图；请先确认解析网站步骤完成。", flush=True)
    print(f"{label}", flush=True)
    print(f"{label} 【1】在浏览器打开解析站并确认登录（若打不开请复制 URL 手动粘贴）：", flush=True)
    print(f"{label}     {u}", flush=True)
    print(f"{label}", flush=True)
    print(f"{label} 【2】将本次下载目录中的小红书参考图上传到解析站并完成「解析/下载」。", flush=True)
    print(f"{label} 【3】把无水印成品保存到此目录（与 README 约定一致）：", flush=True)
    print(f"{label}     {d.resolve()}", flush=True)
    print(f"{label}", flush=True)
    print(f"{label} 以上内容确认就绪后，回到本终端按回车 → 再继续 Picset 上传与生成。", flush=True)
    print(f"{label} ==============================================", flush=True)
    print("", flush=True)


def materialize_after_xhs_download(
    base_dir: str | Path,
    downloaded_paths: list[str],
    *,
    watermark_url: str = "https://wuhenqingyin.com.cn/#",
    open_browser: bool = True,
    open_folder: bool = True,
) -> Path:
    """
    Create postprocess/ with subdirs, list of source files, and workflow README.
    Returns path to postprocess root.
    """
    base = Path(base_dir).resolve()
    pp = base / "postprocess"
    clean_dir = pp / "01_去水印后"
    ps_out = pp / "02_ps导出"
    pp.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(exist_ok=True)
    ps_out.mkdir(exist_ok=True)

    readme = pp / "watermark_and_ps_workflow.txt"
    url = (watermark_url or "").strip() or "https://wuhenqingyin.com.cn/#"

    lines: list[str] = [
        f"小红书参考图下载目录：{base}",
        "（本说明位于该目录下的 postprocess 子文件夹内。）",
        "下列为本次记录的本地文件路径（用于对照上传到无痕清印）：",
        "",
    ]
    for p in downloaded_paths:
        lines.append(f"  - {Path(p).resolve()}")
    lines.extend(
        [
            "",
            "--------------------------------------------------------------------",
            "【步骤 1】无痕清印 — 去掉「小红书」等平台水印",
            f"  在浏览器打开：{url}",
            "  将上方列表中的图片逐个上传处理，把无水印结果保存到本目录下的：",
            f"    {clean_dir}",
            "  （平台规则与账号权益请自行遵守；本站为第三方工具，仅作流程衔接说明。）",
            "",
            "--------------------------------------------------------------------",
            "【步骤 2】（可选）Windows「画图」检查/裁剪",
            "  在「文件资源管理器」打开上述文件夹，右键图片 → 打开方式 → 画图。",
            "",
            "--------------------------------------------------------------------",
            "【步骤 3】Adobe Photoshop — 自动色调 / 自动对比度 / 自动颜色",
            "  只需「录制动作」一次，以后可批处理：",
            "  A. Photoshop → 窗口 → 动作 → 新建动作，命名为例如「XHS_AutoTCC」，开始录制",
            "     → 图像 → 自动色调",
            "     → 图像 → 自动对比度",
            "     → 图像 → 自动颜色",
            "     → 停止录制。",
            "  B. 文件 → 自动 → 批处理：",
            f"     源文件夹：{clean_dir}",
            "     动作：「XHS_AutoTCC」",
            f"     目标文件夹：{ps_out}",
            "",
            "--------------------------------------------------------------------",
            "完成后，可将「02_ps导出」中的成品用于 Picset / 发布流水线。",
            "",
        ]
    )
    readme.write_text("\n".join(lines), encoding="utf-8")

    print(f"[postprocess] 已写入流程说明: {readme}")
    print(f"[postprocess] 请把去水印后的图保存到: {clean_dir}")

    if open_browser:
        open_watermark_tool_url(url, label="[postprocess]")

    if open_folder and sys.platform == "win32":
        try:
            os.startfile(str(base))  # noqa: S606
            print(f"[postprocess] 已打开资源管理器: {base}")
        except Exception as e:
            print(f"[postprocess] Warning: could not open Explorer: {e}", file=sys.stderr)

    return pp


def run_full_auto_pipeline(
    base_dir: str | Path,
    downloaded_paths: list[str],
    *,
    no_inpaint: bool = False,
    corner_width_ratio: float = 0.36,
    corner_height_ratio: float = 0.14,
    photoshop_batch: bool = False,
) -> dict[str, Any]:
    """
    Local pipeline: heuristic inpaint mask (bottom-right), Pillow auto-enhance JPEG,
    optionally Photoshop COM batch applying auto tone/contrast/color (best-effort).

    Final JPEGs appear under postprocess/02_ps导出/.
    """
    base = Path(base_dir).resolve()
    pp = base / "postprocess"
    d01 = pp / "01_去水印后"
    staging = pp / "_staging_pillow"
    d02 = pp / "02_ps导出"
    for d in (pp, d01, staging, d02):
        d.mkdir(parents=True, exist_ok=True)

    from xhs_image_autofix import (  # noqa: PLC0415
        inpaint_bottom_right_corner,
        pillow_post_adjust,
        run_photoshop_batch_auto_tcc,
        summarize_paths,
    )

    clean_paths: list[str] = []
    staging_paths: list[str] = []

    for src in downloaded_paths:
        srcp = Path(src)
        if not srcp.is_file():
            continue
        stem = srcp.stem
        suf = srcp.suffix if srcp.suffix.lower() in (
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        ) else ".jpg"
        clean = d01 / f"{stem}{suf}"
        pj = staging / f"{stem}.jpg"

        if no_inpaint:
            shutil.copy2(srcp, clean)
        else:
            inpaint_bottom_right_corner(
                srcp,
                clean,
                width_ratio=float(corner_width_ratio),
                height_ratio=float(corner_height_ratio),
            )

        pillow_post_adjust(clean, pj)

        clean_paths.append(str(clean.resolve()))
        staging_paths.append(str(pj.resolve()))

    ps_ok = False
    staging_file_n = (
        sum(1 for x in staging.iterdir() if x.is_file())
        if staging.exists()
        else 0
    )
    if photoshop_batch and staging_file_n > 0:
        ps_ok = bool(run_photoshop_batch_auto_tcc(staging, d02))
        out_n = sum(1 for p in d02.iterdir() if p.is_file())
        # region agent log
        pipeline_debug_log(
            "H5",
            "postprocess_xhs_workflow.py:run_full_auto_pipeline",
            "after photoshop batch",
            {
                "staging_file_n": staging_file_n,
                "d02_file_n": out_n,
                "ps_dispatch_ok": ps_ok,
                "will_downgrade": bool(
                    staging_file_n > 0
                    and ps_ok
                    and (out_n == 0 or out_n != staging_file_n),
                ),
            },
        )
        # endregion
        if (
            staging_file_n > 0
            and ps_ok
            and (out_n == 0 or out_n != staging_file_n)
        ):
            ps_ok = False
            print(
                "[postprocess-full] Photoshop 输出数量与暂存不一致或为空，已回退为 Pillow 复制到 02_ps导出。",
                file=sys.stderr,
            )
    elif photoshop_batch and staging_file_n == 0:
        print(
            "[postprocess-full] 无有效图片进入暂存，已跳过 Photoshop。",
            file=sys.stderr,
        )

    use_pillow_fallback = (not photoshop_batch) or (not ps_ok)
    if use_pillow_fallback:
        if photoshop_batch and not ps_ok:
            print(
                "[postprocess-full] Photoshop COM 不可用或 JSX 失败；改用 Pillow JPG 导出到 02_ps导出。"
                "（可安装 Photoshop + pip install pywin32，再试用 --watermark-photoshop。）",
                file=sys.stderr,
            )
        for pj in staging_paths:
            shutil.copy2(pj, d02 / Path(pj).name)
    else:
        print("[postprocess-full] Photoshop ExtendScript 批处理完成 → postprocess\\02_ps导出")

    final_paths = sorted(str(p.resolve()) for p in d02.iterdir() if p.is_file())

    log_path = pp / "auto_pipeline_log.json"
    payload: dict[str, Any] = {
        "mode": "full_auto",
        "clean_paths": clean_paths,
        "pillow_paths": staging_paths,
        "photoshop_attempted": bool(photoshop_batch),
        "photoshop_ok": bool(ps_ok),
        "output_dir_final": str(d02.resolve()),
        "final_files": final_paths,
    }
    summarize_paths(log_path, payload)
    print(f"[postprocess-full] Wrote log: {log_path}")

    payload["final_jpgs_under_02"] = final_paths

    return payload


def materialize_generated_photoshop_autotcc(
    generated_root: str | Path,
    downloaded_paths: list[str],
) -> tuple[list[str], dict[str, Any]]:
    """
    Picset「生成参考图」已落盘后：对这批文件做 Photoshop 中与菜单
    「图像→自动色调 / 自动对比度 / 自动颜色」一致的 ExtendScript 批处理（autoTone/autoContrast/autoColor）。
    结果写在 generated_root/postprocess_ps/after_photoshop_autotcc/；
    COM 失败时把原文件复制到该目录，尽量不中断下游发布。
    """
    root = Path(generated_root).resolve()
    pp = root / "postprocess_ps"
    staging = pp / "_staging_for_ps"
    outd = pp / "after_photoshop_autotcc"

    if staging.exists():
        shutil.rmtree(staging)
    if outd.exists():
        shutil.rmtree(outd)
    staging.mkdir(parents=True, exist_ok=True)
    outd.mkdir(parents=True, exist_ok=True)

    for p in downloaded_paths:
        src = Path(p)
        if not src.is_file():
            continue
        shutil.copy2(src, staging / src.name)

    staging_files = sorted(
        f for f in staging.iterdir() if f.is_file()
    )
    staging_n = len(staging_files)
    # region agent log
    pipeline_debug_log(
        "H3",
        "postprocess_xhs_workflow.py:materialize_generated_photoshop_autotcc",
        "staging after copy",
        {"staging_n": staging_n, "dl_len": len(downloaded_paths)},
    )
    # endregion

    from xhs_image_autofix import (  # noqa: PLC0415
        open_photoshop_start_menu_shortcut_after_task,
        pillow_post_adjust,
        run_photoshop_batch_auto_tcc,
        summarize_paths,
    )

    if staging_n == 0:
        meta = {
            "mode": "picset_generated_photoshop",
            "generated_root": str(root),
            "photoshop_ok": False,
            "error": "no_valid_inputs_copied_to_staging",
            "outputs": [],
            "inputs": [str(Path(p).resolve()) for p in downloaded_paths],
        }
        summarize_paths(pp / "generated_photoshop_log.json", meta)
        print(
            "[postprocess-ps] 无有效输入文件复制到暂存目录，跳过 PS。",
            file=sys.stderr,
        )
        return downloaded_paths, meta

    ps_dispatch_ok = bool(run_photoshop_batch_auto_tcc(staging, outd))
    out_n = sum(1 for _ in outd.iterdir() if _.is_file())
    verified = (
        ps_dispatch_ok
        and out_n > 0
        and out_n == staging_n
    )
    # region agent log
    pipeline_debug_log(
        "H1",
        "postprocess_xhs_workflow.py:materialize_generated_photoshop_autotcc",
        "after jsx before fallback",
        {
            "staging_n": staging_n,
            "out_n": out_n,
            "ps_dispatch_ok": ps_dispatch_ok,
            "verified": verified,
        },
    )
    # endregion

    if not verified:
        # Guarantee the "contrast + color" requirement even when Photoshop JSX fails.
        # We apply the same intent via Pillow (auto-contrast + Color boost) and
        # always export as JPG so downstream tools (e.g. drawing apps) can open them.
        if ps_dispatch_ok:
            print(
                "[postprocess-ps] Photoshop JSX 未验证成功（输出不匹配或为空）。"
                "改用 Pillow 做自动对比度/颜色并导出 JPG 到 after_photoshop_autotcc。",
                file=sys.stderr,
            )
        else:
            print(
                "[postprocess-ps] Photoshop COM/JSX 未成功。"
                "改用 Pillow 做自动对比度/颜色并导出 JPG 到 after_photoshop_autotcc。"
                "（原逻辑：直接复制原图）",
                file=sys.stderr,
            )

        # Clear outd to avoid mixing stale/copied files with Pillow outputs.
        if outd.exists():
            for f in outd.iterdir():
                try:
                    if f.is_file():
                        f.unlink()
                except Exception:
                    pass

        for f in staging_files:
            src = Path(f)
            # Use a stable JPG name; stem derived from original file name.
            out_path = outd / f"{src.stem}.jpg"
            pillow_post_adjust(src, out_path)

    ps_ok = verified
    out_paths = sorted(str(p.resolve()) for p in outd.iterdir() if p.is_file())
    if out_paths:
        # Even when Photoshop JSX fails (Pillow fallback),
        # still open the Photoshop shortcut so you can manually adjust.
        # (The helper already respects REDBOOK_PHOTOSHOP_NO_OPEN_AFTER_TASK.)
        try:
            open_photoshop_start_menu_shortcut_after_task()
        except Exception:
            pass
    # region agent log
    pipeline_debug_log(
        "H1",
        "postprocess_xhs_workflow.py:materialize_generated_photoshop_autotcc",
        "final outputs",
        {"out_count": len(out_paths), "ps_ok_meta": ps_ok},
    )
    # endregion
    meta: dict[str, Any] = {
        "mode": "picset_generated_photoshop",
        "generated_root": str(root),
        "photoshop_ok": bool(ps_ok),
        "outputs": out_paths,
        "inputs": [str(Path(p).resolve()) for p in downloaded_paths],
        "menu_equivalent": "图像 → 自动色调(Shift+Ctrl+L) / 自动对比度 / 自动颜色(Shift+Ctrl+B)（由 JSX executeAction 尽力调用）",
    }
    summarize_paths(pp / "generated_photoshop_log.json", meta)
    print(
        f"[postprocess-ps] 生成图 PS 批处理输出: {outd} "
        f"（共 {len(out_paths)} 个文件）"
    )

    if not out_paths:
        return downloaded_paths, meta
    return out_paths, meta


def materialize_generated_auto_color(
    generated_root: str | Path,
    downloaded_paths: list[str],
) -> tuple[list[str], dict[str, Any]]:
    """Code-only auto color for generated images (no Photoshop dependency)."""
    root = Path(generated_root).resolve()
    pp = root / "postprocess_ps"
    outd = pp / "after_code_autocolor"
    outd.mkdir(parents=True, exist_ok=True)

    from xhs_image_autofix import pillow_post_adjust, summarize_paths  # noqa: PLC0415

    outputs: list[str] = []
    for p in downloaded_paths:
        src = Path(p)
        if not src.is_file():
            continue
        out_path = outd / f"{src.stem}.jpg"
        pillow_post_adjust(src, out_path)
        if out_path.is_file():
            outputs.append(str(out_path.resolve()))

    meta: dict[str, Any] = {
        "mode": "picset_generated_code_autocolor",
        "generated_root": str(root),
        "autocolor_ok": bool(outputs),
        "outputs": outputs,
        "inputs": [str(Path(p).resolve()) for p in downloaded_paths],
        "engine": "pillow_post_adjust",
    }
    summarize_paths(pp / "generated_autocolor_log.json", meta)
    if not outputs:
        return downloaded_paths, meta
    return outputs, meta
