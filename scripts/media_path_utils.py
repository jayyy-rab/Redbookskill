"""Shared helpers for generated image path filtering/deduplication."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def dedupe_existing_paths_by_hash(paths: list[str]) -> list[str]:
    """Keep existing files only, deduplicated by sha256 hash while preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        pp = Path(p)
        if not pp.is_file():
            continue
        digest = hashlib.sha256(pp.read_bytes()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        out.append(str(pp))
    return out


def validate_publish_images(payload: dict[str, Any], image_paths: list[str]) -> list[str]:
    """
    Guardrail: only keep valid generated outputs for publish.

    - must exist;
    - if generated root exists, image must be under generated root;
    - if reference root exists, image must not be under reference root.
    """
    generated_root_raw = str(payload.get("generated_output_dir") or "").strip()
    ref_root_raw = str(payload.get("output_dir") or "").strip()
    generated_root = Path(generated_root_raw).resolve() if generated_root_raw else None
    ref_root = Path(ref_root_raw).resolve() if ref_root_raw else None

    out: list[str] = []
    for p in image_paths:
        pp = Path(p).resolve()
        if not pp.is_file():
            continue
        if generated_root and not pp.is_relative_to(generated_root):
            continue
        if ref_root and pp.is_relative_to(ref_root):
            continue
        out.append(str(pp))
    return out

