"""
Minimal-cost smoke runner for regression checks.

Covers:
1) login health check
2) orchestrated pipeline dry-run (step range)
3) multi-account round-robin preview publish
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TMP_DIR = REPO_ROOT / "tmp"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from account_manager import list_accounts  # noqa: E402


@dataclass
class CaseResult:
    name: str
    ok: bool
    returncode: int
    duration_ms: int
    command: list[str]
    stdout_tail: str
    stderr_tail: str


def _now_iso() -> str:
    return datetime.now().isoformat()


def _resolve_accounts(explicit: list[str] | None) -> list[str]:
    if explicit:
        return [x.strip() for x in explicit if x.strip()]
    rows = [x for x in list_accounts() if x.get("name")]
    names = [str(x["name"]) for x in rows]
    if not names:
        raise SystemExit("No accounts configured in config/accounts.json.")
    non_default = [x for x in rows if not bool(x.get("is_default"))]
    preferred = [str(x["name"]) for x in non_default] or names
    return preferred[:2]


def _ensure_text_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(content, encoding="utf-8")


def _run_case(name: str, cmd: list[str], timeout_seconds: int) -> CaseResult:
    started = datetime.now()
    cp = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout_seconds)),
    )
    ended = datetime.now()
    ms = int((ended - started).total_seconds() * 1000)
    return CaseResult(
        name=name,
        ok=(cp.returncode == 0),
        returncode=int(cp.returncode),
        duration_ms=ms,
        command=cmd,
        stdout_tail=(cp.stdout or "")[-2000:],
        stderr_tail=(cp.stderr or "")[-2000:],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal-cost smoke runner.")
    parser.add_argument("--accounts", nargs="*", default=None, help="Account names to include.")
    parser.add_argument("--image", default=str(REPO_ROOT / "image.png"), help="One local image for preview checks.")
    parser.add_argument("--title-file", default=str(TMP_DIR / "xhs_promo_out" / "title.txt"))
    parser.add_argument("--content-file", default=str(TMP_DIR / "xhs_promo_out" / "content.txt"))
    parser.add_argument("--json-out", default=str(TMP_DIR / "smoke_report_latest.json"))
    parser.add_argument("--skip-login-check", action="store_true")
    parser.add_argument("--skip-orchestrated-check", action="store_true")
    parser.add_argument("--skip-bulk-check", action="store_true")
    parser.add_argument("--timeout-login", type=int, default=240)
    parser.add_argument("--timeout-orchestrated", type=int, default=180)
    parser.add_argument("--timeout-bulk", type=int, default=900)
    args = parser.parse_args()

    accounts = _resolve_accounts(args.accounts)
    image_path = Path(args.image).resolve()
    if not image_path.is_file():
        raise SystemExit(f"Image not found: {image_path}")

    title_file = Path(args.title_file).resolve()
    content_file = Path(args.content_file).resolve()
    _ensure_text_file(title_file, "茶叶上新，清爽回甘\n")
    _ensure_text_file(
        content_file,
        "今天分享一款清爽耐泡的口粮茶，适合办公室和日常自饮。\n\n#茶叶 #日常喝茶\n",
    )

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "started_at": _now_iso(),
        "accounts": accounts,
        "image": str(image_path),
        "cases": [],
    }

    py = sys.executable

    if not args.skip_login_check:
        cmd = [
            py,
            str(SCRIPT_DIR / "batch_check_login.py"),
            "--accounts",
            *accounts,
            "--json-out",
            str(TMP_DIR / "login_audit_latest.json"),
        ]
        report["cases"].append(asdict(_run_case("login_check", cmd, args.timeout_login)))

    if not args.skip_orchestrated_check:
        cmd = [
            py,
            str(SCRIPT_DIR / "full_stack_orchestrated.py"),
            "--from-step",
            "b",
            "--to-step",
            "c",
            "--dry-run",
            "--seed-keyword",
            "茶叶",
            "--generated-images",
            str(image_path),
            "--workflow-report-out",
            str(TMP_DIR / "workflow_report_smoke.json"),
        ]
        report["cases"].append(asdict(_run_case("orchestrated_dry_run", cmd, args.timeout_orchestrated)))

    if not args.skip_bulk_check:
        cmd = [
            py,
            str(SCRIPT_DIR / "bulk_publish_accounts.py"),
            "--skip-prepare",
            "--preview",
            "--accounts",
            *accounts,
            "--round-robin",
            "--continue-on-failure",
            "--title-file",
            str(title_file),
            "--content-file",
            str(content_file),
            "--images",
            str(image_path),
            "--retries",
            "0",
            "--sleep-min",
            "1",
            "--sleep-max",
            "2",
        ]
        report["cases"].append(asdict(_run_case("bulk_round_robin_preview", cmd, args.timeout_bulk)))

    ok = all(bool(x.get("ok")) for x in report["cases"])
    report["ended_at"] = _now_iso()
    report["ok"] = ok
    report["failed_count"] = sum(1 for x in report["cases"] if not bool(x.get("ok")))

    out_path = Path(args.json_out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[smoke] report={out_path}")
    print(f"[smoke] cases={len(report['cases'])} failed={report['failed_count']}")
    if not ok:
        sys.exit(2)


if __name__ == "__main__":
    main()

