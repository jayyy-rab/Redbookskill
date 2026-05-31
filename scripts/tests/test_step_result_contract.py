"""Minimal contract test for unified StepResult IO (PRD v1.0 format)."""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_core import StepErrorPayload, StepResult, StepSuccessCheck, StepEvidence, BugRecord
from workflow_io import (
    append_failure_record,
    build_failure_record,
    read_step_result,
    write_step_result,
)
from workflow_status import StepStatus


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="step_contract_") as td:
        base = Path(td)
        step_path = base / "step_result.json"
        fail_path = base / "failure_records.json"

        result = StepResult(
            task_id="task_demo_001",
            account_id="acc_001",
            step_id="step1_validate_input",
            step_name="校验输入",
            status=StepStatus.SUCCESS.value,
            started_at="2026-05-10T10:00:00",
            finished_at="2026-05-10T10:00:05",
            evidence=StepEvidence(
                screenshot="screenshots/step1_ok.png",
                url="",
                title_text="",
                body_text="",
            ),
            artifacts={"valid_images": ["product.jpg"]},
            success_check=StepSuccessCheck(passed=True, items=["file_exists", "file_readable"]),
            error=None,
            retry_count=0,
            created_files=["step1_ok.png"],
        )

        written = write_step_result(step_path, result)
        loaded = read_step_result(written)

        assert loaded.task_id == "task_demo_001"
        assert loaded.account_id == "acc_001"
        assert loaded.step_id == "step1_validate_input"
        assert loaded.status == StepStatus.SUCCESS.value
        assert loaded.success_check.passed is True
        assert loaded.evidence.screenshot == "screenshots/step1_ok.png"

        rec = build_failure_record(
            task_id="task_demo_001",
            step_index=8,
            step_name="generate_post_copy",
            error_code="COPY_QUALITY_FAILED",
            error_message="post_text_length out of range",
            error_detail="expected 120~180, got 89",
            error_level="P2",
        )
        append_failure_record(fail_path, rec)
        append_failure_record(
            fail_path,
            build_failure_record(
                task_id="task_demo_001",
                step_index=10,
                step_name="attach_product_verify",
                error_code="PRODUCT_NOT_EXACT_MATCH",
                error_message="match rule not exact",
                error_detail="rule=similarity",
                error_level="P1",
            ),
        )

        assert fail_path.is_file()
        print("TEST_PASS: StepResult contract generate/save/read and failure record append are OK.")


if __name__ == "__main__":
    main()
