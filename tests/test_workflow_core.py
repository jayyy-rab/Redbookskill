import json
import tempfile
import unittest
from pathlib import Path

from scripts.workflow_core import (
    StepFailure,
    WorkflowContext,
    WorkflowRunner,
    WorkflowStep,
)


class _OkStep(WorkflowStep):
    name = "ok_step"

    def run(self, ctx: WorkflowContext) -> dict:
        return {"value": 1}


class _FailStep(WorkflowStep):
    name = "fail_step"

    def run(self, ctx: WorkflowContext) -> dict:
        raise StepFailure("failed", code=7, detail={"stage": "x"})


class TestWorkflowCore(unittest.TestCase):
    def test_runner_stops_on_failure_in_strict_mode(self) -> None:
        ctx = WorkflowContext(run_id="r1", strict=True)
        runner = WorkflowRunner(strict=True)
        code, reports = runner.execute([_OkStep(), _FailStep(), _OkStep()], ctx)
        self.assertEqual(code, 7)
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0].status, "success")
        self.assertEqual(reports[1].status, "failed")

    def test_report_writer_outputs_json(self) -> None:
        ctx = WorkflowContext(run_id="r2", strict=True, artifacts={"a": 1}, meta={"m": 2})
        runner = WorkflowRunner(strict=True)
        code, reports = runner.execute([_OkStep()], ctx)
        self.assertEqual(code, 0)

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.json"
            runner.write_report(out, ctx, reports, code)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], "r2")
            self.assertEqual(payload["exit_code"], 0)
            self.assertEqual(len(payload["steps"]), 1)


if __name__ == "__main__":
    unittest.main()
