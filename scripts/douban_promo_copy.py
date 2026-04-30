"""
Generate Xiaohongshu title/content files via your HTTP promo API (e.g. Douban backend).

Writes UTF-8 files:
  - title.txt
  - content.txt — body text; LAST non-empty line MUST be topic tokens:
      "#标签1 #标签2 ..."
    This matches publish_pipeline.py topic extraction.

Environment (recommended — avoids secrets on CLI):

  Custom HTTP promo API:
  DOUBAN_PROMO_API_URL   POST endpoint (required for --provider http unless --dry-run)
  DOUBAN_PROMO_API_KEY   Optional Bearer token

  火山方舟 · 豆包（OpenAI-compatible Chat Completions，--provider ark）:
  ARK_API_KEY            Required for ark（勿提交到仓库）
  ARK_MODEL              推理接入点 ID（控制台复制，必填）
  ARK_BASE_URL           默认 https://ark.cn-beijing.volces.com/api/v3
  ARK_BODY_MIN_CHARS     可选；正文 JSON「body」最短字数（默认 95，与正文上限成套使用）
  ARK_BODY_MAX_CHARS     可选；正文最长字数（默认 100，含标点换行）；超出会在写入 content 前截断
  ARK_TITLE_MAX_CHARS    可选；豆包 title 最长字符数（默认 18）；与 CLI --ark-title-max 对照

  Common:
  DOUBAN_PROMO_TIMEOUT   Seconds (default 120)
  DOUBAN_PROMO_VERIFY_SSL default "1"; set "0" to disable TLS verify (dev only)

API contract (default request body shape):
  {
    "intent": "xhs_promo",
    "brief": "...",
    "seed_keyword": "... or null",
    "images": [
      {"filename": "a.jpg", "mime_type": "image/jpeg", "data_base64": "..."}
    ]
  }

Expected JSON response (flexible keys):
  title / subject / xhs_title
  body / content / text / copy / article  — main paragraph(s), WITHOUT the tag line
  tags — array of strings ("茶" or "#茶") OR one string "#a #b"

If the API returns body that already ends with #tags, tags can be omitted.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from env_local_loader import load_env_local

# scripts on path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
load_env_local(Path(SCRIPT_DIR).parent)

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import requests
except ImportError:
    print("Error: requests is required. pip install requests", file=sys.stderr)
    sys.exit(2)


def _display_width(title: str) -> int:
    """Heuristic title width: CJK counts 2, ASCII 1 (aligns with common XHS hints)."""
    w = 0
    for ch in title:
        if ord(ch) > 127:
            w += 2
        else:
            w += 1
    return w


def _clamp_title_char_length(title: str, max_chars: int) -> tuple[str, bool]:
    """Single-line title; truncate to max_chars (Python str lengths) if needed."""
    t = (title or "").replace("\n", " ").strip()
    if max_chars <= 0:
        return t, False
    if len(t) <= max_chars:
        return t, False
    return t[:max_chars], True


def _clamp_body_char_length(body: str, max_chars: int) -> tuple[str, bool]:
    """Main note body before topic line; truncate to max_chars (len 含标点与换行) if needed."""
    if max_chars <= 0:
        return (body or "").strip(), False
    b = (body or "").strip()
    if len(b) <= max_chars:
        return b, False
    return b[:max_chars], True


def _first_nonempty(d: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _normalize_tag_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        out = []
        for x in raw:
            if isinstance(x, str) and x.strip():
                s = x.strip()
                out.append(s if s.startswith("#") else f"#{s}")
        return out
    if isinstance(raw, str):
        parts = []
        for tok in raw.replace(",", " ").split():
            tok = tok.strip()
            if not tok:
                continue
            parts.append(tok if tok.startswith("#") else f"#{tok}")
        return parts
    return []


def _normalize_body_text(body: str) -> str:
    text = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n")]
    # Collapse excessive blank lines while keeping paragraph readability.
    out: list[str] = []
    blank = False
    for ln in lines:
        if not ln:
            if not blank and out:
                out.append("")
            blank = True
            continue
        out.append(ln)
        blank = False
    return "\n".join(out).strip()


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        key = x.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(x.strip())
    return out


def _parse_api_response(payload: dict[str, Any]) -> tuple[str, str, list[str]]:
    title = _first_nonempty(
        payload,
        ("title", "subject", "xhs_title", "headline"),
    )
    if not title:
        raise ValueError("API JSON must include a title field (title/subject/xhs_title).")

    body = _first_nonempty(
        payload,
        ("body", "content", "text", "copy", "article", "main"),
    )
    if body is None:
        body = ""

    tags = _normalize_tag_list(payload.get("tags"))
    if not tags:
        tags = _normalize_tag_list(payload.get("topics"))
    if not tags:
        tags = _normalize_tag_list(payload.get("hashtags"))

    # If body already has a valid last-line tag pattern, extract like publish_pipeline
    from publish_pipeline import _extract_topic_tags_from_last_line

    main, from_line = _extract_topic_tags_from_last_line(body)
    if from_line:
        return title, main, from_line

    title = title.replace("\n", " ").strip()
    body = _normalize_body_text(body)
    tags = _dedupe_keep_order(_normalize_tag_list(tags))[:10]
    return title, body, tags


def _build_content_file(body: str, tags: list[str]) -> str:
    body = body.strip()
    if not tags:
        if not body:
            raise ValueError("Empty body and no tags from API.")
        return body

    tag_line = " ".join(tags)
    if not body:
        return tag_line
    return f"{body}\n\n{tag_line}"


def _encode_images(paths: list[str]) -> list[dict[str, str]]:
    out = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {p}")
        mime, _ = mimetypes.guess_type(str(path))
        if not mime:
            mime = "application/octet-stream"
        b64 = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        out.append(
            {
                "filename": path.name,
                "mime_type": mime,
                "data_base64": b64,
            }
        )
    return out


def _ark_data_url_for_path(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Image not found: {path}")
    mime, _ = mimetypes.guess_type(str(p))
    if not mime or not mime.startswith("image/"):
        mime = "image/jpeg"
    b64 = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _repair_json_literal_controls(raw: str) -> str:
    """
    Escape raw ASCII control characters inside JSON double-quoted strings so that
    json.loads accepts output from models that insert literal newlines in "body".
    """
    out: list[str] = []
    i = 0
    in_string = False
    escape = False
    while i < len(raw):
        c = raw[i]
        if escape:
            out.append(c)
            escape = False
            i += 1
            continue
        if c == "\\":
            out.append(c)
            escape = True
            i += 1
            continue
        if c == '"':
            in_string = not in_string
            out.append(c)
            i += 1
            continue
        if in_string and ord(c) < 32:
            if c == "\n":
                out.append("\\n")
            elif c == "\r":
                out.append("\\r")
            elif c == "\t":
                out.append("\\t")
            else:
                out.append(f"\\u{ord(c):04x}")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _loads_json_dict_candidates(s: str) -> dict[str, Any] | None:
    for candidate in (s, _repair_json_literal_controls(s)):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _extract_json_object_from_text(text: str) -> dict[str, Any]:
    """Parse model output: raw JSON or ```json ...``` or first {...} block."""
    s = text.strip()
    if not s:
        raise ValueError("Model returned empty content.")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    obj = _loads_json_dict_candidates(s)
    if obj is not None:
        return obj
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        chunk = s[start : end + 1]
        obj = _loads_json_dict_candidates(chunk)
        if obj is not None:
            return obj
    raise ValueError(f"Could not parse JSON from model output: {text[:800]}")


def _warn_ark_body_length(body: str, lo: int, hi: int) -> None:
    """body 为正文字符串（不含最后一行话题）。按 Python len 统计字符数（中英文均 1）。"""
    n = len((body or "").strip())
    if n < lo or n > hi:
        print(
            f"[douban_promo] Warning: 「body」长度 {n} 字，目标区间为 {lo}～{hi} 字；"
            "可重试或在提示词側强调字数。",
            file=sys.stderr,
        )


def call_ark_doubao(
    *,
    base_url: str,
    api_key: str,
    model: str,
    brief: str,
    seed_keyword: str | None,
    image_paths: list[str],
    timeout: float,
    verify_ssl: bool,
    body_min_chars: int = 95,
    body_max_chars: int = 100,
    title_max_chars: int = 18,
    style_hint: str = "小红书实拍种草",
    tone_hint: str = "像朋友聊天、好读、利他、不硬广",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call Ark Chat Completions; return (normalized_payload, raw_chat_json)."""
    base = base_url.rstrip("/")
    chat_url = f"{base}/chat/completions"

    user_parts: list[dict[str, Any]] = []
    instructions = (
        "你是熟悉小红书内容生态的爆款笔记策划：懂「信息流标题 + 可滑读正文 + 可搜话题」的组合。"
        "语气要像真实用户发笔记，不是电商详情页、不是公告通稿。\n\n"
        "小红书风格要点：\n"
        "· 标题：信息密度高、能点进「详情」——用具体场景/小痛点/对比/结果感之一，让人一眼知道「和我有什么关系」；"
        "拒绝空泛口号、拒绝标题党承诺。\n"
        "· 正文：首段用一句话抓共鸣或场景；中间写体验细节（五感、过程、对比、小插曲）；"
        "结尾给「适合谁/怎么选/小提醒」之一，利他收束；句短、段短，适合手机屏阅读；"
        "全文最多用 1～2 个常见表情点缀即可，不要 emoji 刷屏。\n"
        "· 话题：像用户会主动搜的词，覆盖场景/人群/品类，少而准，不要硬堆品牌或空词。\n\n"
        "【商品/活动说明】\n"
        f"{brief}\n"
        f"\n【文风】{style_hint}"
        f"\n【语气】{tone_hint}\n"
    )
    if seed_keyword:
        instructions += f"\n【希望侧重的话题/搜索方向】{seed_keyword}\n"

    lo, hi = int(body_min_chars), int(body_max_chars)
    if lo < 10:
        lo = 10
    if hi < lo:
        hi = lo + 1

    tc = max(4, min(120, int(title_max_chars)))

    if lo >= hi:
        body_len_rule = (
            f"JSON 字段「body」总长度必须恰好 {hi} 个字（中文、英文数字、标点、空格及换行均各计 1 个字符）；"
            "不得在 body 末尾另外拼话题。"
        )
    else:
        body_len_rule = (
            f"JSON 字段「body」总长须在 {lo}～{hi} 个字之间（按字符计，含标点与换行）；"
            f"严禁超过 {hi} 个字；不得在 body 末尾另外拼话题。"
        )

    instructions += (
        "\n请严格只输出 JSON（不要 markdown 代码围栏、不要解释），格式：\n"
        '{"title":"标题","body":"正文…","tags":["话题词1","话题词2"]}\n\n'
        "硬性要求（符合小红书社区表达与合规）：\n"
        f"1) title：最多{tc}个字（含标点与空格），单行；"
        "写法偏「爆款信息流」：场景化/结果感/小反转/小痛点其一，含明确利益点；"
        "避免夸张、避免医疗与功效话术。\n"
        "2) body：2～3段，段与段之间在字符串里用换行分隔；必须包含——使用场景、体验细节、适合人群或购买建议中的至少两样；"
        f"读起来要像小红书原生笔记而不是广告脚本。{body_len_rule}\n"
        "3) tags：6～8 个中文短标签，不要带 #、不要重复；"
        "优先「场景词+品类词+人群词」可被搜索的搭配，少用「好物」「种草」这类空泛占位。\n"
        "4) 禁止：承诺医疗疗效、绝对化与极限用语（最/第一/顶配/闭眼入等无底依据词）、贬低竞品、虚假体验、与给定信息无关编造。\n"
        "5) 严禁输出 JSON 之外的任何字符。"
    )
    user_parts.append({"type": "text", "text": instructions})
    for img_path in image_paths:
        user_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": _ark_data_url_for_path(img_path)},
            }
        )

    req_body: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你只输出合法 JSON（键 title、body、tags），文风为小红书站内爆款笔记常见写法。"
                    f" title 至多{tc}个字符。"
                ),
            },
            {"role": "user", "content": user_parts},
        ],
        "temperature": 0.55,
    }

    resp = requests.post(
        chat_url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        data=json.dumps(req_body, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
        verify=verify_ssl,
    )
    raw_chat: dict[str, Any]
    try:
        raw_chat = resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Ark API non-JSON: {resp.text[:800]}") from e

    if resp.status_code >= 400:
        err = raw_chat.get("error", {})
        raise RuntimeError(
            f"Ark HTTP {resp.status_code}: {err or raw_chat}"
        )

    choices = raw_chat.get("choices") or []
    if not choices:
        raise RuntimeError(f"Ark returned no choices: {raw_chat}")

    raw_content = (choices[0].get("message") or {}).get("content")
    if isinstance(raw_content, list):
        msg = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in raw_content
        )
    else:
        msg = raw_content or ""
    if not isinstance(msg, str):
        msg = str(msg)
    parsed = _extract_json_object_from_text(msg)

    normalized = {
        "title": parsed.get("title"),
        "body": parsed.get("body") if parsed.get("body") is not None else parsed.get("content"),
        "tags": parsed.get("tags"),
        "topics": parsed.get("topics"),
    }

    return normalized, raw_chat


