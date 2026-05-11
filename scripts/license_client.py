"""
Online license activation and periodic renewal client.

Expected server API:
1) POST /v1/licenses/activate
2) POST /v1/licenses/renew

Request JSON:
{
  "license_key": "...",
  "product": "redbookskills-monthly",
  "client_version": "x.y.z"
}

Response JSON:
{
  "ok": true,
  "access_token": "...",
  "expires_at": "2026-06-01T00:00:00Z"
}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


class LicenseError(RuntimeError):
    """Raised when license activation/renewal fails."""


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LicenseResult:
    access_token: str
    expires_at: datetime
    source: str


class LicenseClient:
    """Client-side license checker with local cache and periodic renewal."""

    def __init__(
        self,
        server_url: str,
        license_key: str,
        product: str = "redbookskills-monthly",
        cache_path: str | None = None,
        renew_before_hours: float = 24.0,
        grace_hours: float = 24.0,
        timeout_seconds: float = 8.0,
        client_version: str = "unknown",
    ):
        self.server_url = server_url.rstrip("/")
        self.license_key = license_key.strip()
        self.product = product.strip() or "redbookskills-monthly"
        self.cache_path = (
            cache_path
            if cache_path
            else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tmp", "license_cache.json"))
        )
        self.renew_before = timedelta(hours=max(0.0, renew_before_hours))
        self.grace = timedelta(hours=max(0.0, grace_hours))
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.client_version = client_version

    def ensure_valid(self) -> LicenseResult:
        cached = self._read_cache()
        now = _utc_now()
        if cached:
            expires_at = _parse_utc_datetime(cached.get("expires_at"))
            token = str(cached.get("access_token") or "").strip()
            if token and expires_at:
                if expires_at - now > self.renew_before:
                    return LicenseResult(access_token=token, expires_at=expires_at, source="cache")
                try:
                    renewed = self._renew(token)
                    self._write_cache(renewed)
                    return LicenseResult(
                        access_token=renewed["access_token"],
                        expires_at=_parse_utc_datetime(renewed["expires_at"]) or expires_at,
                        source="renew",
                    )
                except LicenseError:
                    if expires_at + self.grace > now:
                        return LicenseResult(access_token=token, expires_at=expires_at, source="cache_grace")
                    raise

        activated = self._activate()
        self._write_cache(activated)
        activated_expiry = _parse_utc_datetime(activated["expires_at"])
        if not activated_expiry:
            raise LicenseError("License server returned invalid expires_at.")
        return LicenseResult(
            access_token=activated["access_token"],
            expires_at=activated_expiry,
            source="activate",
        )

    def _activate(self) -> dict[str, str]:
        return self._post_json(
            path="/v1/licenses/activate",
            payload={
                "license_key": self.license_key,
                "product": self.product,
                "client_version": self.client_version,
            },
        )

    def _renew(self, access_token: str) -> dict[str, str]:
        return self._post_json(
            path="/v1/licenses/renew",
            payload={
                "license_key": self.license_key,
                "product": self.product,
                "access_token": access_token,
                "client_version": self.client_version,
            },
        )

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, str]:
        if not self.server_url:
            raise LicenseError("Missing license server URL.")
        if not self.license_key:
            raise LicenseError("Missing license key.")

        endpoint = f"{self.server_url}{path}"
        try:
            response = requests.post(endpoint, json=payload, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            raise LicenseError(f"License server request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise LicenseError("License server response is not valid JSON.") from exc

        if response.status_code != 200 or not data.get("ok"):
            message = data.get("message") if isinstance(data, dict) else None
            detail = message or f"HTTP {response.status_code}"
            raise LicenseError(f"License check failed: {detail}")

        access_token = str(data.get("access_token") or "").strip()
        expires_at = str(data.get("expires_at") or "").strip()
        if not access_token or not _parse_utc_datetime(expires_at):
            raise LicenseError("License response missing access_token or valid expires_at.")

        return {"access_token": access_token, "expires_at": expires_at}

    def _read_cache(self) -> dict[str, Any] | None:
        try:
            if not os.path.isfile(self.cache_path):
                return None
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None

    def _write_cache(self, payload: dict[str, str]) -> None:
        cache_dir = os.path.dirname(self.cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

