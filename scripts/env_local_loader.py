from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from pipeline_debug import pipeline_debug_log


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    pipeline_debug_log(
        hypothesis_id,
        location,
        message,
        data,
        run_id=f"env_loader_{int(time.time() * 1000)}",
    )
    # endregion


def _load_one_env_file(env_file: Path) -> None:
    if not env_file.is_file():
        return

    loaded_keys: list[str] = []
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip().strip("'").strip('"')
        before = key in os.environ and bool((os.environ.get(key) or "").strip())
        os.environ.setdefault(key, value)
        after = key in os.environ and bool((os.environ.get(key) or "").strip())
        if (not before) and after:
            loaded_keys.append(key)

    _debug_log(
        "H11",
        "env_local_loader.py:_load_one_env_file",
        "Processed env file",
        {
            "file": str(env_file),
            "loaded_count": len(loaded_keys),
            "loaded_keys": [k for k in loaded_keys if "KEY" not in k],
            "loaded_ark_api_key": "ARK_API_KEY" in loaded_keys,
            "loaded_ark_model": "ARK_MODEL" in loaded_keys,
        },
    )


def load_env_local(start_path: str | Path) -> None:
    """
    Load KEY=VALUE pairs from repo-level .env.local into process env.
    Existing environment variables are preserved.
    """
    p = Path(start_path).resolve()
    root = p.parent if p.is_file() else p
    # Portable-first then local overrides are still allowed via real env vars.
    _load_one_env_file(root / ".env.portable")
    _load_one_env_file(root / ".env.local")
    _debug_log(
        "H12",
        "env_local_loader.py:load_env_local",
        "Env resolved after loading portable/local",
        {
            "has_ark_api_key": bool((os.environ.get("ARK_API_KEY") or "").strip()),
            "has_ark_model": bool((os.environ.get("ARK_MODEL") or "").strip()),
            "provider": (os.environ.get("DOUBAN_PROMO_PROVIDER") or "").strip(),
        },
    )
