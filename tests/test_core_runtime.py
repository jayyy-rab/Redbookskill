import json
import tempfile
import unittest
from pathlib import Path

from scripts import core_config
from scripts.core_logger import RunLogger


class TestCoreRuntime(unittest.TestCase):
    def test_resolve_runtime_target_prefers_runner_port_when_not_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runner = Path(td) / "runner.json"
            accounts = Path(td) / "accounts.json"
            runner.write_text(
                json.dumps({"runner_account": "runner", "runner_port": 9555}),
                encoding="utf-8",
            )
            accounts.write_text(
                json.dumps(
                    {
                        "default_account": "runner",
                        "accounts": {"runner": {"port": 9333}},
                    }
                ),
                encoding="utf-8",
            )

            old_runner = core_config.RUNNER_FILE
            old_accounts = core_config.ACCOUNTS_FILE
            try:
                core_config.RUNNER_FILE = runner
                core_config.ACCOUNTS_FILE = accounts
                account, port, meta = core_config.resolve_runtime_target(
                    host="127.0.0.1",
                    port=9222,
                    account=None,
                    port_explicit=False,
                    account_explicit=False,
                )
            finally:
                core_config.RUNNER_FILE = old_runner
                core_config.ACCOUNTS_FILE = old_accounts

            self.assertEqual(account, "runner")
            self.assertEqual(port, 9555)
            self.assertEqual(meta["port_source"], "runner")

    def test_run_logger_writes_ndjson(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "run.ndjson"
            logger = RunLogger(component="t", run_id="r", context={"k": "v"})
            logger.log_file = path
            logger.info("evt", data={"x": 1})
            self.assertTrue(path.is_file())
            line = path.read_text(encoding="utf-8").strip()
            payload = json.loads(line)
            self.assertEqual(payload["component"], "t")
            self.assertEqual(payload["run_id"], "r")
            self.assertEqual(payload["event"], "evt")


if __name__ == "__main__":
    unittest.main()
