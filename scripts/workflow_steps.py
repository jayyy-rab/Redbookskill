"""PRD v1.0 统一步骤函数 — 每个步骤独立可调用。

规范：
- 每个 run_stepN_xxx(ctx: PipelineContext) -> StepResult
- 唯一参数 PipelineContext，返回值 StepResult
- 禁止散传参数，禁止步骤自己定义路径/变量名
- 使用已有脚本（subprocess），不复制逻辑
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workflow_core import (
    ArtifactRegistry,
    ErrorLevel,
    PipelineContext,
    StepErrorPayload,
    StepEvidence,
    StepResult,
    StepStatus,
    StepSuccessCheck,
)
from workflow_io import build_paths, ensure_task_dirs, save_screenshot, save_step_result


# ── helpers ──


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _script(rel: str) -> Path:
    return Path(__file__).resolve().parent / rel


def _run_script(
    script_name: str,
    args: list[str],
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_script(script_name)), *args]
    if script_name in ("cdp_publish.py", "publish_pipeline.py"):
        port = os.environ.get("XHS_CDP_PORT") or "9322"
        cmd[2:2] = ["--port", port]
    cwd = _script("..")
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )


def _check_timeout(cp: subprocess.CompletedProcess, timeout: int) -> str | None:
    if cp.returncode == -9:
        return "进程被杀死 (OOM / 信号 9)"
    if cp.returncode == -15:
        return f"进程超时 ({timeout}s)"
    return None


def _parse_summary_json(summary_path: Path) -> dict[str, Any] | None:
    if summary_path.is_file():
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _rule_to_score(rule: str) -> float:
    """将商品匹配 rule 映射为数值 match_score。"""
    mapping = {
        "product_id_exact": 1.0,
        "product_name_exact": 0.9,
        "product_name_fuzzy_contains": 0.75,
    }
    return mapping.get(rule, 0.0)


def _new_result(ctx: PipelineContext, step_id: str, step_name: str) -> StepResult:
    return StepResult(
        task_id=ctx.task_id,
        account_id=ctx.account_id,
        step_id=step_id,
        step_name=step_name,
        status=StepStatus.PENDING.value,
        started_at=_now(),
        finished_at="",
    )


def _fail(result: StepResult, *, code: str, message: str, level: str = ErrorLevel.P2.value,
          detail: Any = None, action: str = "") -> StepResult:
    result.status = StepStatus.FAILED.value
    result.finished_at = _now()
    result.error = StepErrorPayload(
        code=code, message=message, detail=detail or {},
        error_level=level, action=action,
    )
    return result


def _succeed(result: StepResult, *, evidence: StepEvidence | None = None,
             artifacts: dict | None = None, created: list[str] | None = None) -> StepResult:
    result.status = StepStatus.SUCCESS.value
    result.finished_at = _now()
    if evidence:
        result.evidence = evidence
    if artifacts:
        result.artifacts = artifacts
    if created:
        result.created_files = created
    result.success_check = StepSuccessCheck(passed=True)
    return result


# ════════════════════════════════════════════════════════════
# Step 1: 输入校验
# ════════════════════════════════════════════════════════════


def run_step1_validate_input(ctx: PipelineContext) -> StepResult:
    """校验客户输入：商品图存在、关键词/商品名/文案非空。"""
    result = _new_result(ctx, "step1_validate_input", "校验输入")

    problems: list[str] = []
    valid_imgs: list[str] = []

    for p in ctx.artifacts.product_images or ctx.input.product_images:
        path = Path(p)
        if not path.is_file():
            problems.append(f"商品图不存在: {p}")
        elif path.stat().st_size < 1024:
            problems.append(f"商品图太小 (<1KB): {p}")
        else:
            valid_imgs.append(p)

    if not valid_imgs:
        problems.append("无有效的商品图片")
    if not ctx.seed_keyword:
        problems.append("搜索关键词为空")
    if not ctx.product_name and not ctx.input.allow_no_product:
        problems.append("商品名称为空")
    if not ctx.brief:
        problems.append("文案简述为空")
    if not ctx.input.accounts:
        problems.append("账号列表为空")

    if problems:
        return _fail(result, code="VALIDATION_FAILED", message="; ".join(problems),
                     level=ErrorLevel.P1.value, detail={"problems": problems})

    return _succeed(result, artifacts={"valid_images": valid_imgs})


# ════════════════════════════════════════════════════════════
# Step 2: 小红书搜索关键词
# ════════════════════════════════════════════════════════════


def run_step2_search_xhs_keyword(ctx: PipelineContext) -> StepResult:
    """通过 CDP 搜索小红书关键词。"""
    result = _new_result(ctx, "step2_search_xhs_keyword", "小红书搜索关键词")

    keyword = ctx.seed_keyword
    if not keyword:
        return _fail(result, code="NO_KEYWORD", message="关键词为空", level=ErrorLevel.P1.value)

    cp = _run_script("cdp_publish.py", [
        "--account", ctx.account_id or "acc_a",
        "search-feeds", "--keyword", keyword,
        "--sort-by", "最多点赞", "--note-type", "图文", "--publish-time", "一周内",
    ], timeout=300)

    timeout_err = _check_timeout(cp, 300)
    if timeout_err:
        return _fail(result, code="STEP2_TIMEOUT", message=timeout_err, level=ErrorLevel.P2.value,
                     action="retry")

    if cp.returncode != 0:
        return _fail(result, code="SEARCH_FAILED",
                     message=f"搜索失败 (exit={cp.returncode})",
                     detail={"stderr": (cp.stderr or "")[-1000:]},
                     level=ErrorLevel.P2.value, action="retry")

    feed_count = 0
    for line in (cp.stdout or "").splitlines():
        if "SEARCH_FEEDS_RESULT:" in line:
            try:
                payload = json.loads(line.split("SEARCH_FEEDS_RESULT:")[-1].strip())
                feed_count = int(payload.get("count", 0))
            except Exception:
                pass

    if feed_count == 0:
        return _succeed(result, evidence=StepEvidence(url="", screenshot=""),
                        artifacts={"feed_count": 0})
    return _succeed(result, artifacts={"feed_count": feed_count})


# ════════════════════════════════════════════════════════════
# Step 3: 筛选参考帖子
# ════════════════════════════════════════════════════════════


def run_step3_select_reference_post(ctx: PipelineContext) -> StepResult:
    """筛选搜索结果，下载参考图。"""
    result = _new_result(ctx, "step3_select_reference_post", "筛选参考帖子")

    product_image = ctx.artifacts.product_images[0] if ctx.artifacts.product_images else ""
    output_dir = Path(ctx.paths.artifacts_dir) / "refs" if ctx.paths.artifacts_dir else _script("..") / "tmp" / "refs"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"

    cp = _run_script("step3_select_and_download_refs.py", [
        "--keyword", ctx.seed_keyword,
        "--product-image", product_image,
        "--top-k", "6",
        "--output-dir", str(output_dir),
        "--summary-json", str(summary_path),
        "--host", "127.0.0.1", "--port", "9322",
        "--account", ctx.account_id or "acc_a",
    ], timeout=300)

    timeout_err = _check_timeout(cp, 300)
    if timeout_err:
        return _fail(result, code="STEP3_TIMEOUT", message=timeout_err, level=ErrorLevel.P2.value,
                     action="retry")

    if cp.returncode != 0:
        return _fail(result, code="SELECT_REFS_FAILED",
                     message=f"参考图筛选失败 (exit={cp.returncode})",
                     detail={"stderr": (cp.stderr or "")[-1000:]},
                     level=ErrorLevel.P2.value, action="retry")

    summary = _parse_summary_json(summary_path)
    ref_paths: list[str] = []
    ref_url = ""
    if summary:
        ref_paths = summary.get("local_paths") or summary.get("downloaded_paths") or []
        ref_url = summary.get("source_url", "")

    ctx.artifacts.reference_images = ref_paths

    return _succeed(result,
                    evidence=StepEvidence(screenshot="", url=ref_url),
                    artifacts={"reference_count": len(ref_paths), "reference_paths": ref_paths},
                    created=ref_paths)


# ════════════════════════════════════════════════════════════
# Step 4: 生成图片
# ════════════════════════════════════════════════════════════


def run_step4_generate_image(ctx: PipelineContext) -> StepResult:
    """Picset AI 生成商品展示图。"""
    result = _new_result(ctx, "step4_generate_image", "画图软件生成新图")

    # ── Checkpoint: reuse existing generated images ──
    existing = ctx.artifacts.generated_images
    if existing:
        valid = [p for p in existing if Path(p).is_file() and Path(p).stat().st_size > 1024]
        if valid:
            print(f"[step4] reuse {len(valid)} existing generated image(s), skip Picset")
            return _succeed(result, artifacts={
                "generated_count": len(valid), "generated_paths": valid, "skipped": True,
            })

    product_images = ctx.artifacts.product_images or ctx.input.product_images
    reference_images = ctx.artifacts.reference_images
    prompt = ctx.artifacts.title_text or f"电商详情主图：{ctx.seed_keyword}商品展示，竖版 4:5"

    summary_path = Path(ctx.paths.artifacts_dir) / "picset_summary.json" if ctx.paths.artifacts_dir \
        else _script("..") / "tmp" / "picset_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        "--keyword", ctx.seed_keyword,
        "--account", ctx.account_id or "acc_a",
        "--port", os.environ.get("XHS_CDP_PORT") or "9322",
        "--max-images", "1", "--generate",
        "--generate-timeout", "1200",
        "--product-images", *product_images,
        *([] if not reference_images else ["--reference-images", *reference_images]),
        "--prompt", prompt,
        "--summary-json", str(summary_path),
        "--no-strict-step-lock",
    ]
    cp = _run_script("xhs_images_to_picset.py", args, timeout=1800)

    timeout_err = _check_timeout(cp, 1800)
    if timeout_err:
        return _fail(result, code="STEP4_TIMEOUT", message=timeout_err, level=ErrorLevel.P2.value,
                     action="retry")

    if cp.returncode != 0:
        return _fail(result, code="GENERATE_FAILED",
                     message=f"Picset 生成失败 (exit={cp.returncode})",
                     detail={"stderr": (cp.stderr or "")[-1000:]},
                     level=ErrorLevel.P2.value, action="retry")

    summary = _parse_summary_json(summary_path)
    gen_paths: list[str] = []
    if summary:
        gen_paths = summary.get("generated_local_paths") or []

    if not gen_paths:
        return _fail(result, code="NO_GENERATED_IMAGE", message="生成完成但未找到图片文件",
                     level=ErrorLevel.P1.value, action="stop_account")

    ctx.artifacts.generated_images = gen_paths
    return _succeed(result,
                    artifacts={"generated_count": len(gen_paths), "generated_paths": gen_paths},
                    created=gen_paths)


# ════════════════════════════════════════════════════════════
# Step 5: 自动调色
# ════════════════════════════════════════════════════════════


def run_step5_adjust_image_color(ctx: PipelineContext) -> StepResult:
    """对生成图自动调色，输出最终图。"""
    result = _new_result(ctx, "step5_adjust_image_color", "自动调色")

    gen_images = ctx.artifacts.generated_images
    if not gen_images:
        return _succeed(result, artifacts={"note": "无已生成图片，跳过调色"})

    artifacts_dir = Path(ctx.paths.artifacts_dir) if ctx.paths.artifacts_dir else None
    screenshots_dir = Path(ctx.paths.screenshots_dir) if ctx.paths.screenshots_dir else None

    # ── 检查每张生成图 ──
    valid_images: list[str] = []
    for p in gen_images:
        pp = Path(p)
        if not pp.is_file():
            return _fail(result, code="GENERATED_IMAGE_NOT_FOUND",
                         message=f"生成图文件不存在: {p}",
                         level=ErrorLevel.P1.value, action="stop_account")
        if pp.stat().st_size < 1024:
            return _fail(result, code="GENERATED_IMAGE_TOO_SMALL",
                         message=f"生成图太小 (<1KB): {p}",
                         level=ErrorLevel.P1.value, action="stop_account")
        valid_images.append(p)

    # ── 调色处理 ──
    final_images: list[str] = []
    color_adjust_script = _script("color_adjust.py")
    has_color_adjust = color_adjust_script.is_file()
    used_fallback = False

    for img_path in valid_images:
        src = Path(img_path)
        stem = src.stem
        final_name = f"{stem}_final{src.suffix}"

        if artifacts_dir:
            final_dest = artifacts_dir / final_name
        else:
            final_dest = src.parent / final_name

        if has_color_adjust:
            try:
                cp = _run_script("color_adjust.py", ["--input", img_path, "--output", str(final_dest)], timeout=120)
                if cp.returncode != 0 or not final_dest.is_file():
                    raise RuntimeError(f"color_adjust.py exit={cp.returncode}")
            except Exception as exc:
                print(f"[step5] color_adjust.py failed ({exc}), fallback copy")
                shutil.copy2(src, final_dest)
                used_fallback = True
        else:
            shutil.copy2(src, final_dest)
            used_fallback = True

        if not final_dest.is_file() or final_dest.stat().st_size < 1024:
            return _fail(result, code="FINAL_IMAGE_INVALID",
                         message=f"最终图无效: {final_dest}",
                         level=ErrorLevel.P1.value, action="stop_account")

        final_images.append(str(final_dest))

    # ── 写 color_adjust_report.json ──
    report = {
        "generated_count": len(gen_images),
        "final_count": len(final_images),
        "final_paths": final_images,
        "used_fallback": used_fallback,
        "color_adjust_script_found": has_color_adjust,
        "timestamp": _now(),
    }
    if artifacts_dir:
        report_path = artifacts_dir / "color_adjust_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 复制第一张最终图到 screenshots/step5_final_image.png ──
    if screenshots_dir and final_images:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final_images[0], screenshots_dir / "step5_final_image.png")

    # ── 更新 ctx ──
    ctx.artifacts.final_images = final_images

    return _succeed(result, artifacts={
        "final_count": len(final_images),
        "final_paths": final_images,
        "used_fallback": used_fallback,
    })


# ════════════════════════════════════════════════════════════
# Step 6: 生成文案
# ════════════════════════════════════════════════════════════


def run_step6_generate_copywriting(ctx: PipelineContext) -> StepResult:
    """豆包 / LLM 生成小红书文案。"""
    result = _new_result(ctx, "step6_generate_copywriting", "豆包生成文案")

    out_dir = Path(ctx.paths.artifacts_dir) if ctx.paths.artifacts_dir else _script("..") / "tmp" / "copy"
    out_dir.mkdir(parents=True, exist_ok=True)

    cp = _run_script("douban_promo_copy.py", [
        "--provider", "ark",
        "--seed-keyword", ctx.seed_keyword,
        "--brief", ctx.brief or ctx.seed_keyword,
        "--out-dir", str(out_dir),
    ], timeout=180)

    timeout_err = _check_timeout(cp, 180)
    if timeout_err:
        return _fail(result, code="STEP6_TIMEOUT", message=timeout_err, level=ErrorLevel.P2.value,
                     action="retry")

    if cp.returncode != 0:
        return _fail(result, code="COPY_FAILED",
                     message=f"文案生成失败 (exit={cp.returncode})",
                     detail={"stderr": (cp.stderr or "")[-1000:]},
                     level=ErrorLevel.P2.value, action="retry")

    # Try to load from JSON output
    copy_json = None
    for f in out_dir.iterdir():
        if f.suffix == ".json" and "copy" in f.stem.lower():
            copy_json = f
            break

    title, body = "", ""
    if copy_json and copy_json.is_file():
        try:
            data = json.loads(copy_json.read_text(encoding="utf-8"))
            title = data.get("title", "")
            body = data.get("body", "")
        except Exception:
            pass

    if not title or not body:
        # Fallback: parse stdout
        for line in (cp.stdout or "").splitlines():
            if line.startswith("TITLE:"):
                title = line.replace("TITLE:", "", 1).strip()
            elif line.startswith("BODY:"):
                body = line.replace("BODY:", "", 1).strip()

    if not title or not body:
        # Fallback: read title.txt / content.txt from out_dir
        t_file = out_dir / "title.txt"
        c_file = out_dir / "content.txt"
        if t_file.is_file() and c_file.is_file():
            title = t_file.read_text(encoding="utf-8").strip()
            body = c_file.read_text(encoding="utf-8").strip()

    if not title or not body:
        return _fail(result, code="COPY_EMPTY", message="文案生成结果为空",
                     level=ErrorLevel.P1.value, action="stop_account")

    # Content policy check
    max_chars = ctx.config.max_body_chars
    min_chars = ctx.config.min_body_chars
    if len(body) > max_chars:
        body = body[:max_chars]
    if len(body) < min_chars:
        pass  # warn only, don't fail

    ctx.artifacts.title_text = title
    ctx.artifacts.body_text = body
    if copy_json:
        ctx.artifacts.copywriting_json_path = str(copy_json)

    # ── 写 copywriting.json ──
    cw = {
        "title_text": title, "body_text": body,
        "title_length": len(title), "body_length": len(body),
        "source": "ark", "created_at": _now(),
        "step": "step6", "status": "success",
    }
    cw_path = out_dir / "copywriting.json"
    cw_path.write_text(json.dumps(cw, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.artifacts.copywriting_json_path = str(cw_path)

    return _succeed(result,
                    evidence=StepEvidence(title_text=title, body_text=body),
                    artifacts={"title": title, "body": body, "json_path": str(copy_json) if copy_json else str(cw_path)},
                    created=[str(copy_json)] if copy_json else [str(cw_path)])


# ════════════════════════════════════════════════════════════
# Step 7: 打开创作者发布页
# ════════════════════════════════════════════════════════════


def run_step7_open_creator_page(ctx: PipelineContext) -> StepResult:
    """打开小红书创作者发布页，检查登录态。"""
    result = _new_result(ctx, "step7_open_creator_page", "打开创作者发布页")

    # 使用 cdp_publish.py check-login 检查登录并获取当前 URL
    cp = _run_script("cdp_publish.py", [
        "--account", ctx.account_id or "acc_a",
        "--reuse-existing-tab",
        "check-login",
    ], timeout=120)

    timeout_err = _check_timeout(cp, 120)
    if timeout_err:
        return _fail(result, code="STEP7_TIMEOUT", message=timeout_err,
                     level=ErrorLevel.P2.value, action="retry")

    # 解析 stdout 中的 Current URL
    current_url = ""
    for line in (cp.stdout or "").splitlines():
        if "Current URL:" in line:
            current_url = line.split("Current URL:", 1)[-1].strip()
            break
    cache_hit = any("Login confirmed (cached)" in line for line in (cp.stdout or "").splitlines())

    # 风控/验证码检测
    stdout_lower = (cp.stdout or "").lower()
    stderr_lower = (cp.stderr or "").lower()
    combined = stdout_lower + stderr_lower
    if any(kw in combined for kw in ("风控", "验证码", "captcha", "risk_control")):
        return _fail(result, code="RISK_CONTROL_REQUIRED", message="检测到风控/验证码",
                     level=ErrorLevel.P1.value, action="stop_account",
                     detail={"url": current_url})

    # 登录态检查 — 未登录时等待扫码（retry_policy: 120s，10s 间隔）
    LOGIN_WAIT = int(os.environ.get("XHS_LOGIN_WAIT", "120"))
    if cp.returncode != 0 or "login" in current_url.lower():
        deadline = time.time() + LOGIN_WAIT
        print(f"\n=== 账号 '{ctx.account_id}' 未登录，请在 Chrome 窗口扫码登录"
              f"（等待至 {time.strftime('%H:%M:%S', time.localtime(deadline))}）===\n", flush=True)
        while time.time() < deadline:
            time.sleep(10)
            cp = _run_script("cdp_publish.py", ["--account", ctx.account_id or "acc_a",
                              "--reuse-existing-tab", "check-login"], timeout=30)
            if cp.returncode == 0:
                for line in (cp.stdout or "").splitlines():
                    if "Current URL:" in line:
                        current_url = line.split("Current URL:", 1)[-1].strip()
                break
        else:
            return _fail(result, code="LOGIN_REQUIRED", message=f"等待{LOGIN_WAIT}s后仍未登录",
                         level=ErrorLevel.P1.value, action="stop_account", detail={"url": current_url})
        print(f"=== 账号 '{ctx.account_id}' 登录成功 ===\n", flush=True)

    # URL 必须是发布页（空 URL 时跳过 — 缓存命中无新导航）
    if current_url and "creator.xiaohongshu.com/publish" not in current_url:
        return _fail(result, code="CREATOR_URL_INVALID",
                     message=f"非发布页: {current_url}",
                     level=ErrorLevel.P1.value, action="stop_account",
                     detail={"url": current_url})

    # 写 login_status.json
    login_status = {
        "url": current_url if current_url else "(cached - see screenshot)",
        "logged_in": True,
        "timestamp": _now(),
        "cache_hit": cache_hit,
    }
    evidence_dir = Path(ctx.paths.evidence_dir) if ctx.paths.evidence_dir else None
    if evidence_dir:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "login_status.json").write_text(
            json.dumps(login_status, ensure_ascii=False, indent=2), encoding="utf-8")

    # 截图 step7_creator_page.png
    screenshot_path = ""
    screenshots_dir = Path(ctx.paths.screenshots_dir) if ctx.paths.screenshots_dir else None
    if screenshots_dir:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        ss_path = screenshots_dir / "step7_creator_page.png"
        _run_script("cdp_publish.py", [
            "--account", ctx.account_id or "acc_a",
            "--reuse-existing-tab",
            "screenshot", "--output", str(ss_path),
        ], timeout=30)
        screenshot_path = str(ss_path)

    return _succeed(result, evidence=StepEvidence(url=current_url, screenshot=screenshot_path))


# ════════════════════════════════════════════════════════════
# Step 8: 填充发布表单
# ════════════════════════════════════════════════════════════


def run_step8_fill_publish_form(ctx: PipelineContext) -> StepResult:
    """上传图片、填入标题和正文。"""
    result = _new_result(ctx, "step8_fill_publish_form", "上传图片和填写文案")

    images = ctx.artifacts.final_images or ctx.artifacts.generated_images
    if not images:
        return _fail(result, code="NO_IMAGES", message="无可用图片",
                     level=ErrorLevel.P1.value, action="stop_account")

    title = ctx.artifacts.title_text
    body = ctx.artifacts.body_text
    if not title or not body:
        return _fail(result, code="NO_COPY", message="文案为空",
                     level=ErrorLevel.P1.value, action="stop_account")

    tmp_dir = _script("..") / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    title_file = tmp_dir / "_step8_title.txt"
    content_file = tmp_dir / "_step8_content.txt"
    title_file.write_text(title + "\n", encoding="utf-8")
    content_file.write_text(body + "\n", encoding="utf-8")

    max_retries = 2
    last_stdout = ""
    last_returncode = -1

    for attempt in range(1, max_retries + 1):
        print(f"[step8] publish_pipeline attempt {attempt}/{max_retries}")
        cp = _run_script("publish_pipeline.py", [
            "--title-file", str(title_file),
            "--content-file", str(content_file),
            "--images", *images,
            "--account", ctx.account_id or "acc_a",
            "--reuse-existing-tab", "--preview",
        ], timeout=600)

        timeout_err = _check_timeout(cp, 600)
        if timeout_err:
            if attempt < max_retries:
                continue
            return _fail(result, code="STEP8_TIMEOUT", message=timeout_err,
                         level=ErrorLevel.P2.value, action="retry")

        last_stdout = cp.stdout or ""
        last_returncode = cp.returncode

        if cp.returncode == 0 and "FILL_STATUS: READY_TO_PUBLISH" in last_stdout:
            break  # success

        if attempt < max_retries:
            print(f"[step8] attempt {attempt} failed (rc={cp.returncode}), retrying...")
    else:
        # All retries exhausted
        # Try to identify which specific failure
        stderr_lower = (cp.stderr or "").lower() if cp else ""
        stdout_lower = last_stdout.lower()

        if ("upload" in stderr_lower or "image" in stderr_lower) and last_returncode != 0:
            return _fail(result, code="IMAGE_UPLOAD_FAILED",
                         message=f"图片上传失败 (retry={max_retries})",
                         detail={"stderr": (cp.stderr or "")[-1000:]},
                         level=ErrorLevel.P1.value, action="stop_account")
        elif "title" in stderr_lower and last_returncode != 0:
            return _fail(result, code="TITLE_FILL_FAILED",
                         message=f"标题填写失败 (retry={max_retries})",
                         detail={"stderr": (cp.stderr or "")[-1000:]},
                         level=ErrorLevel.P1.value, action="stop_account")
        elif ("content" in stderr_lower or "body" in stderr_lower) and last_returncode != 0:
            return _fail(result, code="BODY_FILL_FAILED",
                         message=f"正文填写失败 (retry={max_retries})",
                         detail={"stderr": (cp.stderr or "")[-1000:]},
                         level=ErrorLevel.P1.value, action="stop_account")
        elif last_returncode != 0:
            return _fail(result, code="FILL_FORM_FAILED",
                         message=f"填充发布表单失败 (exit={last_returncode}, retry={max_retries})",
                         detail={"stderr": (cp.stderr or "")[-1000:]},
                         level=ErrorLevel.P1.value, action="stop_account")
        else:
            # returncode 0 but no FILL_STATUS
            result.status = StepStatus.MANUAL_REVIEW.value
            result.finished_at = _now()
            result.error = StepErrorPayload(
                code="FILL_NOT_CONFIRMED",
                message="表单已提交但未收到确认信号",
                detail={"stdout_snippet": (last_stdout or "")[-300:],
                        "suggestion": "请检查发布页表单是否已填写完成"},
                error_level="P3", action="log_only",
            )
            return result

    # ── 写 form_evidence.json ──
    form_evidence = {
        "images": images,
        "title": title,
        "body": body,
        "fill_ok": "FILL_STATUS: READY_TO_PUBLISH" in last_stdout,
        "retry_count": max_retries,
        "returncode": last_returncode,
        "stdout_snippet": last_stdout[-500:],
        "timestamp": _now(),
    }
    evidence_dir = Path(ctx.paths.evidence_dir) if ctx.paths.evidence_dir else None
    if evidence_dir:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "form_evidence.json").write_text(
            json.dumps(form_evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 截图 step8_publish_form_filled.png ──
    screenshots_dir = Path(ctx.paths.screenshots_dir) if ctx.paths.screenshots_dir else None
    screenshot_path = ""
    if screenshots_dir:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        ss_path = screenshots_dir / "step8_publish_form_filled.png"
        _run_script("cdp_publish.py", [
            "--account", ctx.account_id or "acc_a",
            "--reuse-existing-tab",
            "screenshot", "--output", str(ss_path),
        ], timeout=30)
        screenshot_path = str(ss_path)

    return _succeed(result,
                    evidence=StepEvidence(title_text=title, body_text=body, screenshot=screenshot_path),
                    created=[str(title_file), str(content_file)])


# ════════════════════════════════════════════════════════════
# Step 9: 添加商品
# ════════════════════════════════════════════════════════════


def run_step9_attach_product(ctx: PipelineContext) -> StepResult:
    """搜索并挂载商品到笔记。"""
    result = _new_result(ctx, "step9_attach_product", "添加商品")

    product_name = ctx.product_name
    product_id = ctx.product_id

    if not product_name and not product_id:
        result.status = StepStatus.SKIPPED.value
        result.finished_at = _now()
        result.success_check = StepSuccessCheck(passed=True)
        return result

    images = ctx.artifacts.final_images or ctx.artifacts.generated_images
    title = ctx.artifacts.title_text
    body = ctx.artifacts.body_text

    tmp_dir = _script("..") / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    title_file = tmp_dir / "_step9_title.txt"
    content_file = tmp_dir / "_step9_content.txt"
    title_file.write_text(title + "\n", encoding="utf-8")
    content_file.write_text(body + "\n", encoding="utf-8")

    args = [
        "--title-file", str(title_file), "--content-file", str(content_file),
        "--images", *images,
        "--account", ctx.account_id or "acc_a",
        "--reuse-existing-tab", "--preview", "--click-add-product", "--skip-form-fill",
    ]
    if product_name:
        args.extend(["--product-name", product_name])
    if product_id:
        args.extend(["--product-id", product_id])

    cp = _run_script("publish_pipeline.py", args, timeout=600)

    timeout_err = _check_timeout(cp, 600)
    if timeout_err:
        return _fail(result, code="STEP9_TIMEOUT", message=timeout_err, level=ErrorLevel.P2.value,
                     action="retry")

    if cp.returncode not in (0, 4):
        return _fail(result, code="ATTACH_PRODUCT_FAILED",
                     message=f"挂载商品失败 (exit={cp.returncode})",
                     detail={"stderr": (cp.stderr or "")[-1000:]},
                     level=ErrorLevel.P1.value, action="stop_account")

    # ── 解析 evidence_json ──
    product_mounted = False
    evidence_json: dict[str, Any] = {}
    for line in (cp.stdout or "").splitlines():
        if "PRODUCT_SELECT_EVIDENCE_JSON:" in line:
            try:
                evidence_json = json.loads(line.split("PRODUCT_SELECT_EVIDENCE_JSON:")[-1].strip())
                product_mounted = evidence_json.get("mounted", False)
            except Exception:
                pass
        elif "PRODUCT_SELECT_STATUS:" in line:
            product_mounted = "mounted" in line.lower()

    # ── 计算 match_score ──
    rule = evidence_json.get("rule", "")
    # matched 可能是 dict（有候选）或空 dict（无匹配）
    matched = evidence_json.get("matched") or {}
    match_score = _rule_to_score(rule)

    # ── 商品名匹配预期检查 ──
    target_name = (evidence_json.get("target") or {}).get("product_name", product_name or "")
    matched_name = (matched or {}).get("name", "")
    name_mismatch = False
    if target_name and matched_name:
        tn = target_name.replace(" ", "").lower()
        mn = matched_name.replace(" ", "").lower()
        if tn not in mn and mn not in tn:
            name_mismatch = True

    is_live = ctx.publish_mode == "live"

    # ── 截图 step9_product_card.png ──
    screenshots_dir = Path(ctx.paths.screenshots_dir) if ctx.paths.screenshots_dir else None
    screenshot_path = ""
    if screenshots_dir:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        ss_path = screenshots_dir / "step9_product_card.png"
        _run_script("cdp_publish.py", [
            "--account", ctx.account_id or "acc_a",
            "--reuse-existing-tab",
            "screenshot", "--output", str(ss_path),
        ], timeout=30)
        screenshot_path = str(ss_path)

    # ── 写 product_match_evidence.json ──
    match_evidence = {
        "status": evidence_json.get("status", ""),
        "mounted": product_mounted,
        "match_score": match_score,
        "rule": rule,
        "target": {"product_name": target_name, "product_id": product_id},
        "matched": matched,
        "name_mismatch": name_mismatch,
        "candidates_count": len(evidence_json.get("candidates") or []),
        "returncode": cp.returncode,
        "screenshot": screenshot_path,
        "timestamp": _now(),
    }
    evidence_dir = Path(ctx.paths.evidence_dir) if ctx.paths.evidence_dir else None
    if evidence_dir:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "product_match_evidence.json").write_text(
            json.dumps(match_evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── P0: 商品名不匹配 ──
    if name_mismatch and product_mounted:
        return _fail(result, code="WRONG_PRODUCT_MOUNTED",
                     message=f"挂载的商品名不匹配预期",
                     level=ErrorLevel.P0.value, action="stop_task",
                     detail={"expected": target_name, "selected": matched_name,
                             "rule": rule, "screenshot": screenshot_path})

    # ── manual_review: exit code 4 ──
    if cp.returncode == 4:
        result.status = StepStatus.MANUAL_REVIEW.value
        result.finished_at = _now()
        result.error = StepErrorPayload(
            code="PRODUCT_NEEDS_REVIEW", message="商品挂载需人工确认",
            detail={
                "reason": evidence_json.get("reason", ""),
                "match_score": match_score,
                "rule": rule,
                "matched": matched,
                "candidates_count": len(evidence_json.get("candidates") or []),
                "suggestion": "请检查商品搜索结果，手动选择正确商品后继续",
                "input_snapshot": {
                    "product_name": product_name,
                    "product_id": product_id,
                    "publish_mode": ctx.publish_mode,
                },
                "screenshot": screenshot_path,
            },
            error_level="", action="log_only",
        )
        return result

    # ── match_score 阈值检查 ──
    if match_score < 0.75:
        if is_live:
            return _fail(result, code="MATCH_SCORE_TOO_LOW",
                         message=f"商品匹配度过低 ({match_score})，live 模式禁止发布",
                         level=ErrorLevel.P0.value, action="stop_task",
                         detail={"match_score": match_score, "rule": rule,
                                 "expected": target_name, "screenshot": screenshot_path})
        else:
            result.status = StepStatus.MANUAL_REVIEW.value
            result.finished_at = _now()
            result.error = StepErrorPayload(
                code="MATCH_SCORE_TOO_LOW", message=f"商品匹配度过低 ({match_score})，需人工确认",
                detail={
                    "match_score": match_score,
                    "rule": rule,
                    "matched": matched,
                    "suggestion": "请检查商品搜索结果，确认当前选中的商品是否正确",
                    "input_snapshot": {"product_name": product_name, "product_id": product_id},
                    "screenshot": screenshot_path,
                },
                error_level="", action="log_only",
            )
            return result

    # ── 成功 ──
    if product_mounted:
        return _succeed(result,
                        evidence=StepEvidence(screenshot=screenshot_path),
                        artifacts={"product_mounted": True, "match_score": match_score,
                                   "rule": rule, "matched_name": matched_name})
    else:
        # 没有 mounted 但也没有错误 → manual_review
        result.status = StepStatus.MANUAL_REVIEW.value
        result.finished_at = _now()
        result.error = StepErrorPayload(
            code="PRODUCT_NOT_VERIFIED", message="商品未确认挂载",
            detail={
                "match_score": match_score,
                "rule": rule,
                "suggestion": "请检查发布页，确认商品卡片是否已显示",
                "input_snapshot": {"product_name": product_name, "product_id": product_id},
                "screenshot": screenshot_path,
            },
            error_level=ErrorLevel.P3.value, action="log_only",
        )
        return result


# ════════════════════════════════════════════════════════════
# Step 10: 预览 / 发布
# ════════════════════════════════════════════════════════════


def run_step10_preview_or_publish(ctx: PipelineContext) -> StepResult:
    """预览（preview）或正式发布（live）。"""
    result = _new_result(ctx, "step10_preview_or_publish", "预览/发布")

    is_live = ctx.publish_mode == "live"

    images = ctx.artifacts.final_images or ctx.artifacts.generated_images
    title = ctx.artifacts.title_text
    body = ctx.artifacts.body_text

    tmp_dir = _script("..") / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    title_file = tmp_dir / "_step10_title.txt"
    content_file = tmp_dir / "_step10_content.txt"
    title_file.write_text(title + "\n", encoding="utf-8")
    content_file.write_text(body + "\n", encoding="utf-8")

    screenshots_dir = Path(ctx.paths.screenshots_dir) if ctx.paths.screenshots_dir else None

    # ════════════════════════════════════════════════════════════
    # preview 模式
    # ════════════════════════════════════════════════════════════
    if not is_live:
        args = [
            "--title-file", str(title_file), "--content-file", str(content_file),
            "--images", *images,
            "--account", ctx.account_id or "acc_a",
            "--reuse-existing-tab", "--preview", "--skip-form-fill",
        ]
        cp = _run_script("publish_pipeline.py", args, timeout=600)

        timeout_err = _check_timeout(cp, 600)
        if timeout_err:
            return _fail(result, code="STEP10_TIMEOUT", message=timeout_err,
                         level=ErrorLevel.P2.value, action="retry")

        if cp.returncode != 0:
            return _fail(result, code="PUBLISH_PREVIEW_FAILED",
                         message=f"预览失败 (exit={cp.returncode})",
                         detail={"stderr": (cp.stderr or "")[-1000:]},
                         level=ErrorLevel.P1.value, action="stop_account")

        fill_ok = any("FILL_STATUS: READY_TO_PUBLISH" in line for line in (cp.stdout or "").splitlines())
        if not fill_ok:
            return _fail(result, code="PUBLISH_PREVIEW_FAILED", message="预览未就绪（FILL_STATUS 未确认）",
                         level=ErrorLevel.P1.value, action="stop_account",
                         detail={"stdout": (cp.stdout or "")[-500:]})

        # 截图 step10_preview_ready.png
        screenshot_path = ""
        if screenshots_dir:
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            ss_path = screenshots_dir / "step10_preview_ready.png"
            _run_script("cdp_publish.py", [
                "--account", ctx.account_id or "acc_a",
                "--reuse-existing-tab",
                "screenshot", "--output", str(ss_path),
            ], timeout=30)
            screenshot_path = str(ss_path)

        # ── 写 final_result.json（preview）──
        fr = {
            "publish_mode": "preview", "preview_ready": True,
            "publish_clicked": False,
            "final_image_path": images[0] if images else "",
            "title_text_exists": bool(title), "body_text_exists": bool(body),
            "product_status": "requested" if ctx.product_name or ctx.product_id else "skipped",
            "created_at": _now(), "step": "step10", "status": "success",
        }
        fr_path = Path(ctx.paths.artifacts_dir) / "final_result.json"
        fr_path.write_text(json.dumps(fr, ensure_ascii=False, indent=2), encoding="utf-8")

        return _succeed(result,
                        evidence=StepEvidence(screenshot=screenshot_path),
                        artifacts={"mode": "preview", "published": False})

    # ════════════════════════════════════════════════════════════
    # live 模式 — 10 项串行安全检查
    # ════════════════════════════════════════════════════════════

    # 读取历史步骤结果
    evidence_dir = Path(ctx.paths.evidence_dir) if ctx.paths.evidence_dir else None
    step_statuses: dict[str, str] = {}
    step_ids = [
        "step1_validate_input", "step2_search_xhs_keyword",
        "step3_select_reference_post", "step4_generate_image",
        "step5_adjust_image_color", "step6_generate_copywriting",
        "step7_open_creator_page", "step8_fill_publish_form",
        "step9_attach_product",
    ]
    if evidence_dir:
        for sid in step_ids:
            p = evidence_dir / f"{sid}.json"
            if p.is_file():
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    step_statuses[sid] = data.get("status", "")
                except Exception:
                    step_statuses[sid] = "failed"

    # 读取 bugs.json（task 级）
    bugs: list[dict] = []
    if ctx.paths.task_dir:
        bugs_path = Path(ctx.paths.task_dir) / "bugs.json"
        if bugs_path.is_file():
            try:
                raw = json.loads(bugs_path.read_text(encoding="utf-8"))
                bugs = raw if isinstance(raw, list) else raw.get("bugs", [])
            except Exception:
                pass

    step1_8_ids = step_ids[:8]
    s9_id = step_ids[8]
    s9_status = step_statuses.get(s9_id, "")

    gates: list[tuple[str, bool]] = []

    # ① publish_mode = live?
    gates.append(("publish_mode=live", is_live is True))

    # ② allow_live_publish = true?
    gates.append(("allow_live_publish", bool(ctx.input.allow_live_publish)))

    # ③ Step1-Step8 全部 success
    s1_8_ok = all(step_statuses.get(sid) == "success" for sid in step1_8_ids)
    gates.append(("Step1-8 all success", s1_8_ok))

    # ④ Step9 success 或合法 skipped
    s9_ok = s9_status in ("success", "skipped")
    gates.append(("Step9 success/skipped", s9_ok))

    # ⑤ 没有 P0/P1 错误
    has_p0_p1 = any(b.get("error_level") in ("P0", "P1") for b in bugs)
    gates.append(("No P0/P1 errors", not has_p0_p1))

    # ⑥ final_image_path 存在
    has_final = bool(images) and all(Path(p).is_file() for p in images)
    gates.append(("final_images exist", has_final))

    # ⑦ title_text 存在
    has_title = bool(title)
    gates.append(("title_text exists", has_title))

    # ⑧ body_text 存在
    has_body = bool(body)
    gates.append(("body_text exists", has_body))

    # ⑨ 商品规则通过
    gates.append(("Product rule passed", s9_status == "success"))

    # ⑩ preview 已稳定通过过（Step8 成功 = 表单填写已验证）
    s8_ok = step_statuses.get("step8_fill_publish_form") == "success"
    gates.append(("Preview verified (Step8 ok)", s8_ok))

    # ── 执行门禁检查 ──
    failed_gates = [name for name, passed in gates if not passed]
    if failed_gates:
        return _fail(result, code="LIVE_NOT_ALLOWED",
                     message=f"Live 安全门禁未通过: {'; '.join(failed_gates)}",
                     level=ErrorLevel.P0.value, action="stop_task",
                     detail={
                         "failed_gates": failed_gates,
                         "gate_results": [
                             {"name": n, "expected": True, "actual": p}
                             for n, p in gates
                         ],
                         "step_statuses": step_statuses,
                         "bug_count": len(bugs),
                     })

    # ── 所有门禁通过 → 执行 live 发布 ──
    live_args = [
        "--title-file", str(title_file), "--content-file", str(content_file),
        "--images", *images,
        "--account", ctx.account_id or "acc_a",
        "--reuse-existing-tab", "--skip-form-fill",
    ]
    cp = _run_script("publish_pipeline.py", live_args, timeout=600)

    timeout_err = _check_timeout(cp, 600)
    if timeout_err:
        return _fail(result, code="STEP10_TIMEOUT", message=timeout_err,
                     level=ErrorLevel.P2.value, action="retry")

    if cp.returncode != 0:
        return _fail(result, code="PUBLISH_FAILED",
                     message=f"发布失败 (exit={cp.returncode})",
                     detail={"stderr": (cp.stderr or "")[-1000:]},
                     level=ErrorLevel.P1.value, action="stop_account")

    published = any("PUBLISH_STATUS: PUBLISHED" in line for line in (cp.stdout or "").splitlines())

    if not published:
        return _fail(result, code="PUBLISH_NOT_CONFIRMED", message="发布完成但未收到确认信号",
                     level=ErrorLevel.P1.value, action="stop_account",
                     detail={"stdout": (cp.stdout or "")[-500:]})

    # 解析 post_url
    post_url = ""
    for line in (cp.stdout or "").splitlines():
        if "Note published at:" in line:
            post_url = line.split("Note published at:", 1)[-1].strip()
            break

    # 截图 published_success.png
    screenshot_path = ""
    if screenshots_dir:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        ss_path = screenshots_dir / "published_success.png"
        _run_script("cdp_publish.py", [
            "--account", ctx.account_id or "acc_a",
            "--reuse-existing-tab",
            "screenshot", "--output", str(ss_path),
        ], timeout=30)
        screenshot_path = str(ss_path)

    return _succeed(result,
                    evidence=StepEvidence(screenshot=screenshot_path, url=post_url),
                    artifacts={"mode": "live", "published": True, "post_url": post_url})
