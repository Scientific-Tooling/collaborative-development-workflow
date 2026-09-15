from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import behavior_eval
from deadline_guard import fail_if_call_blocks
from git_fixture import GitRepositoryTemplate, fixture_git_environment


RUNNER_IMAGE_ID = "sha256:" + "a" * 64
VALIDATION_IMAGE_ID = "sha256:" + "b" * 64


def _run_fixture_git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=fixture_git_environment(),
    )


def _build_behavior_source(root: Path) -> None:
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: fixture\ndescription: fixture\n---\n", encoding="utf-8"
    )
    (root / "target.txt").write_text("before\n", encoding="utf-8")
    (root / ".gitignore").write_text(".secret\n", encoding="utf-8")
    (root / ".secret").write_text("must not be copied\n", encoding="utf-8")
    _run_fixture_git(root, "init", "-q", "--initial-branch=fixture")
    _run_fixture_git(root, "config", "user.name", "Behavior Eval Test")
    _run_fixture_git(root, "config", "user.email", "test@example.invalid")
    _run_fixture_git(root, "add", "--all")
    _run_fixture_git(root, "commit", "-qm", "fixture")


class BehaviorEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.source_template = GitRepositoryTemplate(_build_behavior_source)
        cls.addClassCleanup(cls.source_template.cleanup)

    def require_user_xattrs(self, root: Path) -> None:
        if not all(
            hasattr(os, name)
            for name in ("setxattr", "listxattr", "getxattr")
        ):
            self.skipTest("extended attributes are not available")
        probe = root / "xattr-probe"
        probe.write_bytes(b"")
        try:
            os.setxattr(probe, b"user.cdw-probe", b"supported")
            self.assertIn("user.cdw-probe", os.listxattr(probe))
            self.assertEqual(
                os.getxattr(probe, b"user.cdw-probe"), b"supported"
            )
        except OSError as exc:
            self.skipTest(f"user extended attributes are not supported: {exc}")
        finally:
            probe.unlink(missing_ok=True)

    def run_git(self, root: Path, *arguments: str) -> None:
        _run_fixture_git(root, *arguments)

    def write_case(
        self,
        path: Path,
        *,
        validation_commands: list[list[str]] | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        path.write_text(
            json.dumps(
                {
                    "version": "skill-behavior-case-v1",
                    "id": "fixture",
                    "prompt": "Edit target.txt using {SKILL_PATH}",
                    "allowed_changed_paths": ["target.txt"],
                    "required_changed_paths": ["target.txt"],
                    "validation_commands": validation_commands
                    if validation_commands is not None
                    else [
                        [
                            sys.executable,
                            "-c",
                            "assert open('target.txt').read() == 'after\\n'",
                        ]
                    ],
                    "required_output_lines": ["ACCEPTED_PORTABLE"],
                    "timeout_seconds": timeout_seconds,
                }
            ),
            encoding="utf-8",
        )

    def make_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        source = root / "source"
        self.source_template.copy_to(source)

        skill = root / "skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: fixture\ndescription: fixture\n---\n", encoding="utf-8"
        )
        case = root / "case.json"
        self.write_case(case)
        return source, skill, case

    def write_runner(self, root: Path, source: str, name: str = "runner.py") -> Path:
        runner = root / name
        runner.write_text(source, encoding="utf-8")
        return runner

    def run_fixture(
        self,
        root: Path,
        source: Path,
        skill: Path,
        case: Path,
        runner: Path,
        *,
        inherited_environment_names: set[str] | None = None,
    ) -> tuple[int, dict[str, object]]:
        environment_names = (
            {"PATH"}
            if inherited_environment_names is None
            else inherited_environment_names
        )
        with mock.patch.object(
            behavior_eval,
            "_process_environment_names",
            return_value=environment_names,
        ):
            return behavior_eval.run_case(
                case,
                source,
                skill,
                [sys.executable, str(runner)],
                root / "workspace",
                validation_isolation="unsafe-direct",
                runner_isolation="unsafe-direct",
            )

    def short_case_timeout(
        self, case: Path, seconds: float
    ) -> AbstractContextManager:
        parsed = behavior_eval._load_case(case)
        parsed["timeout_seconds"] = seconds
        return mock.patch.object(behavior_eval, "_load_case", return_value=parsed)

    def assert_process_stopped(self, pid: int, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        status_path = Path("/proc") / str(pid) / "stat"
        while time.monotonic() < deadline:
            try:
                state = status_path.read_text(encoding="utf-8").split()[2]
            except (FileNotFoundError, ProcessLookupError):
                return
            if state == "Z":
                return
            time.sleep(0.01)
        self.fail(f"process {pid} remained alive after its process group was stopped")

    def test_valid_runner_and_scoped_change_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            runner = self.write_runner(
                root,
                "import subprocess\n"
                "from pathlib import Path\n"
                "subprocess.run(['git', 'status', '--short'], check=True)\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "subprocess.run(['git', 'diff', '--check'], check=True)\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            code, report = self.run_fixture(root, source, skill, case, runner)
        self.assertEqual(code, 0, report)
        self.assertTrue(report["valid"])
        self.assertEqual(report["changed_paths"], ["target.txt"])
        self.assertTrue(all(report["repository_integrity"].values()))
        self.assertIn("not independent proof", report["result_meaning"])

    def test_negated_acceptance_text_does_not_pass_exact_line_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            runner = self.write_runner(
                root,
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "print('I could not reach ACCEPTED_PORTABLE')\n",
            )
            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertIn(
            "runner output did not contain exact line 'ACCEPTED_PORTABLE'",
            report["errors"],
        )
        self.assertFalse(report["runner_claimed_line_checks"][0]["present"])

    def test_unexpected_change_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            runner = self.write_runner(
                root,
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "Path('extra.txt').write_text('unexpected\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            code, report = self.run_fixture(root, source, skill, case, runner)
        self.assertEqual(code, 1)
        self.assertIn(
            "unexpected changed paths (1); opaque IDs:", report["errors"][0]
        )
        self.assertEqual(report["unexpected_changed_path_count"], 1)
        self.assertEqual(
            report["unexpected_changed_path_ids"],
            [behavior_eval._opaque_path_identifier("extra.txt")],
        )

    def test_runner_controlled_filename_is_opaque_in_report(self) -> None:
        secret = "sk-restricted-project-secret-value"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            runner = self.write_runner(
                root,
                "import os\n"
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "Path(os.environ['OPENAI_API_KEY']).write_text('leak attempt')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": secret}):
                code, report = self.run_fixture(root, source, skill, case, runner)

        rendered = json.dumps(report, ensure_ascii=True, sort_keys=True)
        self.assertEqual(code, 1)
        self.assertNotIn(secret, rendered)
        self.assertEqual(report["unexpected_changed_path_count"], 1)
        self.assertEqual(
            report["unexpected_changed_path_ids"],
            [behavior_eval._opaque_path_identifier(secret)],
        )

    def test_runner_created_fifo_fails_closed_without_hanging(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs are not available")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            self.write_case(case, validation_commands=[])
            runner = self.write_runner(
                root,
                "import os\n"
                "os.unlink('target.txt')\n"
                "os.mkfifo('target.txt')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            with fail_if_call_blocks(), self.assertRaisesRegex(
                behavior_eval.EvalError,
                "post-run workspace budget check failed",
            ):
                self.run_fixture(root, source, skill, case, runner)

    def test_low_level_runner_filename_error_does_not_leak_in_report(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs are not available")
        secret = "sk-low-level-secret-filename"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            self.write_case(case, validation_commands=[])
            runner = self.write_runner(
                root,
                "import os\n"
                "os.mkfifo(os.environ['OPENAI_API_KEY'])\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            arguments = [
                "--case",
                str(case),
                "--source-root",
                str(source),
                "--skill-path",
                str(skill),
                "--workspace",
                str(root / "workspace"),
                "--runner-json",
                json.dumps([sys.executable, str(runner)]),
                "--runner-isolation",
                "unsafe-direct",
                "--validation-isolation",
                "unsafe-direct",
            ]
            with mock.patch.dict(
                os.environ, {"OPENAI_API_KEY": secret}
            ), mock.patch("builtins.print") as output, fail_if_call_blocks():
                code = behavior_eval.main(arguments)

        rendered = output.call_args.args[0]
        self.assertEqual(code, 2)
        self.assertNotIn(secret, rendered)
        report = json.loads(rendered)
        self.assertEqual(
            report["errors"],
            [
                "post-run workspace budget check failed while inspecting "
                "runner-controlled workspace"
            ],
        )

    def test_validation_cannot_supply_the_required_runner_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            validation = [
                sys.executable,
                "-c",
                "open('target.txt', 'w').write('after\\n')",
            ]
            self.write_case(case, validation_commands=[validation])
            runner = self.write_runner(root, "print('ACCEPTED_PORTABLE')\n")

            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertIn(
            "required paths were not changed: target.txt", report["errors"]
        )
        self.assertIn(
            "validation changed workspace paths (1); opaque IDs:",
            report["errors"][1],
        )
        self.assertEqual(report["validation_changed_path_count"], 1)

    def test_validation_cannot_rewrite_the_runner_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            validation = [
                sys.executable,
                "-c",
                "open('target.txt', 'w').write('validation rewrite\\n')",
            ]
            self.write_case(case, validation_commands=[validation])
            runner = self.write_runner(
                root,
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )

            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertEqual(report["changed_paths"], ["target.txt"])
        self.assertIn(
            "validation changed workspace paths (1); opaque IDs:",
            report["errors"][0],
        )

    def test_touch_only_does_not_satisfy_a_required_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            self.write_case(case, validation_commands=[])
            runner = self.write_runner(
                root,
                "import os\n"
                "os.utime('target.txt', ns=(1700000000000000000, 1700000000000000000))\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertIn("required paths were not changed: target.txt", report["errors"])
        self.assertNotIn("target.txt", report["changed_paths"])
        self.assertIn("target.txt", report["observed_changed_paths"])

    def test_chmod_only_does_not_satisfy_a_required_file_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            self.write_case(case, validation_commands=[])
            runner = self.write_runner(
                root,
                "import os\n"
                "os.chmod('target.txt', 0o755)\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertIn("required paths were not changed: target.txt", report["errors"])
        self.assertNotIn("target.txt", report["changed_paths"])
        self.assertIn("target.txt", report["observed_changed_paths"])

    def test_out_of_scope_file_xattr_change_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.require_user_xattrs(root)
            source, skill, case = self.make_fixture(root)
            (source / "other.txt").write_text("unchanged\n", encoding="utf-8")
            self.run_git(source, "add", "other.txt")
            self.run_git(source, "commit", "-qm", "add out-of-scope file")
            runner = self.write_runner(
                root,
                "import os\n"
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "os.setxattr('other.txt', b'user.cdw', b'x')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )

            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertEqual(report["unexpected_changed_path_count"], 1)
        self.assertIn(
            behavior_eval._opaque_path_identifier("other.txt"),
            report["observed_changed_paths"],
        )

    def test_xattr_only_change_does_not_satisfy_required_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.require_user_xattrs(root)
            source, skill, case = self.make_fixture(root)
            self.write_case(case, validation_commands=[])
            runner = self.write_runner(
                root,
                "import os\n"
                "os.setxattr('target.txt', b'user.cdw', b'x')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )

            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertIn(
            "required paths were not changed: target.txt", report["errors"]
        )
        self.assertNotIn("target.txt", report["changed_paths"])
        self.assertIn("target.txt", report["observed_changed_paths"])

    def test_git_metadata_xattr_change_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.require_user_xattrs(root)
            source, skill, case = self.make_fixture(root)
            runner = self.write_runner(
                root,
                "import os\n"
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "os.setxattr('.git/config', b'user.cdw', b'x')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )

            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertIn("Git control metadata changed", report["errors"])

    def test_hardlink_topology_change_is_detected_but_not_meaningful(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("identical\n", encoding="utf-8")
            second.write_text("identical\n", encoding="utf-8")
            timestamp = 1_700_000_000_000_000_000
            os.utime(first, ns=(timestamp, timestamp))
            os.utime(second, ns=(timestamp, timestamp))
            baseline = behavior_eval._filesystem_inventory(
                root, exclude_git=False, exclude_caches=False
            )

            second.unlink()
            os.link(first, second)
            after = behavior_eval._filesystem_inventory(
                root, exclude_git=False, exclude_caches=False
            )

        self.assertEqual(
            behavior_eval._inventory_changes(baseline, after),
            {"first.txt", "second.txt"},
        )
        self.assertEqual(
            behavior_eval._meaningful_inventory_changes(baseline, after), set()
        )

        if not hasattr(os, "symlink"):
            return
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first-link"
            second = root / "second-link"
            os.symlink("target", first)
            os.symlink("target", second)
            timestamp = 1_700_000_000_000_000_000
            os.utime(first, ns=(timestamp, timestamp), follow_symlinks=False)
            os.utime(second, ns=(timestamp, timestamp), follow_symlinks=False)
            baseline = behavior_eval._filesystem_inventory(
                root, exclude_git=False, exclude_caches=False
            )

            second.unlink()
            os.link(first, second, follow_symlinks=False)
            after = behavior_eval._filesystem_inventory(
                root, exclude_git=False, exclude_caches=False
            )

        self.assertEqual(
            behavior_eval._inventory_changes(baseline, after),
            {"first-link", "second-link"},
        )
        self.assertEqual(
            behavior_eval._meaningful_inventory_changes(baseline, after), set()
        )

    def test_directory_replacement_is_detected_but_not_meaningful(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "inventory"
            package = root / "pkg"
            package.mkdir(parents=True)
            kept = package / "keep.txt"
            kept.write_text("unchanged\n", encoding="utf-8")
            baseline = behavior_eval._filesystem_inventory(
                root, exclude_git=False, exclude_caches=False
            )

            saved = parent / "saved-pkg"
            package.rename(saved)
            package.mkdir(mode=stat.S_IMODE(saved.stat().st_mode))
            (saved / "keep.txt").rename(package / "keep.txt")
            saved.rmdir()
            after = behavior_eval._filesystem_inventory(
                root, exclude_git=False, exclude_caches=False
            )

        self.assertEqual(
            behavior_eval._inventory_changes(baseline, after), {"pkg"}
        )
        self.assertEqual(
            behavior_eval._meaningful_inventory_changes(baseline, after), set()
        )

    def test_ignored_source_secret_is_not_copied_and_ignored_change_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            runner = self.write_runner(
                root,
                "from pathlib import Path\n"
                "assert not Path('.secret').exists()\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "Path('.secret').write_text('new secret\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            code, report = self.run_fixture(root, source, skill, case, runner)
        self.assertEqual(code, 1)
        self.assertIn("unexpected changed paths (1); opaque IDs:", report["errors"][0])
        self.assertIn(
            behavior_eval._opaque_path_identifier(".secret"),
            report["observed_changed_paths"],
        )

    def test_tracked_cache_file_change_is_not_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            tracked_cache = source / "htmlcov" / "tracked.html"
            tracked_cache.parent.mkdir()
            tracked_cache.write_text("before\n", encoding="utf-8")
            self.run_git(source, "add", "htmlcov/tracked.html")
            self.run_git(source, "commit", "-qm", "track cache-named fixture")
            runner = self.write_runner(
                root,
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "Path('htmlcov/tracked.html').write_text('after\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            code, report = self.run_fixture(root, source, skill, case, runner)

        self.assertEqual(code, 1)
        self.assertIn("unexpected changed paths (1); opaque IDs:", report["errors"][0])
        self.assertIn(
            behavior_eval._opaque_path_identifier("htmlcov/tracked.html"),
            report["observed_changed_paths"],
        )
        self.assertIn(
            "htmlcov/tracked.html",
            report["workspace_inventory"]["included_tracked_cache_paths"],
        )

    def test_tracked_symlink_is_rejected(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are not available")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            os.symlink("target.txt", source / "tracked-link")
            self.run_git(source, "add", "tracked-link")
            self.run_git(source, "commit", "-qm", "add tracked symlink")
            runner = self.write_runner(root, "print('ACCEPTED_PORTABLE')\n")
            with self.assertRaisesRegex(
                behavior_eval.EvalError, "tracked path is not a regular file"
            ):
                self.run_fixture(root, source, skill, case, runner)

    def test_behavior_case_must_be_a_bounded_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            regular = root / "case.json"
            self.write_case(regular)

            symlink = root / "case-link.json"
            os.symlink(regular.name, symlink)
            with self.assertRaisesRegex(
                behavior_eval.EvalError, "cannot safely open behavior case"
            ):
                behavior_eval._load_case(symlink)

            fifo = root / "case.fifo"
            os.mkfifo(fifo)
            with fail_if_call_blocks(), self.assertRaisesRegex(
                behavior_eval.EvalError, "must be a regular file"
            ):
                behavior_eval._load_case(fifo)

            oversized = root / "oversized.json"
            with oversized.open("wb") as stream:
                stream.truncate(behavior_eval.MAX_CASE_BYTES + 1)
            with self.assertRaisesRegex(behavior_eval.EvalError, "size limit"):
                behavior_eval._load_case(oversized)

    def test_behavior_case_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary) / "case.json"
            self.write_case(case)
            rendered = case.read_text(encoding="utf-8").replace(
                '"id": "fixture"',
                '"id": "first", "id": "fixture"',
                1,
            )
            case.write_text(rendered, encoding="utf-8")

            with self.assertRaisesRegex(behavior_eval.EvalError, "duplicate key"):
                behavior_eval._load_case(case)

    def test_behavior_case_rejects_excessive_nesting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary) / "case.json"
            depth = behavior_eval.MAX_CASE_NESTING + 1
            case.write_text("[" * depth + "0" + "]" * depth, encoding="utf-8")

            with self.assertRaisesRegex(behavior_eval.EvalError, "nesting limit"):
                behavior_eval._load_case(case)

            deeper_than_the_json_decoder = 2_000
            case.write_text(
                "[" * deeper_than_the_json_decoder
                + "0"
                + "]" * deeper_than_the_json_decoder,
                encoding="utf-8",
            )
            with mock.patch("builtins.print") as output:
                code = behavior_eval.main(
                    ["--case", str(case), "--runner-json", '["runner"]']
                )

        self.assertEqual(code, 2)
        report = json.loads(output.call_args.args[0])
        self.assertEqual(report["errors"], ["behavior case exceeds the nesting limit"])

    def test_behavior_case_rejects_nul_and_surrogate_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case = Path(temporary) / "case.json"
            self.write_case(case)
            baseline = json.loads(case.read_text(encoding="utf-8"))
            malformed_values = {
                "id": "fixture\0",
                "prompt": "prompt\ud800",
                "allowed_changed_paths": ["target.txt\0"],
                "required_changed_paths": ["target.txt\udfff"],
                "required_output_lines": ["ACCEPTED\ud800"],
                "validation_commands": [["python3", "argument\0"]],
            }

            for field, malformed in malformed_values.items():
                with self.subTest(field=field):
                    payload = dict(baseline)
                    payload[field] = malformed
                    case.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(
                        behavior_eval.EvalError, "NUL or surrogate"
                    ):
                        behavior_eval._load_case(case)

    def test_subprocess_bound_inputs_reject_nul_and_surrogates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            with self.assertRaisesRegex(
                behavior_eval.EvalError, "runner argument.*NUL or surrogate"
            ):
                behavior_eval.run_case(
                    case,
                    source,
                    skill,
                    ["runner\0"],
                    root / "workspace",
                    validation_isolation="unsafe-direct",
                    runner_isolation="unsafe-direct",
                )

        with self.assertRaisesRegex(
            behavior_eval.EvalError, "Docker image name.*NUL or surrogate"
        ):
            behavior_eval._validate_docker_image("image\ud800")
        with self.assertRaisesRegex(
            behavior_eval.EvalError, "Docker container name.*NUL or surrogate"
        ):
            behavior_eval._validate_docker_container_name("container\0")
        with self.assertRaisesRegex(
            behavior_eval.EvalError, "validation command.*NUL or surrogate"
        ):
            behavior_eval._docker_validation_argv(
                VALIDATION_IMAGE_ID,
                Path("/tmp/workspace"),
                ["command", "argument\ud800"],
                "container-name",
            )
        with self.assertRaisesRegex(
            behavior_eval.EvalError, "runner prompt.*NUL or surrogate"
        ):
            behavior_eval._docker_runner_argv(
                RUNNER_IMAGE_ID,
                Path("/tmp/workspace"),
                ["runner"],
                "prompt\0",
                "container-name",
                "/workspace",
            )

    def test_tracked_file_count_and_setup_deadline_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _skill, _case = self.make_fixture(root)
            with mock.patch.object(behavior_eval, "MAX_INVENTORY_ENTRIES", 1):
                with self.assertRaisesRegex(
                    behavior_eval.EvalError, "too many tracked files"
                ):
                    behavior_eval._tracked_regular_files(source)

            workspace = root / "expired-workspace"
            with self.assertRaisesRegex(
                behavior_eval._CaseDeadlineExpired,
                "case deadline expired during workspace preparation",
            ):
                behavior_eval._prepare_workspace(
                    source, workspace, time.monotonic() - 1
                )
            self.assertFalse(workspace.exists())

    def test_post_run_inventory_honors_expired_case_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "file.txt").write_text("data\n", encoding="utf-8")
            with self.assertRaisesRegex(
                behavior_eval._CaseDeadlineExpired, "case deadline expired"
            ):
                behavior_eval._filesystem_inventory(
                    root,
                    exclude_git=True,
                    exclude_caches=True,
                    deadline=time.monotonic() - 1,
                )

    def test_workspace_budget_counts_excluded_cache_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "large.pyc").write_bytes(b"x" * 32)
            with mock.patch.object(behavior_eval, "MAX_INVENTORY_TOTAL_BYTES", 16):
                with self.assertRaisesRegex(
                    behavior_eval.EvalError, "total size limit"
                ):
                    behavior_eval._enforce_workspace_budget(
                        root, time.monotonic() + 10, "test workspace budget"
                    )

    def test_tracked_file_cannot_be_redirected_through_a_parent_symlink(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are not available")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            nested = source / "nested"
            nested.mkdir()
            (nested / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            self.run_git(source, "add", "nested/tracked.txt")
            self.run_git(source, "commit", "-qm", "add nested file")
            nested.rename(source / "original-nested")
            external = root / "external"
            external.mkdir()
            (external / "tracked.txt").write_text("outside\n", encoding="utf-8")
            os.symlink(external, nested)
            runner = self.write_runner(root, "print('ACCEPTED_PORTABLE')\n")
            with self.assertRaisesRegex(
                behavior_eval.EvalError, "cannot safely open tracked file"
            ):
                self.run_fixture(root, source, skill, case, runner)

    def test_workspace_equal_to_or_below_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            runner = self.write_runner(root, "print('ACCEPTED_PORTABLE')\n")
            for workspace in (source, source / "nested-workspace"):
                with self.subTest(workspace=workspace), self.assertRaisesRegex(
                    behavior_eval.EvalError, "workspace must be outside the source tree"
                ):
                    behavior_eval.run_case(
                        case,
                        source,
                        skill,
                        [sys.executable, str(runner)],
                        workspace,
                        validation_isolation="unsafe-direct",
                        runner_isolation="unsafe-direct",
                    )

    def test_runner_cannot_replace_workspace_root_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            decoy = root / "decoy"
            decoy.mkdir()
            workspace = root / "workspace"
            moved = root / "moved-workspace"
            runner = self.write_runner(
                root,
                "import os\n"
                f"os.rename({str(workspace)!r}, {str(moved)!r})\n"
                f"os.symlink({str(decoy)!r}, {str(workspace)!r})\n"
                "print('ACCEPTED_PORTABLE')\n",
            )

            with mock.patch.object(
                behavior_eval, "_process_environment_names", return_value={"PATH"}
            ), mock.patch.object(
                behavior_eval, "_run_validation_commands"
            ) as validation, self.assertRaisesRegex(
                behavior_eval.EvalError, "workspace root"
            ):
                behavior_eval.run_case(
                    case,
                    source,
                    skill,
                    [sys.executable, str(runner)],
                    workspace,
                    validation_isolation="unsafe-direct",
                    runner_isolation="unsafe-direct",
                )
            validation.assert_not_called()

    def test_runner_cannot_relocate_git_directory_into_excluded_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            runner = self.write_runner(
                root,
                "import os\n"
                "from pathlib import Path\n"
                "Path('__pycache__').mkdir()\n"
                "os.rename('.git', '__pycache__/.git')\n"
                "os.symlink('__pycache__/.git', '.git')\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )

            with mock.patch.object(
                behavior_eval, "_process_environment_names", return_value={"PATH"}
            ), mock.patch.object(
                behavior_eval, "_run_validation_commands"
            ) as validation, self.assertRaisesRegex(
                behavior_eval.EvalError, "Git directory"
            ):
                behavior_eval.run_case(
                    case,
                    source,
                    skill,
                    [sys.executable, str(runner)],
                    root / "workspace",
                    validation_isolation="unsafe-direct",
                    runner_isolation="unsafe-direct",
                )
            validation.assert_not_called()

    def test_docker_runner_requires_nonempty_api_key_before_workspace_setup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _skill, case = self.make_fixture(root)
            workspace = root / "workspace"
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(
                behavior_eval.EvalError, "nonempty OPENAI_API_KEY"
            ):
                behavior_eval.run_case(
                    case,
                    source,
                    source,
                    ["codex", "exec"],
                    workspace,
                    validation_isolation="unsafe-direct",
                    runner_isolation="docker",
                    runner_image=RUNNER_IMAGE_ID,
                    runner_tool_version="codex-cli 0.154.0",
                )

            self.assertFalse(workspace.exists())

    def test_commit_ref_and_index_mutations_fail_repository_checks(self) -> None:
        mutations = {
            "commit": (
                "import subprocess\n"
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "subprocess.run(['git', 'add', 'target.txt'], check=True)\n"
                "subprocess.run(['git', 'commit', '-qm', 'unauthorized'], check=True)\n"
                "print('ACCEPTED_PORTABLE')\n",
                "Git HEAD changed",
            ),
            "ref": (
                "import subprocess\n"
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "subprocess.run(['git', 'branch', 'unauthorized'], check=True)\n"
                "print('ACCEPTED_PORTABLE')\n",
                "Git refs changed",
            ),
            "index": (
                "import subprocess\n"
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "subprocess.run(['git', 'add', 'target.txt'], check=True)\n"
                "print('ACCEPTED_PORTABLE')\n",
                "Git index changed",
            ),
        }
        for name, (script, expected_error) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, skill, case = self.make_fixture(root)
                runner = self.write_runner(root, script)
                code, report = self.run_fixture(root, source, skill, case, runner)
                self.assertEqual(code, 1)
                self.assertIn(expected_error, report["errors"])

    def test_repository_inspection_errors_never_compare_as_unchanged(self) -> None:
        failed_state = {"error": "same bounded failure"}
        checks = behavior_eval._repository_checks(
            failed_state, failed_state.copy(), failed_state.copy()
        )
        self.assertFalse(any(checks.values()))

        observation_failure = {
            "head": {"exit_code": None, "error": "OSError"},
            "head_ref": {"exit_code": None, "error": "OSError"},
            "refs": {"exit_code": None, "error": "OSError"},
            "index": "missing",
            "metadata_sha256": "same",
        }
        checks = behavior_eval._repository_checks(
            observation_failure,
            observation_failure.copy(),
            observation_failure.copy(),
        )
        self.assertFalse(any(checks.values()))

        with mock.patch.object(
            behavior_eval,
            "_filesystem_inventory",
            side_effect=behavior_eval.EvalError("metadata failure"),
        ), self.assertRaisesRegex(behavior_eval.EvalError, "metadata failure"):
            behavior_eval._repository_state(Path("/workspace"))

    def test_timeout_kills_runner_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            self.write_case(case, validation_commands=[], timeout_seconds=1)
            marker = root / "descendant-marker"
            pid_file = root / "descendant-pid"
            child_code = (
                "import pathlib,time; time.sleep(10); "
                f"pathlib.Path({str(marker)!r}).write_text('alive')"
            )
            runner = self.write_runner(
                root,
                "import pathlib,subprocess,sys,time\n"
                f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
                "time.sleep(10)\n",
            )
            with self.short_case_timeout(case, 0.5), self.assertRaisesRegex(
                behavior_eval._CaseDeadlineExpired, "case deadline expired"
            ):
                self.run_fixture(root, source, skill, case, runner)
            self.assertTrue(pid_file.is_file(), "runner did not record its child PID")
            self.assert_process_stopped(int(pid_file.read_text(encoding="utf-8")))
            self.assertFalse(marker.exists())

    def test_runner_output_is_stopped_at_hard_combined_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            self.write_case(case, validation_commands=[])
            runner = self.write_runner(
                root,
                "import os\n"
                f"data = b'x' * ({behavior_eval.MAX_RUNNER_OUTPUT} + 1)\n"
                "os.write(1, data)\n",
            )
            code, report = self.run_fixture(root, source, skill, case, runner)
        self.assertEqual(code, 1)
        self.assertIn("runner output exceeded the evaluation limit", report["errors"])
        self.assertLessEqual(report["runner_output_bytes"], behavior_eval.MAX_RUNNER_OUTPUT)
        self.assertEqual(report["runner_termination"], "output_limit")

    def test_runner_and_validation_commands_share_one_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            sleeper = [sys.executable, "-c", "import time; time.sleep(0.35)"]
            self.write_case(
                case,
                validation_commands=[sleeper, sleeper],
                timeout_seconds=1,
            )
            runner = self.write_runner(
                root,
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            with self.short_case_timeout(case, 0.5), self.assertRaisesRegex(
                behavior_eval._CaseDeadlineExpired, "case deadline expired"
            ):
                self.run_fixture(root, source, skill, case, runner)

    def test_direct_validation_fails_closed_with_inherited_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            validation = [
                sys.executable,
                "-c",
                (
                    "import os,pathlib; "
                    "pathlib.Path('validation-ran').write_text('unsafe'); "
                    "assert open('target.txt').read() == 'after\\n'"
                ),
            ]
            self.write_case(case, validation_commands=[validation])
            runner = self.write_runner(
                root,
                "import os\n"
                "from pathlib import Path\n"
                "assert os.environ['OPENAI_API_KEY'] == 'runner-secret'\n"
                "assert os.environ['CODEX_HOME'] == '/private/codex'\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "runner-secret",
                    "CODEX_HOME": "/private/codex",
                },
            ):
                code, report = self.run_fixture(
                    root,
                    source,
                    skill,
                    case,
                    runner,
                    inherited_environment_names={
                        "AWS_SECRET_ACCESS_KEY",
                        "GENERIC_SERVICE_TOKEN",
                        "HARMLESS_METADATA",
                        "OPENAI_API_KEY",
                        "PATH",
                        "SSH_AUTH_SOCK",
                    },
                )
        self.assertEqual(code, 1, report)
        self.assertEqual(report["runner_exit_code"], 0)
        self.assertEqual(
            report["validation_checks"][0]["status"],
            "not_run_unsafe_parent_environment",
        )
        self.assertIn(
            "unsafe direct validation refused", "\n".join(report["errors"])
        )
        detected = report[
            "validation_inherited_environment_names_outside_allowlist"
        ]
        self.assertIn("AWS_SECRET_ACCESS_KEY", detected)
        self.assertIn("GENERIC_SERVICE_TOKEN", detected)
        self.assertIn("HARMLESS_METADATA", detected)
        self.assertIn("OPENAI_API_KEY", detected)
        self.assertIn("SSH_AUTH_SOCK", detected)
        self.assertEqual(
            report["validation_isolation"]["inherited_environment_name_gate"],
            "blocked",
        )
        self.assertFalse((root / "workspace" / "validation-ran").exists())
        self.assertTrue(report["validation_home_isolated"])

    def test_report_publication_is_atomic_and_never_clobbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report.json"
            behavior_eval._publish_report(report, '{"valid": true}\n')
            self.assertEqual(report.read_text(encoding="utf-8"), '{"valid": true}\n')

            with self.assertRaisesRegex(behavior_eval.EvalError, "already exists"):
                behavior_eval._publish_report(report, "replacement\n")
            self.assertEqual(report.read_text(encoding="utf-8"), '{"valid": true}\n')

            target = root / "target.txt"
            target.write_text("sentinel\n", encoding="utf-8")
            symlink = root / "symlink-report.json"
            os.symlink(target.name, symlink)
            with self.assertRaisesRegex(behavior_eval.EvalError, "already exists"):
                behavior_eval._publish_report(symlink, "replacement\n")
            self.assertEqual(target.read_text(encoding="utf-8"), "sentinel\n")

    def test_main_publishes_a_no_clobber_report_digest_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "report.json"
            marker = root / "published.sha256"
            fixed_report = {
                "version": "skill-behavior-report-v1",
                "valid": False,
                "errors": ["expected fixture failure"],
            }
            with mock.patch.object(
                behavior_eval, "run_case", return_value=(1, fixed_report)
            ), mock.patch("builtins.print"):
                code = behavior_eval.main(
                    [
                        "--case",
                        "case.json",
                        "--source-root",
                        str(REPOSITORY_ROOT),
                        "--skill-path",
                        str(REPOSITORY_ROOT),
                        "--runner-json",
                        '["runner"]',
                        "--workspace",
                        str(root / "workspace"),
                        "--report",
                        str(report),
                        "--publication-marker",
                        str(marker),
                    ]
                )

            report_bytes = report.read_bytes()
            marker_text = marker.read_text(encoding="ascii")

        self.assertEqual(code, 1)
        self.assertEqual(
            marker_text,
            hashlib.sha256(report_bytes).hexdigest() + "\n",
        )

    def test_publication_marker_requires_report_and_never_clobbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "published.sha256"
            marker.write_text("stale\n", encoding="ascii")
            report = root / "report.json"
            fixed_report = {
                "version": "skill-behavior-report-v1",
                "valid": True,
                "errors": [],
            }
            with mock.patch.object(
                behavior_eval, "run_case", return_value=(0, fixed_report)
            ), mock.patch("builtins.print"):
                code = behavior_eval.main(
                    [
                        "--case",
                        "case.json",
                        "--source-root",
                        str(REPOSITORY_ROOT),
                        "--skill-path",
                        str(REPOSITORY_ROOT),
                        "--runner-json",
                        '["runner"]',
                        "--workspace",
                        str(root / "workspace"),
                        "--report",
                        str(report),
                        "--publication-marker",
                        str(marker),
                    ]
                )

            stale_marker = marker.read_text(encoding="ascii")
            report_was_published = report.exists()

        self.assertEqual(code, 2)
        self.assertTrue(report_was_published)
        self.assertEqual(stale_marker, "stale\n")

        with mock.patch("builtins.print") as output:
            code = behavior_eval.main(
                [
                    "--case",
                    "case.json",
                    "--runner-json",
                    '["runner"]',
                    "--publication-marker",
                    "/tmp/marker",
                ]
            )
        self.assertEqual(code, 2)
        self.assertIn(
            "--publication-marker requires --report",
            json.loads(output.call_args.args[0])["errors"],
        )

    def test_report_path_must_be_outside_source_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            workspace = root / "workspace"
            output = root / "output"
            source.mkdir()
            workspace.mkdir()
            output.mkdir()

            for report in (source / "report.json", workspace / "report.json"):
                with self.subTest(report=report), self.assertRaisesRegex(
                    behavior_eval.EvalError, "must be outside"
                ):
                    behavior_eval._validated_report_path(report, source, workspace)
            self.assertEqual(
                behavior_eval._validated_report_path(
                    output / "report.json", source, workspace
                ),
                output / "report.json",
            )

    def test_minimal_validation_environment_omits_generic_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ,
            {
                "AWS_SECRET_ACCESS_KEY": "aws-secret",
                "GENERIC_SERVICE_TOKEN": "service-secret",
                "OPENAI_API_KEY": "openai-secret",
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "BASH_ENV": "/tmp/startup-hook",
            },
            clear=False,
        ):
            home = Path(temporary)
            environment = behavior_eval._minimal_validation_environment(home)

        for name in (
            "AWS_SECRET_ACCESS_KEY",
            "GENERIC_SERVICE_TOKEN",
            "OPENAI_API_KEY",
            "SSH_AUTH_SOCK",
            "BASH_ENV",
        ):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["HOME"], str(home))
        self.assertEqual(environment["PATH"], os.defpath)

    def test_direct_validation_ignores_inherited_path_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            marker = root / "fake-python-ran"
            fake_python = fake_bin / "python3"
            fake_python.write_text(
                "#!/bin/sh\n" + f"touch {str(marker)!r}\n" + "exit 99\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            self.write_case(
                case,
                validation_commands=[
                    [
                        "python3",
                        "-c",
                        "assert open('target.txt').read() == 'after\\n'",
                    ]
                ],
            )
            runner = self.write_runner(
                root,
                "from pathlib import Path\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )

            with mock.patch.dict(os.environ, {"PATH": str(fake_bin)}):
                code, report = self.run_fixture(
                    root, source, skill, case, runner
                )

        self.assertEqual(code, 0, report)
        self.assertFalse(marker.exists())

    def test_docker_validation_argv_enforces_the_declared_boundary(self) -> None:
        workspace = Path("/tmp/cdw validation workspace")
        command = ["python3", "-c", "print('checked')"]
        argv = behavior_eval._docker_validation_argv(
            VALIDATION_IMAGE_ID,
            workspace,
            command,
            "cdw-validation-test",
        )

        self.assertEqual(argv[:3], ["docker", "container", "create"])
        self.assertNotIn("--rm", argv)
        self.assertIn(
            "--label=com.openai.cdw.behavior-container=cdw-validation-test",
            argv,
        )
        self.assertIn("--network=none", argv)
        self.assertFalse(
            any(item == "--pid" or item.startswith("--pid=") for item in argv)
        )
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop=ALL", argv)
        self.assertIn("--security-opt=no-new-privileges=true", argv)
        for option in (
            "--memory=1073741824",
            "--memory-swap=1073741824",
            "--cpus=2",
            "--pids-limit=256",
            "--ulimit=nofile=1024:1024",
            "--ulimit=core=0:0",
        ):
            self.assertIn(option, argv)
        self.assertIn(f"--user={os.getuid()}:{os.getgid()}", argv)
        self.assertIn(
            "--mount=type=bind,source=/tmp/cdw validation workspace,target=/workspace",
            argv,
        )
        self.assertEqual(sum(item.startswith("--mount=") for item in argv), 1)
        self.assertFalse(any("docker.sock" in item for item in argv))
        self.assertIn("--entrypoint=/usr/bin/env", argv)
        image_index = argv.index(VALIDATION_IMAGE_ID)
        self.assertEqual(argv[image_index + 1], "-i")
        self.assertEqual(argv[-len(command) :], command)

        isolation = behavior_eval._docker_validation_isolation(
            VALIDATION_IMAGE_ID, []
        )
        self.assertFalse(isolation["credential_environment_passed"])
        self.assertTrue(
            isolation["runner_workspace_credential_transfer_possible"]
        )
        self.assertIn(
            "runner_workspace_may_contain_credentials",
            isolation["environment_policy"],
        )

    def test_docker_runner_argv_enforces_lifecycle_and_skill_setup(self) -> None:
        workspace = Path("/tmp/cdw runner workspace")
        runner = ["codex", "exec", "--ephemeral"]
        prompt = "Use /workspace/SKILL.md"
        argv = behavior_eval._docker_runner_argv(
            RUNNER_IMAGE_ID,
            workspace,
            runner,
            prompt,
            "cdw-runner-test",
            "/workspace",
        )

        self.assertEqual(argv[:3], ["docker", "container", "create"])
        self.assertNotIn("--rm", argv)
        self.assertIn(
            "--label=com.openai.cdw.behavior-container=cdw-runner-test",
            argv,
        )
        self.assertIn("--network=bridge", argv)
        self.assertFalse(
            any(item == "--pid" or item.startswith("--pid=") for item in argv)
        )
        for option in (
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges=true",
            "--memory=2147483648",
            "--memory-swap=2147483648",
            "--cpus=2",
        ):
            self.assertIn(option, argv)
        self.assertIn(f"--user={os.getuid()}:{os.getgid()}", argv)
        self.assertEqual(sum(item.startswith("--mount=") for item in argv), 1)
        self.assertFalse(any("docker.sock" in item for item in argv))
        self.assertIn("--env=OPENAI_API_KEY", argv)
        self.assertFalse(any("OPENAI_API_KEY=" in item for item in argv))
        self.assertIn("--env=GIT_OPTIONAL_LOCKS=0", argv)
        self.assertIn("--env=CDW_EVAL_SKILL_PATH=/workspace", argv)
        self.assertIn(
            "--env=CDW_EVAL_SKILL_NAME=collaborative-development-workflow", argv
        )
        image_index = argv.index(RUNNER_IMAGE_ID)
        self.assertEqual(argv[image_index + 1 :], [*runner, prompt])

        workflow = (REPOSITORY_ROOT / ".github/workflows/behavior-eval.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "COPY assets/cdw-reviewer.toml /opt/cdw-runner/cdw-reviewer.toml",
            workflow,
        )
        self.assertIn(
            "cp /opt/cdw-runner/cdw-reviewer.toml "
            '"$CODEX_HOME/agents/cdw-reviewer.toml"',
            workflow,
        )
        self.assertIn(
            'ln -s "$CDW_EVAL_SKILL_PATH" '
            '"$HOME/.agents/skills/$CDW_EVAL_SKILL_NAME"',
            workflow,
        )
        self.assertIn('CODEX_NPM_VERSION: "0.154.0"', workflow)
        self.assertIn(
            "python:3.13-slim@sha256:"
            "9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285",
            workflow,
        )
        self.assertIn(
            "node:22-bookworm-slim@sha256:"
            "83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5",
            workflow,
        )
        self.assertIn(
            "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
            workflow,
        )
        self.assertIn(
            "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
            workflow,
        )
        self.assertIn(
            "actions/upload-artifact@"
            "ea165f8d65b6e75b540449e92b4886f43607fa02",
            workflow,
        )
        self.assertIn(
            'npm install --global "@openai/codex@${CODEX_NPM_VERSION}"',
            workflow,
        )
        self.assertIn(
            "VALIDATION_IMAGE_ID=\"$(docker image inspect --format '{{.Id}}' ",
            workflow,
        )
        self.assertIn(
            "RUNNER_IMAGE_ID=\"$(docker image inspect --format '{{.Id}}' ",
            workflow,
        )
        self.assertIn('--runner-image "$RUNNER_IMAGE_ID"', workflow)
        self.assertIn('--runner-tool-version "$OBSERVED_CODEX_VERSION"', workflow)
        self.assertIn('--validation-image "$VALIDATION_IMAGE_ID"', workflow)
        self.assertIn(
            'test -L "$HOME/.agents/skills/$CDW_EVAL_SKILL_NAME"', workflow
        )
        self.assertIn(
            '/usr/bin/mktemp -d "$RUNNER_TEMP/cdw-behavior-report.XXXXXXXX"',
            workflow,
        )
        self.assertIn('--publication-marker "$PUBLICATION_MARKER"', workflow)
        self.assertIn("steps.behavior_eval.outputs.artifact_ready == 'true'", workflow)
        self.assertIn(
            "path: ${{ steps.behavior_eval.outputs.report_path }}", workflow
        )
        self.assertNotIn("$RUNNER_TEMP/behavior-eval-report.json", workflow)

        isolation = behavior_eval._docker_runner_isolation(
            RUNNER_IMAGE_ID, "/workspace"
        )
        self.assertTrue(isolation["api_credential_passed_to_runner_container"])
        self.assertEqual(
            isolation["credential_access_by_model_controlled_commands"], "possible"
        )
        self.assertFalse(isolation["command_level_credential_isolation"])
        self.assertEqual(
            isolation["network_requested"],
            "docker_bridge_unrestricted_egress",
        )
        self.assertFalse(isolation["api_only_egress_enforced"])
        self.assertTrue(isolation["host_lan_link_local_and_peer_access_possible"])
        self.assertTrue(isolation["requires_disposable_network_isolated_host"])
        self.assertEqual(
            isolation["container_cleanup_race_recheck_seconds"], 30.0
        )
        self.assertEqual(
            isolation["daemon_creation_after_cleanup_window"], "possible"
        )
        self.assertEqual(
            isolation["environment_policy"],
            "container_image_defaults_plus_fixed_values_and_openai_api_key",
        )

    def test_docker_mode_requires_immutable_image_ids_and_observed_version(self) -> None:
        self.assertEqual(
            behavior_eval._validate_docker_image_id(RUNNER_IMAGE_ID),
            RUNNER_IMAGE_ID,
        )
        self.assertEqual(
            behavior_eval._validate_runner_tool_version("codex-cli 0.154.0"),
            "codex-cli 0.154.0",
        )
        for image in (None, "", "cdw-runner:latest", "sha256:" + "g" * 64):
            with self.subTest(image=image), self.assertRaises(
                behavior_eval.EvalError
            ):
                behavior_eval._validate_docker_image_id(image)
        with self.assertRaisesRegex(
            behavior_eval.EvalError, "immutable local sha256 image ID"
        ):
            behavior_eval._docker_validation_argv(
                "mutable:tag", Path("/workspace"), ["python3", "-V"], "name"
            )
        with self.assertRaisesRegex(
            behavior_eval.EvalError, "immutable local sha256 image ID"
        ):
            behavior_eval._docker_runner_argv(
                "mutable:tag",
                Path("/workspace"),
                ["codex", "exec"],
                "prompt",
                "name",
                "/workspace",
            )
        for version in (None, "", " codex-cli 0.154.0", "codex-cli 0.154.0\n"):
            with self.subTest(version=version), self.assertRaises(
                behavior_eval.EvalError
            ):
                behavior_eval._validate_runner_tool_version(version)

    def test_docker_report_records_image_ids_and_observed_tool_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _skill, case = self.make_fixture(root)
            workspace = root / "workspace"

            def run_runner(
                *_args: object, **_kwargs: object
            ) -> subprocess.CompletedProcess[bytes]:
                (workspace / "target.txt").write_text(
                    "after\n", encoding="utf-8"
                )
                return subprocess.CompletedProcess(
                    [], 0, b"ACCEPTED_PORTABLE\n", b""
                )

            validation_result = (
                [
                    {
                        "argv": ["python3", "-c", "pass"],
                        "exit_code": 0,
                        "status": "passed",
                        "output_bytes": 0,
                    }
                ],
                [],
                [],
                behavior_eval._docker_validation_isolation(
                    VALIDATION_IMAGE_ID, []
                ),
                False,
            )
            with mock.patch.dict(
                os.environ, {"OPENAI_API_KEY": "test-only-key"}, clear=True
            ), mock.patch.object(
                behavior_eval, "_run_docker_runner", side_effect=run_runner
            ), mock.patch.object(
                behavior_eval,
                "_run_validation_commands",
                return_value=validation_result,
            ):
                code, report = behavior_eval.run_case(
                    case,
                    source,
                    source,
                    ["codex", "exec"],
                    workspace,
                    validation_isolation="docker",
                    validation_image=VALIDATION_IMAGE_ID,
                    runner_isolation="docker",
                    runner_image=RUNNER_IMAGE_ID,
                    runner_tool_version="codex-cli 0.154.0",
                )

        self.assertEqual(code, 0, report)
        self.assertEqual(
            report["container_image_provenance"],
            {
                "runner_local_image_id": RUNNER_IMAGE_ID,
                "validation_local_image_id": VALIDATION_IMAGE_ID,
                "runner_tool_version_observed_before_secret_step": (
                    "codex-cli 0.154.0"
                ),
                "runner_tool_version_observation_source": (
                    "caller_supplied_pre_secret_image_smoke_check"
                ),
                "base_image_digest_pinning": (
                    "not_independently_verified_by_evaluator"
                ),
            },
        )

    def test_evaluator_git_and_docker_paths_ignore_workspace_path_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _skill, _case = self.make_fixture(root)
            marker = root / "poisoned-command-ran"
            for command in ("git", "docker"):
                fake = source / command
                fake.write_text(
                    "#!/bin/sh\n" + f"touch {str(marker)!r}\n" + "exit 99\n",
                    encoding="utf-8",
                )
                fake.chmod(0o755)

            with mock.patch.dict(os.environ, {"PATH": str(source)}, clear=False):
                behavior_eval._git(source, "status", "--porcelain")
                docker_environment = behavior_eval._docker_client_environment(root)

            self.assertFalse(marker.exists())
            self.assertEqual(docker_environment["PATH"], os.defpath)
            self.assertNotEqual(
                shutil.which("docker", path=docker_environment["PATH"]),
                str(source / "docker"),
            )

    def test_source_fsmonitor_hook_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _skill, _case = self.make_fixture(root)
            marker = root / "fsmonitor-ran"
            hook = root / "fsmonitor"
            hook.write_text(
                "#!/bin/sh\n" + f"touch {str(marker)!r}\n" + "exit 0\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)
            self.run_git(source, "config", "core.fsmonitor", str(hook))

            behavior_eval._tracked_regular_files(source)
            behavior_eval._git(source, "status", "--porcelain")

            self.assertFalse(marker.exists())

    def test_runner_created_fsmonitor_hook_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            marker = root / "runner-fsmonitor-ran"
            hook = root / "runner-fsmonitor"
            hook.write_text(
                "#!/bin/sh\n" + f"touch {str(marker)!r}\n" + "exit 0\n",
                encoding="utf-8",
            )
            hook.chmod(0o755)
            runner = self.write_runner(
                root,
                "import subprocess\n"
                "from pathlib import Path\n"
                f"subprocess.run(['git', 'config', 'core.fsmonitor', {str(hook)!r}], check=True)\n"
                "Path('target.txt').write_text('after\\n', encoding='utf-8')\n"
                "print('ACCEPTED_PORTABLE')\n",
            )

            code, _report = self.run_fixture(root, source, skill, case, runner)

            self.assertEqual(code, 1)
            self.assertFalse(marker.exists())

    def test_container_cleanup_forces_exact_name_and_confirms_absence(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(
            behavior_eval,
            "_run",
            side_effect=[subprocess.CompletedProcess([], 1, b"", b"not found"), completed],
        ) as run:
            behavior_eval._remove_validation_container(
                "cdw-validation-exact", Path("/workspace"), {"PATH": "/bin"}
            )

        self.assertEqual(
            run.call_args_list[0].args[0],
            ["docker", "container", "rm", "--force", "cdw-validation-exact"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "docker",
                "container",
                "ls",
                "--all",
                "--filter",
                "label=com.openai.cdw.behavior-container=cdw-validation-exact",
                "--format",
                "{{.ID}} {{.Names}}",
            ],
        )

    def test_container_cleanup_fails_if_the_name_remains(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        still_present = subprocess.CompletedProcess(
            [], 0, b"cdw-validation-exact\n", b""
        )
        with mock.patch.object(
            behavior_eval, "_run", side_effect=[completed, still_present]
        ), mock.patch.object(
            behavior_eval.time, "monotonic", side_effect=[0.0, 0.0, 76.0, 76.0]
        ), self.assertRaisesRegex(behavior_eval.EvalError, "cannot confirm cleanup"):
            behavior_eval._remove_validation_container(
                "cdw-validation-exact", Path("/workspace"), {"PATH": "/bin"}
            )

    def test_container_cleanup_retries_after_a_late_container_appears(self) -> None:
        completed = subprocess.CompletedProcess([], 0, b"", b"")
        late = subprocess.CompletedProcess([], 0, b"cdw-validation-exact\n", b"")
        with mock.patch.object(
            behavior_eval,
            "_run",
            side_effect=[completed, late, completed, completed],
        ) as run, mock.patch.object(behavior_eval.time, "sleep"):
            behavior_eval._remove_validation_container(
                "cdw-validation-exact", Path("/workspace"), {"PATH": "/bin"}
            )
        self.assertEqual(run.call_count, 4)

    def test_docker_client_failure_still_forces_container_cleanup(self) -> None:
        stopped_errors = (
            behavior_eval._ProcessTimedOut("timed out", b"", b""),
            behavior_eval._ProcessOutputLimit("too much output", b"", b""),
        )
        for stopped_error in stopped_errors:
            with self.subTest(error=type(stopped_error).__name__), mock.patch.object(
                behavior_eval.secrets, "token_hex", return_value="1" * 32
            ), mock.patch.object(
                behavior_eval,
                "_run",
                side_effect=stopped_error,
            ), mock.patch.object(
                behavior_eval, "_remove_validation_container"
            ) as remove, self.assertRaises(type(stopped_error)):
                behavior_eval._run_docker_validation(
                    ["python3", "-V"],
                    Path("/workspace"),
                    1,
                    VALIDATION_IMAGE_ID,
                    {"PATH": "/bin"},
                )

            remove.assert_called_once_with(
                "cdw-validation-" + "1" * 32,
                Path("/workspace"),
                {"PATH": "/bin"},
                settle_seconds=behavior_eval.DOCKER_CLEANUP_RACE_SECONDS,
                container_id=None,
            )

    def test_docker_runner_uses_create_start_and_captured_container_id(self) -> None:
        container_id = "a" * 64
        created = subprocess.CompletedProcess([], 0, (container_id + "\n").encode(), b"")
        started = subprocess.CompletedProcess([], 0, b"runner output", b"")
        client_environment = {"PATH": os.defpath, "OPENAI_API_KEY": "secret"}
        with mock.patch.object(
            behavior_eval.secrets, "token_hex", return_value="3" * 32
        ), mock.patch.object(
            behavior_eval, "_run", side_effect=[created, started]
        ) as run, mock.patch.object(
            behavior_eval, "_remove_validation_container"
        ) as remove:
            result = behavior_eval._run_docker_runner(
                ["codex", "exec"],
                "prompt",
                Path("/workspace"),
                10,
                RUNNER_IMAGE_ID,
                client_environment,
                "/workspace",
            )

        self.assertIs(result, started)
        self.assertEqual(
            run.call_args_list[0].args[0][:3],
            ["docker", "container", "create"],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["docker", "container", "start", "--attach", container_id],
        )
        self.assertNotIn(
            "OPENAI_API_KEY", run.call_args_list[1].kwargs["env"]
        )
        remove.assert_called_once_with(
            "cdw-runner-" + "3" * 32,
            Path("/workspace"),
            {"PATH": os.defpath},
            settle_seconds=0.0,
            container_id=container_id,
        )

    def test_docker_runner_cleanup_does_not_retain_api_key(self) -> None:
        stopped = behavior_eval._ProcessTimedOut("timed out", b"", b"")
        client_environment = {"PATH": os.defpath, "OPENAI_API_KEY": "secret"}
        with mock.patch.object(
            behavior_eval.secrets, "token_hex", return_value="2" * 32
        ), mock.patch.object(
            behavior_eval, "_run", side_effect=stopped
        ), mock.patch.object(
            behavior_eval, "_remove_validation_container"
        ) as remove, self.assertRaises(behavior_eval._ProcessTimedOut):
            behavior_eval._run_docker_runner(
                ["codex", "exec"],
                "prompt",
                Path("/workspace"),
                1,
                RUNNER_IMAGE_ID,
                client_environment,
                "/workspace",
            )

        remove.assert_called_once_with(
            "cdw-runner-" + "2" * 32,
            Path("/workspace"),
            {"PATH": os.defpath},
            settle_seconds=behavior_eval.DOCKER_CLEANUP_RACE_SECONDS,
            container_id=None,
        )

    def test_main_turns_unexpected_value_error_into_a_failure_report(self) -> None:
        with mock.patch.object(
            behavior_eval, "run_case", side_effect=ValueError("bad process value")
        ), mock.patch("builtins.print") as output:
            code = behavior_eval.main(
                ["--case", "case.json", "--runner-json", '["runner"]']
            )

        self.assertEqual(code, 2)
        rendered = output.call_args.args[0]
        report = json.loads(rendered)
        self.assertFalse(report["valid"])
        self.assertEqual(report["errors"], ["bad process value"])

    def test_main_requires_caller_workspace_for_any_unsafe_direct_mode(self) -> None:
        isolation_arguments = (
            ["--runner-isolation", "unsafe-direct"],
            ["--validation-isolation", "unsafe-direct"],
        )
        for isolation in isolation_arguments:
            with self.subTest(isolation=isolation), mock.patch.object(
                behavior_eval.tempfile, "mkdtemp"
            ) as make_temporary, mock.patch.object(
                behavior_eval, "run_case"
            ) as run_case, mock.patch("builtins.print") as output:
                code = behavior_eval.main(
                    [
                        "--case",
                        "case.json",
                        "--runner-json",
                        '["runner"]',
                        *isolation,
                    ]
                )

            self.assertEqual(code, 2)
            make_temporary.assert_not_called()
            run_case.assert_not_called()
            report = json.loads(output.call_args.args[0])
            self.assertIn("--workspace is required", report["errors"][0])

    def test_temporary_workspace_cleanup_timeout_is_bounded_and_reported(self) -> None:
        stopped = behavior_eval._ProcessTimedOut("timed out", b"", b"")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "generated"
            root.mkdir()
            identity = behavior_eval._directory_identity(
                root, "temporary evaluation root"
            )
            with mock.patch.object(
                behavior_eval, "_run", side_effect=stopped
            ) as run, self.assertRaisesRegex(
                behavior_eval.EvalError, "cleanup timed out"
            ):
                behavior_eval._cleanup_temporary_evaluation_root(root, identity)

            self.assertEqual(
                run.call_args.kwargs["timeout"],
                behavior_eval.TEMPORARY_CLEANUP_TIMEOUT_SECONDS,
            )

        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary) / "generated"

            def make_generated_root(*_args: object, **_kwargs: object) -> str:
                generated.mkdir()
                return str(generated)

            successful_report = {
                "version": "skill-behavior-report-v1",
                "valid": True,
                "errors": [],
            }
            with mock.patch.object(
                behavior_eval.tempfile,
                "mkdtemp",
                side_effect=make_generated_root,
            ), mock.patch.object(
                behavior_eval,
                "run_case",
                return_value=(0, successful_report),
            ), mock.patch.object(
                behavior_eval,
                "_cleanup_temporary_evaluation_root",
                side_effect=behavior_eval.EvalError(
                    "temporary workspace cleanup timed out"
                ),
            ), mock.patch("builtins.print") as output:
                code = behavior_eval.main(
                    ["--case", "case.json", "--runner-json", '["runner"]']
                )

            report = json.loads(output.call_args.args[0])

        self.assertEqual(code, 2)
        self.assertFalse(report["valid"])
        self.assertIn("temporary workspace cleanup timed out", report["errors"])
        self.assertEqual(report["temporary_workspace_cleanup"]["status"], "failed")
        self.assertTrue(
            report["temporary_workspace_cleanup"]["outside_case_deadline"]
        )

    def test_temporary_cleanup_waits_for_confirmed_container_teardown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary) / "generated"

            def make_generated_root(*_args: object, **_kwargs: object) -> str:
                generated.mkdir()
                return str(generated)

            with mock.patch.object(
                behavior_eval.tempfile,
                "mkdtemp",
                side_effect=make_generated_root,
            ), mock.patch.object(
                behavior_eval,
                "run_case",
                side_effect=behavior_eval._ContainerCleanupUnconfirmed(
                    "cannot confirm Docker teardown"
                ),
            ), mock.patch.object(
                behavior_eval, "_cleanup_temporary_evaluation_root"
            ) as cleanup, mock.patch("builtins.print") as output:
                code = behavior_eval.main(
                    ["--case", "case.json", "--runner-json", '["runner"]']
                )

            report = json.loads(output.call_args.args[0])
            root_was_preserved = generated.exists()

        self.assertEqual(code, 2)
        self.assertTrue(root_was_preserved)
        cleanup.assert_not_called()
        self.assertEqual(
            report["temporary_workspace_cleanup"]["status"],
            "not_performed_container_teardown_unconfirmed",
        )
        self.assertTrue(
            report["temporary_workspace_cleanup"]["manual_cleanup_required"]
        )

    def test_temporary_cleanup_removes_locked_secret_without_reporting_it(self) -> None:
        secret = "sk-cleanup-residue-secret"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, skill, case = self.make_fixture(root)
            generated = root / "generated"

            def make_generated_root(*_args: object, **_kwargs: object) -> str:
                generated.mkdir(mode=0o700)
                return str(generated)

            def simulate_docker_case(
                _case: Path,
                _source: Path,
                _skill: Path,
                _runner: list[str],
                workspace: Path,
                *_args: object,
            ) -> tuple[int, dict[str, object]]:
                nested = workspace / "locked" / "inside"
                nested.mkdir(parents=True)
                secret_file = nested / secret
                secret_file.write_text("credential residue", encoding="utf-8")
                secret_file.chmod(0)
                nested.chmod(0)
                nested.parent.chmod(0)
                workspace.chmod(0)
                return (
                    2,
                    {
                        "version": "skill-behavior-report-v1",
                        "valid": False,
                        "errors": ["simulated Docker evaluation failure"],
                    },
                )

            arguments = [
                "--case",
                str(case),
                "--source-root",
                str(source),
                "--skill-path",
                str(skill),
                "--runner-json",
                '["codex", "exec"]',
            ]
            with mock.patch.object(
                behavior_eval.tempfile,
                "mkdtemp",
                side_effect=make_generated_root,
            ), mock.patch.object(
                behavior_eval, "run_case", side_effect=simulate_docker_case
            ), mock.patch.dict(
                os.environ, {"OPENAI_API_KEY": secret}
            ), mock.patch("builtins.print") as output:
                code = behavior_eval.main(arguments)

            rendered = output.call_args.args[0]
            generated_still_exists = os.path.lexists(generated)

        self.assertEqual(code, 2)
        self.assertFalse(generated_still_exists)
        self.assertNotIn(secret, rendered)
        report = json.loads(rendered)
        self.assertEqual(
            report["temporary_workspace_cleanup"]["status"], "completed"
        )

    def test_temporary_cleanup_handles_tree_deeper_than_python_recursion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary) / "generated"
            generated.mkdir()
            identity = behavior_eval._directory_identity(
                generated, "temporary evaluation root"
            )
            descriptor = os.open(
                generated,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                for _index in range(1100):
                    os.mkdir("d", dir_fd=descriptor)
                    child = os.open(
                        "d",
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=descriptor,
                    )
                    os.close(descriptor)
                    descriptor = child
                secret_descriptor = os.open(
                    "secret",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=descriptor,
                )
                os.close(secret_descriptor)
            finally:
                os.close(descriptor)

            behavior_eval._cleanup_temporary_evaluation_root(generated, identity)
            generated_still_exists = os.path.lexists(generated)

        self.assertFalse(generated_still_exists)

    def test_temporary_cleanup_depth_is_memory_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary) / "generated"
            nested = generated / "one" / "two" / "three"
            nested.mkdir(parents=True)
            identity = behavior_eval._directory_identity(
                generated, "temporary evaluation root"
            )
            with mock.patch.object(
                behavior_eval, "TEMPORARY_CLEANUP_MAX_DEPTH", 2
            ), self.assertRaisesRegex(
                behavior_eval.EvalError, "cleanup failed"
            ):
                behavior_eval._cleanup_temporary_evaluation_root(
                    generated, identity
                )
            generated_still_exists = os.path.lexists(generated)

        self.assertTrue(generated_still_exists)


if __name__ == "__main__":
    unittest.main()
