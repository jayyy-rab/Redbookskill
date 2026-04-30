"""
One-shot: douban_promo_copy (API) → publish_pipeline with the same local images.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from env_local_loader import load_env_local

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
load_env_local(REPO_ROOT)

_OUT_DIR_RE = re.compile(r"^\[douban_promo\] OUT_DIR=(.+)$")

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _parse_out_dir(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        m = _OUT_DIR_RE.match(line.strip())
        if m:
            return Path(m.group(1).strip())
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate title/content (custom HTTP or 豆包/Ark), then publish "
            "with publish_pipeline.py using the same local images."
        )
    )

    parser.add_argument("--brief", default=None)
    parser.add_argument("--brief-file", default=None)
    parser.add_argument("--seed-keyword", default=None)
    parser.add_argument(
        "--images",
        nargs="+",
        required=True,
        help="Local images for BOTH API (base64) and XHS upload",
    )
    parser.add_argument(
        "--promo-out-dir",
        default=None,
        help="If set, pass --out-dir to douban_promo_copy and skip parsing OUT_DIR",
    )
    parser.add_argument("--api-url", default=os.environ.get("DOUBAN_PROMO_API_URL"))
    parser.add_argument("--api-key", default=os.environ.get("DOUBAN_PROMO_API_KEY"))
    parser.add_argument(
        "--promo-timeout",
        type=float,
        default=float(os.environ.get("DOUBAN_PROMO_TIMEOUT", "120")),
    )
    parser.add_argument("--promo-insecure", action="store_true")
    parser.add_argument(
        "--dry-run-promo",
        action="store_true",
        help="Placeholder copy from douban_promo_copy (--dry-run)",
    )
    parser.add_argument(
        "--dump-raw-response",
        action="store_true",
        help="Save api_response.json from promo step",
    )
    parser.add_argument(
        "--provider",
        choices=("http", "ark"),
        default=os.environ.get("DOUBAN_PROMO_PROVIDER", "http"),
        help="ark = 豆包 via ARK_API_KEY + ARK_MODEL",
    )
    parser.add_argument("--ark-base-url", default=os.environ.get("ARK_BASE_URL"))
    parser.add_argument("--ark-api-key", default=os.environ.get("ARK_API_KEY"))
    parser.add_argument("--ark-model", default=os.environ.get("ARK_MODEL"))
    parser.add_argument(
        "--ark-body-min",
        type=int,
        default=int(os.environ.get("ARK_BODY_MIN_CHARS", "95")),
    )
    parser.add_argument(
        "--ark-body-max",
        type=int,
        default=int(os.environ.get("ARK_BODY_MAX_CHARS", "100")),
    )
    parser.add_argument(
        "--ark-title-max",
        type=int,
        default=int(os.environ.get("ARK_TITLE_MAX_CHARS", "18")),
        help="Pass through to douban_promo_copy (豆包 title 最长字数)",
    )

    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--reuse-existing-tab", action="store_true")
    parser.add_argument("--timing-jitter", type=float, default=0.25)
    parser.add_argument("--account", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--disconnect-cdp", action="store_true")
    parser.add_argument("--skip-file-check", action="store_true")
    parser.add_argument("--preserve-upload-paths", action="store_true")
    parser.add_argument("--post-time", default=None)
    parser.add_argument("--temp-dir", default=None)

    args = parser.parse_args()

    has_brief = bool(args.brief_file) or (
        args.brief is not None and str(args.brief).strip() != ""
    )
    if not has_brief and not args.dry_run_promo:
        print("Error: provide --brief or --brief-file (or --dry-run-promo).", file=sys.stderr)
        sys.exit(2)

    promo_script = Path(SCRIPT_DIR) / "douban_promo_copy.py"
    publish_script = Path(SCRIPT_DIR) / "publish_pipeline.py"

    promo_cmd: list[str] = [sys.executable, str(promo_script)]

    if args.brief_file:
        promo_cmd.extend(["--brief-file", args.brief_file])
    elif args.dry_run_promo and not has_brief:
        promo_cmd.extend(["--brief", "dry-run"])
    else:
        promo_cmd.extend(["--brief", args.brief or ""])

    if args.seed_keyword:
        promo_cmd.extend(["--seed-keyword", args.seed_keyword])

    promo_cmd.extend(["--images", *args.images])

    if args.promo_out_dir:
        promo_cmd.extend(["--out-dir", args.promo_out_dir])

    if args.api_url:
        promo_cmd.extend(["--api-url", args.api_url])
    if args.api_key:
        promo_cmd.extend(["--api-key", args.api_key])

    promo_cmd.extend(["--timeout", str(args.promo_timeout)])

    if args.promo_insecure:
        promo_cmd.append("--insecure")
    if args.dry_run_promo:
        promo_cmd.append("--dry-run")
    if args.dump_raw_response:
        promo_cmd.append("--dump-raw-response")

    promo_cmd.extend(["--provider", args.provider])
    if args.provider == "ark":
        if args.ark_base_url:
            promo_cmd.extend(["--ark-base-url", args.ark_base_url])
        if args.ark_api_key:
            promo_cmd.extend(["--ark-api-key", args.ark_api_key])
        if args.ark_model:
            promo_cmd.extend(["--ark-model", args.ark_model])
        promo_cmd.extend(
            [
                "--ark-body-min",
                str(args.ark_body_min),
                "--ark-body-max",
                str(args.ark_body_max),
                "--ark-title-max",
                str(max(1, min(512, int(args.ark_title_max)))),
            ]
        )

    print("[one_shot] Step 1: douban_promo_copy...")
    proc = subprocess.run(
        promo_cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)

    if proc.returncode != 0:
        sys.exit(proc.returncode)

    if args.promo_out_dir:
        out_dir = Path(args.promo_out_dir)
    else:
        out_dir = _parse_out_dir(proc.stdout or "")
        if not out_dir:
            print(
                "Error: could not parse [douban_promo] OUT_DIR= from promo output; "
                "pass --promo-out-dir explicitly.",
                file=sys.stderr,
            )
            sys.exit(2)

    title_file = out_dir / "title.txt"
    content_file = out_dir / "content.txt"
    if not title_file.is_file() or not content_file.is_file():
        print(f"Error: missing {title_file} or {content_file}", file=sys.stderr)
        sys.exit(2)

    pub_cmd: list[str] = [
        sys.executable,
        str(publish_script),
        "--title-file",
        str(title_file),
        "--content-file",
        str(content_file),
        "--images",
        *args.images,
        "--timing-jitter",
        str(args.timing_jitter),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]

    if args.preview or args.dry_run_promo:
        pub_cmd.append("--preview")
    if args.dry_run_promo and not args.preview:
        print(
            "[one_shot] --dry-run-promo implies --preview (will not click publish).",
            file=sys.stderr,
        )
    if args.headless:
        pub_cmd.append("--headless")
    if args.reuse_existing_tab:
        pub_cmd.append("--reuse-existing-tab")
    if args.account:
        pub_cmd.extend(["--account", args.account])
    if args.disconnect_cdp:
        pub_cmd.append("--disconnect-cdp")
    if args.skip_file_check:
        pub_cmd.append("--skip-file-check")
    if args.preserve_upload_paths:
        pub_cmd.append("--preserve-upload-paths")
    if args.post_time:
        pub_cmd.extend(["--post-time", args.post_time])
    if args.temp_dir:
        pub_cmd.extend(["--temp-dir", args.temp_dir])

    print("[one_shot] Step 2: publish_pipeline...")
    r2 = subprocess.run(pub_cmd, cwd=REPO_ROOT)
    sys.exit(r2.returncode)


if __name__ == "__main__":
    main()
