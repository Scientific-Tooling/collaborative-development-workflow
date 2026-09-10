from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, "scripts")

import doctor


ROOT = Path(__file__).resolve().parents[1]


class DoctorTests(unittest.TestCase):
    def test_local_helpers_are_usable_without_claiming_runtime_readiness(self) -> None:
        code, report = doctor.diagnose(str(ROOT))
        self.assertEqual(code, 0, report)
        self.assertTrue(report["valid"])
        self.assertFalse(report["runtime_readiness"]["attested"])
        self.assertEqual(report["runtime_readiness"]["result"], "UNATTESTED")

    def test_supplied_preflight_is_validated_not_invented(self) -> None:
        preflight = ROOT / "examples" / "capability_preflight.json"
        code, report = doctor.diagnose(str(ROOT), str(preflight))
        self.assertEqual(code, 0, report)
        self.assertEqual(report["runtime_readiness"]["result"], "PORTABLE_READY")
        self.assertEqual(report["runtime_readiness"]["authority"], "observed_tool_surface")

    def test_invalid_preflight_fails_with_bounded_json_cli_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            invalid = Path(temporary) / "preflight.json"
            invalid.write_text("{}", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "doctor.py"),
                    "--root",
                    str(ROOT),
                    "--preflight",
                    str(invalid),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            report = json.loads(result.stdout)
            self.assertFalse(report["valid"])
            self.assertLess(len(result.stdout.encode("utf-8")), 16384)

    def test_missing_git_executable_fails_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {"PATH": temporary}, clear=False):
                code, report = doctor.diagnose(str(ROOT))
        self.assertEqual(code, 2)
        git_check = next(
            check for check in report["checks"] if check["name"] == "git_executable"
        )
        self.assertEqual(git_check["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
