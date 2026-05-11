"""
Step-3 helper:
Search XHS feeds, rank covers by product-image similarity, download top refs.

This script avoids PowerShell redirection encoding pitfalls and avoids
console-encoding crashes from non-GBK characters by forcing UTF-8 output.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests
from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent


if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _run_search(args: argparse.Namespace) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "cdp_publish.py"),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--account",
        args.account,
        "--reuse-existing-tab",
        "search-feeds",
        "--keyword",
        args.keyword,
        "--sort-by",
        args.sort_by,
        "--note-type",
        args.note_type,
        "--publish-time",
        args.publish_time,
    ]
    cp = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if cp.returncode != 0:
        err_tail = (cp.stderr or "")[-2000:]
        # UI filters can be unavailable on some page variants; fallback to keyword-only search.
        if "filter_button_not_found" in err_tail:
            fallback_cmd = [
                sys.executable,
                str(SCRIPT_DIR / "cdp_publish.py"),
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--account",
                args.account,
                "--reuse-existing-tab",
                "search-feeds",
                "--keyword",
                args.keyword,
            ]
            print("[step3] filter_button_not_found, fallback to keyword-only search.", file=sys.stderr)
            cp = subprocess.run(
                fallback_cmd,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        if cp.returncode != 0:
            raise SystemExit(
                f"search-feeds failed (exit={cp.returncode}). stderr tail:\n{(cp.stderr or '')[-1200:]}"
            )

    text = cp.stdout or ""
    marker = "SEARCH_FEEDS_RESULT:"
    idx = text.find(marker)
    if idx < 0:
        raise SystemExit("SEARCH_FEEDS_RESULT marker not found in search output.")
    payload_raw = text[idx + len(marker):].strip()
    try:
        return json.loads(payload_raw)
    except Exception as exc:
        snippet = payload_raw[:1000]
        raise SystemExit(f"failed to parse search JSON: {exc}\npreview:\n{snippet}") from exc


def _img_feature(pil_img: Image.Image) -> dict[str, Any]:
    img = pil_img.convert("RGB").resize((240, 240))
    hsv = img.convert("HSV")
    h = list(hsv.getdata(0))
    s = list(hsv.getdata(1))
    v = list(hsv.getdata(2))

    bins = 24
    hist = [0.0] * bins
    for x in h:
        hist[min(bins - 1, int(x / 256 * bins))] += 1.0
    n = float(len(h) or 1)
    hist = [x / n for x in hist]

    s_mean = sum(s) / n / 255.0
    v_mean = sum(v) / n / 255.0

    r, g, b = img.split()
    r_mean = sum(r.getdata()) / n / 255.0
    g_mean = sum(g.getdata()) / n / 255.0
    b_mean = sum(b.getdata()) / n / 255.0

    return {
        "hist": hist,
        "s_mean": s_mean,
        "v_mean": v_mean,
        "r_mean": r_mean,
        "g_mean": g_mean,
        "b_mean": b_mean,
    }


def _cos_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _pick_product_image(path_arg: str | None) -> Path:
    if path_arg:
        p = Path(path_arg).expanduser().resolve()
        if p.is_file():
            return p
    shots = Path.home() / "Pictures" / "Screenshots"
    pattern_matches = sorted(shots.glob("*091505*.png"))
    if pattern_matches:
        return pattern_matches[-1]
    raise SystemExit("product image not found. pass --product-image explicitly.")


def _download_and_rank(
    feeds: list[dict[str, Any]],
    *,
    product_image: Path,
    top_k: int,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prod_feat = _img_feature(Image.open(product_image))

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.xiaohongshu.com/",
        }
    )

    records: list[dict[str, Any]] = []
    for idx, feed in enumerate(feeds):
        note = feed.get("noteCard") or {}
        title = str(note.get("displayTitle") or "")
        cover = note.get("cover") or {}
        url = str(cover.get("urlDefault") or cover.get("urlPre") or "").strip()
        if not url:
            continue
        url = re.sub(r"^http://", "https://", url)
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            feat = _img_feature(Image.open(io.BytesIO(resp.content)))
            hue_sim = _cos_sim(prod_feat["hist"], feat["hist"])
            sat_sim = 1.0 - min(1.0, abs(prod_feat["s_mean"] - feat["s_mean"]))
            val_sim = 1.0 - min(1.0, abs(prod_feat["v_mean"] - feat["v_mean"]))
            green_bonus = max(0.0, feat["g_mean"] - max(feat["r_mean"], feat["b_mean"]))
            txt_bonus = 0.0
            if "茶" in title:
                txt_bonus += 0.04
            if "绿茶" in title or "龙井" in title:
                txt_bonus += 0.06
            score = 0.62 * hue_sim + 0.16 * sat_sim + 0.12 * val_sim + 0.10 * green_bonus + txt_bonus
            records.append(
                {
                    "index": idx,
                    "id": feed.get("id"),
                    "title": title,
                    "url": url,
                    "score": float(score),
                    "content": resp.content,
                    "content_type": str(resp.headers.get("Content-Type", "")),
                }
            )
        except Exception as exc:
            records.append(
                {
                    "index": idx,
                    "id": feed.get("id"),
                    "title": title,
                    "url": url,
                    "error": str(exc),
                }
            )

    success = [r for r in records if "content" in r]
    success.sort(key=lambda x: x["score"], reverse=True)
    selected = success[: max(1, int(top_k))]

    saved: list[dict[str, Any]] = []
    for rank, row in enumerate(selected, start=1):
        ext = ".webp"
        ct = row.get("content_type", "").lower()
        if "jpeg" in ct or "jpg" in ct:
            ext = ".jpg"
        elif "png" in ct:
            ext = ".png"
        file_id = re.sub(r"[^0-9A-Za-z_-]+", "_", str(row.get("id") or f"idx_{row['index']}"))[:40]
        out_path = output_dir / f"{rank:02d}_{file_id}{ext}"
        out_path.write_bytes(row["content"])
        saved.append(
            {
                "rank": rank,
                "path": str(out_path),
                "id": row.get("id"),
                "title": row.get("title"),
                "score": round(float(row.get("score") or 0.0), 6),
                "url": row.get("url"),
            }
        )

    return {
        "product_image": str(product_image),
        "download_success": len(success),
        "selected_count": len(saved),
        "selected": saved,
        "failed": [
            {
                "id": r.get("id"),
                "title": r.get("title"),
                "url": r.get("url"),
                "error": r.get("error"),
            }
            for r in records
            if r.get("error")
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Step3: select and download matching XHS cover refs.")
    parser.add_argument("--keyword", default="茶叶")
    parser.add_argument("--sort-by", default="综合")
    parser.add_argument("--note-type", default="图文")
    parser.add_argument("--publish-time", default="一周内")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--account", default="acc_a")
    parser.add_argument("--product-image", default=None)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument(
        "--output-dir",
        default=str(REPO_ROOT / "tmp" / "step3_downloaded"),
    )
    parser.add_argument("--summary-json", default=None,
                        help="写入摘要 JSON 到此路径（供编排器读取）")
    args = parser.parse_args()

    search_obj = _run_search(args)
    feeds = search_obj.get("feeds") or []

    out_dir = Path(args.output_dir).resolve()
    product_image = _pick_product_image(args.product_image)
    result = _download_and_rank(
        feeds,
        product_image=product_image,
        top_k=max(1, int(args.top_k)),
        output_dir=out_dir,
    )
    result["keyword"] = args.keyword
    result["total_feeds"] = len(feeds)

    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "manifest": str(manifest),
        "selected_count": result.get("selected_count"),
        "download_success": result.get("download_success"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.summary_json:
        selected_items = result.get("selected") or []
        summary_out = {
            "keyword": args.keyword,
            "product_image": result.get("product_image", ""),
            "output_dir": str(out_dir),
            "selected": selected_items,
            "local_paths": [s["path"] for s in selected_items if s.get("path")],
            "downloaded_paths": [s["path"] for s in selected_items if s.get("path")],
            "reference_image_path": selected_items[0]["path"] if selected_items else "",
            "selected_count": result.get("selected_count", 0),
            "download_success": result.get("download_success", 0),
            "total_feeds": result.get("total_feeds", 0),
        }
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary_out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
