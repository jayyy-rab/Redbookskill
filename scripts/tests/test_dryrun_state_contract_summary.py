"""Minimal test for dryrun state.json contract_summary."""

from __future__ import annotations

import json
import tempfile
from argparse import Namespace
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dryrun_step1_10_runner import run_strict_dryrun


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dryrun_state_contract_") as td:
        run_dir = Path(td) / "run"
        args = Namespace(
            product_image=str(Path(td) / "missing_product.png"),
            keyword="茶叶",
            product_name="米酒",
            product_id="",
            account="test_account",
            host="127.0.0.1",
            port=9222,
            run_dir=str(run_dir),
        )

        payload = run_strict_dryrun(args)
        assert payload.get("final_status") in {"failed", "partial_success"}

        state_path = run_dir / "state.json"
        assert state_path.is_file()
        state = json.loads(state_path.read_text(encoding="utf-8"))

        summary = state.get("contract_summary")
        assert isinstance(summary, dict)
        assert isinstance(summary.get("total_steps_written"), int)
        assert isinstance(summary.get("status_counts"), dict)
        assert summary.get("blocked") is True
        assert summary.get("last_step_index") == 10
        assert str(summary.get("last_step_status")) == "skipped"

        counts = summary.get("status_counts") or {}
        assert int(counts.get("failed", 0)) >= 1
        assert int(counts.get("skipped", 0)) >= 1

        print("TEST_PASS: dryrun state.json contains contract_summary with blocked failure snapshot.")


if __name__ == "__main__":
    main()
