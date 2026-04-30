"""Single-line NDJSON debug lines to repo-root debug-471add.log (session 471add)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SESSION_ID = "471add"
_WARNED_WRITE_FAILED = False


def _debug_ndjson_targets() -> list[Path]:
    """Repo root, `.cursor/`, cwd, and user-home mirror (survives divergent cwd/workspace sync)."""
    seen: set[Path] = set()
    out: list[Path] = []
    for p in (
        _REPO_ROOT / "debug-471add.log",
        _REPO_ROOT / ".cursor" / "debug-471add.log",
        Path.cwd() / "debug-471add.log",
        Path.home() / ".redbookskills_debug_471add.ndjson",
    ):
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def pipeline_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
    *,
    run_id: str | None = None,
) -> None:
    # region agent log
    payload: dict[str, object] = {
        "sessionId": _SESSION_ID,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    payload["runId"] = (
        run_id
        if run_id is not None
        else f"pip_{hypothesis_id}_{int(time.time() * 1000)}"
    )
    global _WARNED_WRITE_FAILED
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    any_ok = False
    for tgt in _debug_ndjson_targets():
        try:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            with tgt.open("a", encoding="utf-8") as fh:
                fh.write(line)
            any_ok = True
        except Exception as e:
            if not _WARNED_WRITE_FAILED:
                _WARNED_WRITE_FAILED = True
                print(
                    f"[pipeline_debug] could not write {tgt}: {e}",
                    file=sys.stderr,
                    flush=True,
                )
    if not any_ok:
        print(line.strip(), file=sys.stderr, flush=True)
    # endregion


if __name__ == "__main__":
    pipeline_debug_log(
        "CLI",
        "pipeline_debug.py:__main__",
        "smoke",
        {"cwd": str(Path.cwd()), "repo_root": str(_REPO_ROOT.resolve())},
    )
    print("[pipeline_debug] NDJSON targets (session 471add):", flush=True)
    for p in _debug_ndjson_targets():
        print(f"  {p.resolve()}", flush=True)
