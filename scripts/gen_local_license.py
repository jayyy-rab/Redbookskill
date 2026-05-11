"""
Generate a simple local license file.

Usage:
    python scripts/gen_local_license.py --license-key RBK-XXXX --days 30 --out config/license.key
"""

from __future__ import annotations

import argparse
import json
import secrets
import string
from datetime import datetime, timedelta, timezone


def _random_key() -> str:
    alphabet = string.ascii_uppercase + string.digits
    chunks = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "RBK-" + "-".join(chunks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate local license JSON file.")
    parser.add_argument("--license-key", default="", help="License key string; auto-generate if empty.")
    parser.add_argument("--days", type=int, default=30, help="Validity days from now (default: 30).")
    parser.add_argument("--out", default="config/license.key", help="Output file path.")
    args = parser.parse_args()

    license_key = args.license_key.strip() or _random_key()
    expires_at = datetime.now(timezone.utc) + timedelta(days=max(1, args.days))
    payload = {
        "license_key": license_key,
        "expires_at": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"license_key={license_key}")
    print(f"expires_at={payload['expires_at']}")
    print(f"written={args.out}")


if __name__ == "__main__":
    main()