def call_promo_api(
    *,
    api_url: str,
    api_key: str | None,
    brief: str,
    seed_keyword: str | None,
    image_paths: list[str],
    timeout: float,
    verify_ssl: bool,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "intent": "xhs_promo",
        "brief": brief,
        "seed_keyword": seed_keyword,
        "images": _encode_images(image_paths) if image_paths else [],
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)

    resp = requests.post(
        api_url,
        headers=headers,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
        verify=verify_ssl,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"API HTTP {resp.status_code}: {resp.text[:2000]}"
        )
    try:
        return resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(f"API did not return JSON: {e}: {resp.text[:500]}") from e


def write_promo_files(
    *,
    out_dir: Path,
    title: str,
    content_full: str,
    raw_response_path: Path | None = None,
    api_payload: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    title_path = out_dir / "title.txt"
    content_path = out_dir / "content.txt"

    title_path.write_text(title.strip() + "\n", encoding="utf-8")
    content_path.write_text(content_full.strip() + "\n", encoding="utf-8")

    if raw_response_path and api_payload is not None:
        raw_response_path.write_text(
            json.dumps(api_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return title_path, content_path


def generate_promo_to_dir(
    *,
    brief: str,
    seed_keyword: str | None,
    image_paths: list[str],
    out_dir: Path,
    provider: str,
    api_url: str | None,
    api_key: str | None,
    ark_base_url: str | None,
    ark_api_key: str | None,
    ark_model: str | None,
    ark_body_min_chars: int = 95,
    ark_body_max_chars: int = 100,
    ark_style_hint: str = "小红书实拍种草",
    ark_tone_hint: str = "像朋友聊天、好读、利他、不硬广",
    ark_title_max_chars: int = 18,
    timeout: float,
    verify_ssl: bool,
    dry_run: bool,
    extra_headers: dict[str, str] | None,
    dump_raw: bool,
) -> tuple[Path, Path]:
    if dry_run:
        title = "【预览】替换为真实标题"
        tags = ["#示例话题", "#替换为你的标签"]
        body = (
            "这里是由 --dry-run 生成的占位正文。请配置 DOUBAN_PROMO_API_URL 后重试。\n"
            "下面最后一行将用于小红书「话题」选择："
        )
        content_full = _build_content_file(body, tags)
        raw_path = out_dir / "api_response.json" if dump_raw else None
        payload: dict[str, Any] | None = {"dry_run": True} if dump_raw else None
        return write_promo_files(
            out_dir=out_dir,
            title=title,
            content_full=content_full,
            raw_response_path=raw_path,
            api_payload=payload,
        )

    if provider == "ark":
        akey = (ark_api_key or os.environ.get("ARK_API_KEY") or "").strip()
        amodel = (ark_model or os.environ.get("ARK_MODEL") or "").strip()
        abase = (ark_base_url or os.environ.get("ARK_BASE_URL") or "").strip()
        if not abase:
            abase = "https://ark.cn-beijing.volces.com/api/v3"
        if not akey:
            raise SystemExit(
                "For --provider ark, set ARK_API_KEY (or pass --ark-api-key) "
                "in the environment; do not commit keys to the repo."
            )
        if not amodel:
            raise SystemExit(
                "For --provider ark, set ARK_MODEL to your console 推理接入点 ID."
            )
        data, raw_chat = call_ark_doubao(
            base_url=abase,
            api_key=akey,
            model=amodel,
            brief=brief,
            seed_keyword=seed_keyword,
            image_paths=list(image_paths),
            timeout=timeout,
            verify_ssl=verify_ssl,
            body_min_chars=ark_body_min_chars,
            body_max_chars=ark_body_max_chars,
            title_max_chars=ark_title_max_chars,
            style_hint=ark_style_hint,
            tone_hint=ark_tone_hint,
        )
        save_raw: dict[str, Any] = {
            "provider": "ark",
            "chat_completions": raw_chat,
            "parsed": data,
        }
    else:
        if not api_url:
            raise SystemExit(
                "Set DOUBAN_PROMO_API_URL or pass --api-url; or use --provider ark; "
                "or use --dry-run."
            )
        data = call_promo_api(
            api_url=api_url,
            api_key=api_key,
            brief=brief,
            seed_keyword=seed_keyword,
            image_paths=image_paths,
            timeout=timeout,
            verify_ssl=verify_ssl,
            extra_headers=extra_headers,
        )
        save_raw = data

    title, body, tags = _parse_api_response(data)
    if provider == "ark":
        title, title_truncated = _clamp_title_char_length(
            title,
            ark_title_max_chars,
        )
        body, body_truncated = _clamp_body_char_length(
            body,
            ark_body_max_chars,
        )
        if title_truncated:
            print(
                f"[douban_promo] Warning: ark 「title」已截断至 {ark_title_max_chars} "
                "个字（可调 ARK_TITLE_MAX_CHARS / --ark-title-max）。",
                file=sys.stderr,
            )
        if body_truncated:
            print(
                f"[douban_promo] Warning: ark 「body」已截断至 {ark_body_max_chars} "
                "个字（含标点换行）（可调 ARK_BODY_MAX_CHARS / --ark-body-max）。",
                file=sys.stderr,
            )
        _warn_ark_body_length(body, ark_body_min_chars, ark_body_max_chars)
    elif _display_width(title) > 38:
        print(
            f"[douban_promo] Warning: title display width {_display_width(title)} "
            f"may exceed common XHS title limit (~38); consider shortening.",
            file=sys.stderr,
        )

    content_full = _build_content_file(body, tags)
    raw_path = (out_dir / "api_response.json") if dump_raw else None
    return write_promo_files(
        out_dir=out_dir,
        title=title,
        content_full=content_full,
        raw_response_path=raw_path,
        api_payload=save_raw if dump_raw else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate title.txt / content.txt via your promo API (e.g. Douban)."
    )
    parser.add_argument("--brief", default=None, help="Short brief for the API")
    parser.add_argument(
        "--brief-file",
        default=None,
        help="UTF-8 file with campaign / product context for the API",
    )
    parser.add_argument(
        "--seed-keyword",
        default=None,
        help="Optional keyword to align copy with picset/XHS strategy",
    )
    parser.add_argument(
        "--images",
        nargs="*",
        default=[],
        help="Local image paths sent to API as base64 (0+ files)",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory (default: Desktop / XHS_promo_<timestamp>)",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("DOUBAN_PROMO_API_URL"),
        help="Override DOUBAN_PROMO_API_URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DOUBAN_PROMO_API_KEY"),
        help="Override DOUBAN_PROMO_API_KEY (Bearer)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("DOUBAN_PROMO_TIMEOUT", "120")),
        help="HTTP timeout seconds",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (sets DOUBAN_PROMO_VERIFY_SSL=0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write sample files without calling the network",
    )
    parser.add_argument(
        "--dump-raw-response",
        action="store_true",
        help="Save api_response.json alongside title/content",
    )
    parser.add_argument(
        "--extra-headers-json",
        default=None,
        help='JSON object of extra HTTP headers, e.g. \'{"X-Custom":"v"}\'',
    )
    parser.add_argument(
        "--provider",
        choices=("http", "ark"),
        default=os.environ.get("DOUBAN_PROMO_PROVIDER", "http"),
        help=(
            'http = custom DOUBAN_PROMO_API_URL; ark = ByteDance Volcengine Ark / 豆包 '
            "(needs ARK_API_KEY + ARK_MODEL)"
        ),
    )
    parser.add_argument(
        "--ark-base-url",
        default=os.environ.get("ARK_BASE_URL"),
        help="Default: ARK_BASE_URL or https://ark.cn-beijing.volces.com/api/v3",
    )
    parser.add_argument(
        "--ark-api-key",
        default=os.environ.get("ARK_API_KEY"),
        help="Prefer env ARK_API_KEY only; CLI exposes key in shell history",
    )
    parser.add_argument(
        "--ark-model",
        default=os.environ.get("ARK_MODEL"),
        help="Ark console 推理接入点 ID",
    )
    parser.add_argument(
        "--ark-body-min",
        type=int,
        default=int(os.environ.get("ARK_BODY_MIN_CHARS", "95")),
        help="豆包 JSON 中 body 最短字数目标（默认 95；可与 max 同属「约 100 字」区间）",
    )
    parser.add_argument(
        "--ark-body-max",
        type=int,
        default=int(os.environ.get("ARK_BODY_MAX_CHARS", "100")),
        help="豆包 JSON 中 body 字数上限（默认 100，含标点换行；超出写入前截断）（ARK_BODY_MAX_CHARS）",
    )
    parser.add_argument(
        "--ark-title-max",
        type=int,
        default=int(os.environ.get("ARK_TITLE_MAX_CHARS", "18")),
        help="豆包 title 最长字数（默认 18；可用 ARK_TITLE_MAX_CHARS）",
    )
    parser.add_argument(
        "--ark-style-hint",
        default=os.environ.get("ARK_STYLE_HINT", "小红书实拍种草"),
        help="豆包文风提示词，如：成分党测评、送礼场景、通勤党",
    )
    parser.add_argument(
        "--ark-tone-hint",
        default=os.environ.get("ARK_TONE_HINT", "像朋友聊天、好读、利他、不硬广"),
        help="豆包语气提示词，如：真诚分享、轻吐槽、学生党口语",
    )

    args = parser.parse_args()

    if args.brief_file:
        brief = Path(args.brief_file).read_text(encoding="utf-8").strip()
    elif args.brief:
        brief = args.brief.strip()
    elif args.dry_run:
        brief = "dry-run placeholder"
    else:
        print("Error: provide --brief or --brief-file.", file=sys.stderr)
        sys.exit(2)

    if not brief.strip() and not args.dry_run:
        print("Error: brief text is empty.", file=sys.stderr)
        sys.exit(2)

    verify_ssl = os.environ.get("DOUBAN_PROMO_VERIFY_SSL", "1") not in (
        "0",
        "false",
        "False",
    )
    if args.insecure:
        verify_ssl = False

    extra_headers: dict[str, str] | None = None
    if args.extra_headers_json:
        extra_headers = json.loads(args.extra_headers_json)

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        desktop = Path.home() / "Desktop"
        if not desktop.is_dir():
            desktop = Path.home()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = desktop / f"XHS_promo_{stamp}"

    b_min = max(10, int(args.ark_body_min))
    b_max = max(b_min, int(args.ark_body_max))
    if b_max > 2000:
        print(
            f"[douban_promo] Warning: --ark-body-max {b_max} is very large; "
            "clamping to 2000.",
            file=sys.stderr,
        )
        b_max = min(b_max, 2000)

    title_max = max(1, min(512, int(args.ark_title_max)))

    t_path, c_path = generate_promo_to_dir(
        brief=brief,
        seed_keyword=args.seed_keyword,
        image_paths=list(args.images),
        out_dir=out_dir,
        provider=args.provider,
        api_url=args.api_url.strip() if args.api_url else None,
        api_key=args.api_key.strip() if args.api_key else None,
        ark_base_url=args.ark_base_url.strip() if args.ark_base_url else None,
        ark_api_key=args.ark_api_key.strip() if args.ark_api_key else None,
        ark_model=args.ark_model.strip() if args.ark_model else None,
        ark_body_min_chars=b_min,
        ark_body_max_chars=b_max,
        ark_style_hint=args.ark_style_hint,
        ark_tone_hint=args.ark_tone_hint,
        ark_title_max_chars=title_max,
        timeout=args.timeout,
        verify_ssl=verify_ssl,
        dry_run=args.dry_run,
        extra_headers=extra_headers,
        dump_raw=args.dump_raw_response,
    )

    print(f"[douban_promo] Wrote title:  {t_path}")
    print(f"[douban_promo] Wrote body:   {c_path}")
    print(
        "[douban_promo] Topic line is the last line of content.txt "
        "(#tag #tag …) for publish_pipeline."
    )
    # Machine-readable path for promo_publish_one_shot.py
    print(f"[douban_promo] OUT_DIR={out_dir.resolve()}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
