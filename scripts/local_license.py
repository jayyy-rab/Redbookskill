"""
Very simple local license validator.

License file format (JSON):
{
  "license_key": "RBK-XXXX-XXXX",
  "expires_at": "2026-12-31T23:59:59Z"
}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone


class LocalLicenseError(RuntimeError):
    """Raised when local license validation fails."""


@dataclass
class LocalLicenseResult:
    license_key: str
    expires_at: datetime


def _parse_utc_datetime(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def validate_local_license_file(
    license_file_path: str,
    expected_license_key: str | None,
) -> LocalLicenseResult:
    if not os.path.isfile(license_file_path):
        raise LocalLicenseError(f"License file not found: {license_file_path}")

    try:
        with open(license_file_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        raise LocalLicenseError(f"License file is invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise LocalLicenseError("License file content must be a JSON object.")

    file_license_key = str(payload.get("license_key") or "").strip()
    file_expires_at = _parse_utc_datetime(str(payload.get("expires_at") or "").strip())
    if not file_license_key:
        raise LocalLicenseError("license_key is missing in license file.")
    if not file_expires_at:
        raise LocalLicenseError("expires_at is missing or invalid in license file.")

    if expected_license_key and expected_license_key.strip():
        if file_license_key != expected_license_key.strip():
            raise LocalLicenseError("License key mismatch.")

    now = datetime.now(timezone.utc)
    if file_expires_at <= now:
        raise LocalLicenseError("License has expired.")

    return LocalLicenseResult(license_key=file_license_key, expires_at=file_expires_at)

