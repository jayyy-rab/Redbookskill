import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"


class TestCliHelp(unittest.TestCase):
    def _run_help(self, script_name: str) -> None:
        cmd = [sys.executable, str(SCRIPTS_DIR / script_name), "--help"]
        cp = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(
            cp.returncode,
            0,
            msg=f"{script_name} --help failed:\nSTDOUT:\n{cp.stdout}\nSTDERR:\n{cp.stderr}",
        )
        self.assertTrue(cp.stdout.strip())

    def test_publish_pipeline_help(self) -> None:
        self._run_help("publish_pipeline.py")

    def test_bulk_publish_help(self) -> None:
        self._run_help("bulk_publish_accounts.py")

    def test_orchestrated_help(self) -> None:
        self._run_help("full_stack_orchestrated.py")


if __name__ == "__main__":
    unittest.main()
