from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))

import doctor
import snapshot_tool
from deadline_guard import fail_if_call_blocks


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
        self.assertFalse(report["runtime_readiness"]["attested"])
        self.assertEqual(report["runtime_readiness"]["result"], "UNATTESTED")
        self.assertEqual(report["runtime_readiness"]["record_result"], "NOT_READY")
        self.assertEqual(
            report["runtime_readiness"]["read_only_enforcement"], "unverified"
        )
        self.assertEqual(
            report["runtime_readiness"]["artifact_only_read_enforcement"], "unknown"
        )
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
            missing_git = str(Path(temporary) / "missing-git")
            with mock.patch.object(snapshot_tool, "GIT_EXECUTABLE", missing_git):
                code, report = doctor.diagnose(str(ROOT))
        self.assertEqual(code, 2)
        git_check = next(
            check for check in report["checks"] if check["name"] == "git_executable"
        )
        self.assertEqual(git_check["status"], "FAILED")

    def test_documentation_named_pipe_and_symlink_fail_without_blocking(self) -> None:
        cases = ("fifo", "symlink")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                document = root / "hostile.md"
                if case == "fifo":
                    os.mkfifo(document)
                else:
                    os.symlink("/dev/zero", document)
                with doctor.SafeRoot(root) as reader:
                    with fail_if_call_blocks(), self.assertRaises(
                        doctor.SafeReadError
                    ):
                        doctor._documentation_errors(reader)

    def test_documentation_scan_enforces_file_count_and_total_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "one.md").write_text("123456", encoding="utf-8")
            (root / "two.md").write_text("123456", encoding="utf-8")

            with mock.patch.object(doctor, "MAX_DOCUMENTS", 1):
                with doctor.SafeRoot(root) as reader:
                    with self.assertRaisesRegex(
                        doctor.SafeReadError, "document limit"
                    ):
                        doctor._documentation_errors(reader)

            with mock.patch.object(doctor, "MAX_ALL_DOCUMENT_BYTES", 10):
                with doctor.SafeRoot(root) as reader:
                    with self.assertRaisesRegex(doctor.SafeReadError, "byte limit"):
                        doctor._documentation_errors(reader)

    def test_documentation_scan_rejects_an_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "large.md").write_bytes(b"x" * 9)
            with mock.patch.object(doctor, "MAX_DOCUMENT_BYTES", 8):
                with doctor.SafeRoot(root) as reader:
                    with self.assertRaisesRegex(doctor.SafeReadError, "byte limit"):
                        doctor._documentation_errors(reader)

    def test_documentation_scan_bounds_link_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "links.md").write_text("[one](x) [two](y)\n", encoding="utf-8")
            with mock.patch.object(doctor, "MAX_DOCUMENT_LINKS", 1):
                with doctor.SafeRoot(root) as reader:
                    with self.assertRaisesRegex(doctor.SafeReadError, "link limit"):
                        doctor._documentation_errors(reader)

    def test_documentation_links_do_not_accept_symlink_targets(self) -> None:
        targets = ("missing-target", "/etc/passwd")
        for target in targets:
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "README.md").write_text(
                    "[unsafe](asset)\n", encoding="utf-8"
                )
                os.symlink(target, root / "asset")
                with doctor.SafeRoot(root) as reader:
                    errors = doctor._documentation_errors(reader)
            self.assertEqual(errors, ["README.md -> asset"])


if __name__ == "__main__":
    unittest.main()
