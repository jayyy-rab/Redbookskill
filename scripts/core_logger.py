"""
Structured runtime logger for CLI workflows.

Design goals:
- Keep existing CLI output behavior by default.
- Add machine-readable NDJSON events for post-run diagnostics.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
LOG_DIR = REPO_ROOT / "tmp" / "structured_logs"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_write(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        # Never break the business flow due to logging failures.
        pass


class RunLogger:
    def __init__(self, component: str, run_id: str, context: dict[str, Any] | None = None):
        self.component = component
        self.run_id = run_id
        self.context = context or {}
        self.log_file = LOG_DIR / f"{self.component}.ndjson"

    def emit(
        self,
        *,
        level: str,
        event: str,
        message: str = "",
        data: dict[str, Any] | None = None,
        mirror: bool = False,
        stderr: bool = False,
    ) -> None:
        payload = {
            "ts_ms": _now_ms(),
            "level": level.lower(),
            "component": self.component,
            "run_id": self.run_id,
            "event": event,
            "message": message,
            "context": self.context,
            "data": data or {},
            "pid": os.getpid(),
        }
        _safe_write(self.log_file, json.dumps(payload, ensure_ascii=False) + "\n")

        if mirror and message:
            stream = sys.stderr if stderr else sys.stdout
            print(message, file=stream, flush=True)

    def info(self, event: str, message: str = "", data: dict[str, Any] | None = None, mirror: bool = False) -> None:
        self.emit(level="info", event=event, message=message, data=data, mirror=mirror, stderr=False)

    def warning(
        self,
        event: str,
        message: str = "",
        data: dict[str, Any] | None = None,
        mirror: bool = False,
    ) -> None:
        self.emit(level="warning", event=event, message=message, data=data, mirror=mirror, stderr=False)

    def error(self, event: str, message: str = "", data: dict[str, Any] | None = None, mirror: bool = False) -> None:
        self.emit(level="error", event=event, message=message, data=data, mirror=mirror, stderr=True)


def create_run_logger(component: str, **context: Any) -> RunLogger:
    run_id = f"{component}_{_now_ms()}_{uuid.uuid4().hex[:8]}"
    return RunLogger(component=component, run_id=run_id, context=context)

