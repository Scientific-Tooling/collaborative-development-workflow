from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, "scripts")

import snapshot_tool


class SnapshotToolTests(unittest.TestCase):
    def run_git(self, root: Path, *args: str, environment: dict[str, str] | None = None) -> None:
        env = os.environ.copy()
        if environment:
            env.update(environment)
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def write_scope(self, path: Path) -> None:
        scope = {
            "version": "impact-scope-v2",
            "changed_paths": [
                "tracked.txt",
                "unicode.txt",
                "binary.bin",
                "old.txt",
                "new.txt",
                "renamed-from.txt",
                "renamed-to.txt",
                "link",
                "nested",
            ],
            "review_paths": [
                "tracked.txt",
                "unicode.txt",
                "binary.bin",
                "old.txt",
                "new.txt",
                "renamed-from.txt",
                "renamed-to.txt",
                "link",
                "nested",
            ],
            "direct_callers": [],
            "direct_consumers": [],
            "mapped_tests_or_configuration": [],
            "explicit_exclusions": [],
        }
        path.write_text(json.dumps(scope, ensure_ascii=False), encoding="utf-8")

    def setup_repository(self, root: Path) -> None:
        root.mkdir()
        self.run_git(root, "-c", "init.defaultBranch=main", "init", "-q")
        self.run_git(root, "config", "user.email", "test@example.invalid")
        self.run_git(root, "config", "user.name", "Snapshot Test")
        (root / "tracked.txt").write_text("base\n", encoding="utf-8")
        (root / "unicode.txt").write_text("雪\n", encoding="utf-8")
        (root / "binary.bin").write_bytes(b"\x00\xff\x01")
        (root / "old.txt").write_text("will be deleted\n", encoding="utf-8")
        (root / "renamed-from.txt").write_text("rename source\n", encoding="utf-8")
        self.run_git(root, "add", "tracked.txt", "unicode.txt", "binary.bin", "old.txt", "renamed-from.txt")
        self.run_git(
            root,
            "commit",
            "-qm",
            "base",
            environment={
                "GIT_AUTHOR_DATE": "@1 +0000",
                "GIT_COMMITTER_DATE": "@1 +0000",
            },
        )
        (root / "tracked.txt").write_text("changed\n", encoding="utf-8")
        os.chmod(root / "tracked.txt", 0o600)
        (root / "old.txt").unlink()
        (root / "new.txt").write_text("untracked\n", encoding="utf-8")
        (root / "renamed-from.txt").unlink()
        (root / "renamed-to.txt").write_text("rename source\n", encoding="utf-8")
        (root / "nested").mkdir()
        (root / "nested" / "inside.txt").write_text("inside\n", encoding="utf-8")
        os.symlink("unicode.txt", root / "link")

    def git_state(self, root: Path) -> dict[str, tuple]:
        git_root = root / ".git"
        state: dict[str, tuple] = {}
        for current_root, directories, filenames in os.walk(git_root, followlinks=False):
            for name in directories + filenames:
                path = Path(current_root) / name
                info = os.lstat(path)
                relative = path.relative_to(git_root).as_posix()
                payload = os.readlink(path) if stat.S_ISLNK(info.st_mode) else path.read_bytes() if stat.S_ISREG(info.st_mode) else None
                state[relative] = (
                    stat.S_IFMT(info.st_mode),
                    stat.S_IMODE(info.st_mode),
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                    payload,
                )
        return state

    def test_snapshot_round_trip_and_workspace_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"

            git_before = self.git_state(root)
            created = snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            self.assertEqual(self.git_state(root), git_before)
            self.assertEqual(created["content_identity"], "df2f6ca5488bf605ee713a79004aa79c3d124055408d79069442acb73db0c910")
            self.assertEqual(created["manifest_identity"], "a78714df97d09975c65df806d72d8feec5a58feee1a96759665b25ccbd5df9b9")
            self.assertTrue(snapshot_tool.verify_artifact(str(artifact))["valid"])
            code, compared = snapshot_tool.compare_workspace(str(root), str(artifact))
            self.assertEqual(code, 0)
            self.assertTrue(compared["match"])

            entries = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))["entries"]
            by_path = {entry["path"]: entry for entry in entries}
            self.assertEqual(by_path["old.txt"]["type"], "deleted")
            self.assertEqual(by_path["renamed-from.txt"]["type"], "deleted")
            self.assertEqual(by_path["renamed-to.txt"]["type"], "file")
            self.assertEqual(by_path["unicode.txt"]["type"], "file")
            self.assertEqual(by_path["binary.bin"]["size"], 3)
            self.assertEqual(by_path["link"]["type"], "symlink")
            self.assertEqual(by_path["link"]["symlink_target"], "unicode.txt")
            baseline = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))["baseline_entries"]
            baseline_by_path = {entry["path"]: entry for entry in baseline}
            self.assertEqual(baseline_by_path["tracked.txt"]["type"], "file")
            self.assertEqual((artifact / baseline_by_path["tracked.txt"]["artifact_path"]).read_text(encoding="utf-8"), "base\n")
            self.assertEqual(baseline_by_path["renamed-from.txt"]["type"], "file")
            self.assertEqual(by_path["tracked.txt"]["mode"], stat.S_IMODE(os.lstat(root / "tracked.txt").st_mode))
            self.assertEqual(stat.S_IMODE(os.lstat(artifact).st_mode), 0o500)
            self.assertEqual(stat.S_IMODE(os.lstat(artifact / "manifest.json").st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(os.lstat(artifact / "files" / "tracked.txt").st_mode), 0o400)

            (root / "tracked.txt").write_text("mutated\n", encoding="utf-8")
            code, compared = snapshot_tool.compare_workspace(str(root), str(artifact))
            self.assertEqual(code, 1)
            self.assertFalse(compared["match"])
            cli_result = subprocess.run(
                [
                    sys.executable,
                    str(Path(snapshot_tool.__file__).resolve()),
                    "compare",
                    str(root),
                    str(artifact),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(cli_result.returncode, 1)
            self.assertFalse(json.loads(cli_result.stdout)["match"])

    def test_helper_cli_imports_do_not_create_bytecode_in_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            source_root = Path(snapshot_tool.__file__).resolve().parents[1]
            copied_root = parent / "copied-skill"
            shutil.copytree(source_root / "scripts", copied_root / "scripts")
            (copied_root / "references").mkdir()
            shutil.copy2(
                source_root / "references" / "contracts-v2.json",
                copied_root / "references" / "contracts-v2.json",
            )
            shutil.rmtree(copied_root / "scripts" / "__pycache__", ignore_errors=True)
            environment = os.environ.copy()
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            for helper in ("snapshot_tool.py", "doctor.py"):
                result = subprocess.run(
                    [sys.executable, str(copied_root / "scripts" / helper), "--help"],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((copied_root / "scripts" / "__pycache__").exists())

    def test_staged_worktree_is_snapshotted_without_index_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            self.run_git(root, "add", "tracked.txt")
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            git_before = self.git_state(root)
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            self.assertEqual(self.git_state(root), git_before)
            code, compared = snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))
            self.assertEqual(code, 0)
            self.assertTrue(compared["match"])

    def test_maximum_source_depth_accounts_for_artifact_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            deep_parts = ["deep", *(f"d{index}" for index in range(62)), "payload.txt"]
            deep_file = root.joinpath(*deep_parts)
            deep_file.parent.mkdir(parents=True)
            deep_file.write_text("deep\n", encoding="utf-8")
            scope = parent / "scope.json"
            self.write_scope(scope)
            scope_data = json.loads(scope.read_text(encoding="utf-8"))
            scope_data["changed_paths"].append("deep")
            scope_data["review_paths"].append("deep")
            scope.write_text(json.dumps(scope_data), encoding="utf-8")
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            self.assertTrue(snapshot_tool.verify_artifact(str(artifact), str(scope))["valid"])

    def test_snapshot_rejects_oversized_entries_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            original_limit = snapshot_tool.MAX_ENTRIES
            snapshot_tool.MAX_ENTRIES = 0
            try:
                with self.assertRaises(snapshot_tool.SnapshotError):
                    snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            finally:
                snapshot_tool.MAX_ENTRIES = original_limit
            self.assertFalse(artifact.exists())

    def test_artifact_mutation_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            target = artifact / "files" / "tracked.txt"
            os.chmod(target, 0o600)
            target.write_text("tampered\n", encoding="utf-8")
            os.chmod(target, 0o400)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.verify_artifact(str(artifact))

    def test_artifact_tree_addition_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            os.chmod(artifact, 0o700)
            extra = artifact / "extra.txt"
            extra.write_text("unexpected\n", encoding="utf-8")
            os.chmod(extra, 0o400)
            os.chmod(artifact, 0o500)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.verify_artifact(str(artifact))

    def test_manifest_shape_and_symlink_hash_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            manifest["entries"][0]["type"] = "deleted"
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool._validate_manifest(manifest)

            fresh_artifact = parent / "fresh-artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(fresh_artifact))
            fresh_manifest = json.loads((fresh_artifact / "manifest.json").read_text(encoding="utf-8"))
            link_entry = next(entry for entry in fresh_manifest["entries"] if entry["path"] == "link")
            link_entry["content_hash"] = "0" * 64
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool._validate_manifest(fresh_manifest)

            oversized = json.loads((fresh_artifact / "manifest.json").read_text(encoding="utf-8"))
            oversized_link = next(entry for entry in oversized["entries"] if entry["path"] == "link")
            oversized_link["symlink_target"] = "x" * (snapshot_tool.SYMLINK_TARGET_BYTES + 1)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool._validate_manifest(oversized)

            malformed = json.loads((fresh_artifact / "manifest.json").read_text(encoding="utf-8"))
            malformed["entries"][0]["status"] = {}
            manifest_path = fresh_artifact / "manifest.json"
            os.chmod(fresh_artifact, 0o700)
            os.chmod(manifest_path, 0o600)
            manifest_path.write_text(json.dumps(malformed), encoding="utf-8")
            os.chmod(manifest_path, 0o400)
            os.chmod(fresh_artifact, 0o500)
            result = subprocess.run(
                [sys.executable, str(Path(snapshot_tool.__file__).resolve()), "verify", str(fresh_artifact)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(json.loads(result.stdout)["valid"])

            forged = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            forged_entry = dict(next(entry for entry in forged["entries"] if entry["path"] == "tracked.txt"))
            forged_entry["path"] = "outside.txt"
            forged_entry["artifact_path"] = "files/outside.txt"
            forged["entries"].append(forged_entry)
            forged["entries"].sort(key=lambda entry: entry["path"])
            forged["content_identity"] = snapshot_tool._content_identity(forged)
            forged["manifest_identity"] = snapshot_tool._manifest_identity(forged)
            manifest_path = artifact / "manifest.json"
            os.chmod(artifact, 0o700)
            os.chmod(manifest_path, 0o600)
            manifest_path.write_text(json.dumps(forged), encoding="utf-8")
            os.chmod(manifest_path, 0o400)
            os.chmod(artifact, 0o500)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.verify_artifact(str(artifact))

    def test_manifest_contract_is_closed_and_drives_artifact_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(set(manifest), snapshot_tool.MANIFEST_FIELDS)
            self.assertEqual(manifest["version"], snapshot_tool.SNAPSHOT_MANIFEST["version"])
            self.assertEqual(set(manifest["base_git_identity"]), snapshot_tool.GIT_IDENTITY_FIELDS)
            for entry in manifest["entries"] + manifest["baseline_entries"]:
                self.assertEqual(set(entry), snapshot_tool.ENTRY_FIELDS)
                self.assertIn(entry["status"], snapshot_tool.ENTRY_STATUSES)
                self.assertIn(entry["type"], snapshot_tool.ENTRY_TYPES)
            self.assertEqual(snapshot_tool.ARTIFACT_PREFIXES, {"files": "files", "baseline": "baseline"})
            self.assertLessEqual(len(manifest["entries"]), snapshot_tool.MAX_ENTRIES)
            self.assertLessEqual(len(manifest["baseline_entries"]), snapshot_tool.MAX_ENTRIES)
            self.assertEqual(set(os.listdir(artifact)), snapshot_tool.TOP_LEVEL)

    def test_drive_relative_and_symlinked_roots_are_rejected(self) -> None:
        with self.assertRaises(snapshot_tool.ContractError):
            snapshot_tool.normalize_repo_path("C:relative")
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            linked_root = parent / "repo-link"
            os.symlink(root, linked_root)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.create_snapshot(str(linked_root), str(scope), str(parent / "artifact"))

            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.create_snapshot(str(parent / "repo" / ".." / "repo"), str(scope), str(parent / "artifact-2"))

            scope_link = parent / "scope-link.json"
            os.symlink(scope, scope_link)
            with self.assertRaises(snapshot_tool.ContractError):
                snapshot_tool.create_snapshot(str(root), str(scope_link), str(parent / "artifact-3"))

    def test_symlink_component_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            with scope.open("r+", encoding="utf-8") as stream:
                value = json.load(stream)
                value["changed_paths"] = ["link/inside.txt"]
                value["review_paths"] = ["link/inside.txt"]
                stream.seek(0)
                stream.truncate()
                json.dump(value, stream)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.create_snapshot(str(root), str(scope), str(parent / "artifact"))

    def test_scoped_directory_stat_to_open_swap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            parked = root / "parked-nested"
            replacement = root / "replacement-nested"
            replacement.mkdir()
            (replacement / "other.txt").write_text("replacement\n", encoding="utf-8")
            descriptor = snapshot_tool._open_root_fd(root)
            original_open = os.open
            directory_flags = snapshot_tool._directory_flags()
            swapped = False

            def swap_during_open(
                path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
            ) -> int:
                nonlocal swapped
                if path == "nested" and dir_fd is not None and not swapped:
                    os.rename(root / "nested", parked)
                    os.rename(replacement, root / "nested")
                    try:
                        opened = original_open(path, flags, mode, dir_fd=dir_fd)
                    finally:
                        os.rename(root / "nested", replacement)
                        os.rename(parked, root / "nested")
                    swapped = True
                    return opened
                return original_open(path, flags, mode, dir_fd=dir_fd)

            try:
                with mock.patch.object(
                    snapshot_tool, "_directory_flags", return_value=directory_flags
                ), mock.patch.object(snapshot_tool.os, "open", swap_during_open):
                    with self.assertRaisesRegex(snapshot_tool.SnapshotError, "changed before listing"):
                        list(snapshot_tool._entry_paths(descriptor, "nested"))
            finally:
                os.close(descriptor)

    def test_broken_output_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            output = parent / "broken-artifact"
            os.symlink(parent / "missing-artifact", output)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.create_snapshot(str(root), str(scope), str(output))

    def test_existing_output_directory_is_not_repermissioned_on_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            output = parent / "existing-artifact"
            output.mkdir()
            os.chmod(output, 0o750)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.create_snapshot(str(root), str(scope), str(output))
            self.assertEqual(stat.S_IMODE(os.lstat(output).st_mode), 0o750)

    def test_artifact_symlink_is_rejected_during_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            target = artifact / "files" / "tracked.txt"
            os.chmod(artifact, 0o700)
            os.chmod(artifact / "files", 0o700)
            target.unlink()
            os.symlink(root / "unicode.txt", target)
            os.chmod(artifact / "files", 0o500)
            os.chmod(artifact, 0o500)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.verify_artifact(str(artifact))

    def test_expected_scope_identity_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))

            other_scope = parent / "other-scope.json"
            other_scope.write_text(
                json.dumps(
                    {
                        "version": "impact-scope-v2",
                        "changed_paths": ["tracked.txt"],
                        "review_paths": ["tracked.txt"],
                        "direct_callers": [],
                        "direct_consumers": [],
                        "mapped_tests_or_configuration": [],
                        "explicit_exclusions": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.verify_artifact(str(artifact), str(other_scope))

    def test_compare_rechecks_git_identity_after_scoped_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))

            original_git_identity = snapshot_tool._git_identity
            calls = 0

            def changing_git_identity(root_descriptor: int) -> dict[str, str]:
                nonlocal calls
                calls += 1
                identity = original_git_identity(root_descriptor)
                if calls >= 4:
                    identity = dict(identity, worktree_status_digest="0" * 64)
                return identity

            snapshot_tool._git_identity = changing_git_identity  # type: ignore[assignment]
            try:
                code, result = snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))
            finally:
                snapshot_tool._git_identity = original_git_identity  # type: ignore[assignment]
            self.assertEqual(code, 1)
            self.assertFalse(result["match"])
            self.assertEqual(result["reason"], "GIT_IDENTITY_CHANGED_DURING_COMPARE")

    def test_repository_subdirectory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            with self.assertRaisesRegex(snapshot_tool.SnapshotError, "worktree top level"):
                snapshot_tool.create_snapshot(
                    str(root / "nested"), str(scope), str(parent / "artifact")
                )

    def test_invalid_arguments_do_not_leak_open_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)

            original_open_root = snapshot_tool._open_root_fd
            opened_roots: list[int] = []

            def capture_root(path: Path) -> int:
                descriptor = original_open_root(path)
                opened_roots.append(descriptor)
                return descriptor

            with mock.patch.object(snapshot_tool, "_open_root_fd", capture_root):
                with self.assertRaisesRegex(snapshot_tool.SnapshotError, "must not contain"):
                    snapshot_tool.create_snapshot(str(root), str(scope), "../artifact")
            self.assertEqual(opened_roots, [])

            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            original_open_artifact = snapshot_tool._open_absolute_directory
            opened_artifacts: list[int] = []

            def capture_artifact(path: Path) -> int:
                descriptor = original_open_artifact(path)
                opened_artifacts.append(descriptor)
                return descriptor

            with mock.patch.object(
                snapshot_tool, "_open_absolute_directory", capture_artifact
            ):
                with self.assertRaises(snapshot_tool.SnapshotError):
                    snapshot_tool.compare_workspace(
                        str(parent / "missing-root"), str(artifact), str(scope)
                    )
            self.assertEqual(len(opened_artifacts), 1)
            with self.assertRaises(OSError):
                os.fstat(opened_artifacts[0])

    def test_unborn_repository_and_empty_scope_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            root.mkdir()
            self.run_git(root, "-c", "init.defaultBranch=main", "init", "-q")
            scope = parent / "scope.json"
            scope.write_text(
                json.dumps(
                    {
                        "version": "impact-scope-v2",
                        "changed_paths": [],
                        "review_paths": [],
                        "direct_callers": [],
                        "direct_consumers": [],
                        "mapped_tests_or_configuration": [],
                        "explicit_exclusions": [],
                    }
                ),
                encoding="utf-8",
            )
            artifact = parent / "artifact"
            created = snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["base_git_identity"]["head"], "UNCOMMITTED")
            self.assertEqual(manifest["base_git_identity"]["branch"], "refs/heads/main")
            self.assertEqual(manifest["entries"], [])
            self.assertEqual(manifest["baseline_entries"], [])
            self.assertTrue(
                snapshot_tool.verify_artifact(
                    str(artifact),
                    str(scope),
                    created["content_identity"],
                    created["manifest_identity"],
                )["valid"]
            )

    def test_head_query_failure_is_not_misreported_as_unborn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            self.setup_repository(root)
            descriptor = snapshot_tool._open_root_fd(root)
            original_run_git = snapshot_tool._run_git

            def fail_head(root_descriptor: int, *args: str, **kwargs: object) -> bytes:
                if args == ("rev-parse", "--verify", "HEAD"):
                    raise snapshot_tool.SnapshotError("injected HEAD timeout")
                return original_run_git(root_descriptor, *args, **kwargs)

            try:
                with mock.patch.object(snapshot_tool, "_run_git", fail_head):
                    with self.assertRaisesRegex(snapshot_tool.SnapshotError, "HEAD timeout"):
                        snapshot_tool._git_identity(descriptor)
            finally:
                os.close(descriptor)

    def test_attached_detached_branch_identity_is_unambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            self.run_git(root, "switch", "-c", "DETACHED")
            attached = snapshot_tool.git_identity(root)
            self.assertEqual(attached["branch"], "refs/heads/DETACHED")
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))

            self.run_git(root, "checkout", "-q", "--detach")
            detached = snapshot_tool.git_identity(root)
            self.assertEqual(detached["branch"], "DETACHED")
            self.assertEqual(
                {key: value for key, value in attached.items() if key != "branch"},
                {key: value for key, value in detached.items() if key != "branch"},
            )
            self.assertNotEqual(attached, detached)
            self.assertEqual(
                snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))[0],
                1,
            )

    def test_intent_to_add_transition_changes_index_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            intent_path = root / "intent.txt"
            intent_path.write_bytes(b"")
            self.run_git(root, "add", "-N", "--", "intent.txt")
            intent_stage = self.run_git_output(root, "ls-files", "--stage", "intent.txt")
            intent_identity = snapshot_tool.git_identity(root)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))

            self.run_git(root, "add", "--", "intent.txt")
            staged_identity = snapshot_tool.git_identity(root)
            self.assertEqual(
                intent_stage,
                self.run_git_output(root, "ls-files", "--stage", "intent.txt"),
                "legacy stage inventory must collide for this regression fixture",
            )
            self.assertEqual(intent_identity["head"], staged_identity["head"])
            self.assertEqual(intent_identity["branch"], staged_identity["branch"])
            self.assertEqual(
                intent_identity["worktree_status_digest"],
                staged_identity["worktree_status_digest"],
            )
            self.assertNotEqual(intent_identity["index_tree"], staged_identity["index_tree"])
            self.assertEqual(
                snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))[0],
                1,
            )

    def test_diff_order_configuration_cannot_change_index_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            (root / "a.txt").write_bytes(b"")
            (root / "b.txt").write_bytes(b"")
            self.run_git(root, "add", "--", "a.txt", "b.txt")
            default_raw = self.run_git_output(
                root, "diff", "--cached", "--raw", "--no-abbrev", "-z"
            )
            expected_identity = snapshot_tool.git_identity(root)

            order_file = parent / "diff-order"
            order_file.write_text("b.txt\na.txt\n", encoding="utf-8")
            self.run_git(root, "config", "diff.orderFile", str(order_file))
            configured_raw = self.run_git_output(
                root, "diff", "--cached", "--raw", "--no-abbrev", "-z"
            )
            self.assertNotEqual(
                default_raw,
                configured_raw,
                "fixture must prove diff.orderFile changes ordinary raw output",
            )
            self.assertEqual(snapshot_tool.git_identity(root), expected_identity)

    def test_submodule_diff_configuration_cannot_change_index_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            first_commit = self.run_git_output(root, "rev-parse", "HEAD")
            tree = self.run_git_output(root, "rev-parse", "HEAD^{tree}")
            second_commit = self.run_git_output(
                root,
                "commit-tree",
                tree,
                "-p",
                first_commit,
                "-m",
                "synthetic submodule commit",
            )
            self.run_git(
                root,
                "update-index",
                "--add",
                "--cacheinfo",
                "160000",
                first_commit,
                "sub",
            )
            self.run_git(root, "commit", "-qm", "add synthetic gitlink")
            self.run_git(
                root,
                "update-index",
                "--cacheinfo",
                "160000",
                second_commit,
                "sub",
            )
            default_raw = self.run_git_output(
                root, "diff", "--cached", "--raw", "--no-abbrev", "-z"
            )
            expected_identity = snapshot_tool.git_identity(root)

            self.run_git(root, "config", "diff.ignoreSubmodules", "all")
            configured_raw = self.run_git_output(
                root, "diff", "--cached", "--raw", "--no-abbrev", "-z"
            )
            self.assertNotEqual(
                default_raw,
                configured_raw,
                "fixture must prove diff.ignoreSubmodules changes ordinary raw output",
            )
            self.assertEqual(snapshot_tool.git_identity(root), expected_identity)

    def test_git_environment_poisoning_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            other = parent / "other"
            self.setup_repository(root)
            self.setup_repository(other)
            scope = parent / "scope.json"
            self.write_scope(scope)
            poison = {
                "GIT_DIR": str(other / ".git"),
                "GIT_WORK_TREE": str(other),
                "GIT_INDEX_FILE": str(other / ".git" / "index"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
                "GIT_CONFIG_VALUE_0": "99",
            }
            with mock.patch.dict(os.environ, poison, clear=False):
                identity = snapshot_tool.git_identity(root)
                self.assertEqual(identity["head"], self.run_git_output(root, "rev-parse", "HEAD"))
                artifact = parent / "artifact"
                snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
                self.assertEqual(
                    snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))[0],
                    0,
                )

    def test_git_replace_objects_cannot_rewrite_the_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            replacement_payload = parent / "replacement.txt"
            replacement_payload.write_text("replacement\n", encoding="utf-8")
            original_object = self.run_git_output(root, "rev-parse", "HEAD:tracked.txt")
            replacement_object = self.run_git_output(
                root, "hash-object", "-w", str(replacement_payload)
            )
            self.run_git(root, "replace", original_object, replacement_object)
            self.assertEqual(
                self.run_git_output(root, "cat-file", "blob", "HEAD:tracked.txt"),
                "replacement",
            )

            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            manifest = json.loads(
                (artifact / "manifest.json").read_text(encoding="utf-8")
            )
            baseline_by_path = {
                entry["path"]: entry for entry in manifest["baseline_entries"]
            }
            baseline_path = artifact / baseline_by_path["tracked.txt"]["artifact_path"]
            self.assertEqual(baseline_path.read_text(encoding="utf-8"), "base\n")
            self.assertEqual(
                snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))[0],
                0,
            )

    def test_git_identity_never_executes_clean_or_process_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            attributes = root / ".gitattributes"
            attributes.write_text("tracked.txt filter=attack\n", encoding="utf-8")
            self.run_git(root, "add", ".gitattributes")
            self.run_git(root, "commit", "-qm", "add attributes")
            tracked = root / "tracked.txt"
            tracked.write_text("base\n", encoding="utf-8")
            self.run_git(root, "add", "--refresh", "--", "tracked.txt")
            original_stat = os.stat(tracked)

            marker = parent / "filter-ran"
            filter_script = parent / "filter.py"
            filter_script.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "from pathlib import Path\n"
                "data = sys.stdin.buffer.read()\n"
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
                "sys.stdout.buffer.write(data)\n",
                encoding="utf-8",
            )
            filter_script.chmod(0o700)
            self.run_git(root, "config", "filter.attack.clean", str(filter_script))
            tracked.write_text("evil\n", encoding="utf-8")
            os.utime(
                tracked,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )

            self.run_git(root, "status", "--porcelain=v1", "--untracked-files=all", "-z")
            self.assertTrue(marker.exists(), "fixture must prove porcelain status runs the filter")
            marker.unlink()

            snapshot_tool.git_identity(root)
            self.assertFalse(marker.exists())
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            self.assertFalse(marker.exists())
            self.assertEqual(
                snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))[0],
                0,
            )
            self.assertFalse(marker.exists())

            self.run_git(root, "config", "--unset", "filter.attack.clean")
            process_filter = parent / "process-filter.py"
            process_filter.write_text(
                f"#!{sys.executable}\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
                encoding="utf-8",
            )
            process_filter.chmod(0o700)
            self.run_git(root, "config", "filter.attack.process", str(process_filter))
            snapshot_tool.git_identity(root)
            self.assertFalse(marker.exists())

    def test_excluded_descendant_cannot_enter_a_directory_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            record = json.loads(scope.read_text(encoding="utf-8"))
            record["changed_paths"] = ["nested"]
            record["review_paths"] = ["nested"]
            record["explicit_exclusions"] = ["nested/inside.txt"]
            scope.write_text(json.dumps(record), encoding="utf-8")
            artifact = parent / "artifact"

            with self.assertRaisesRegex(
                snapshot_tool.SnapshotError,
                "review_paths overlap explicit_exclusions",
            ):
                snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            self.assertFalse(artifact.exists())

    def test_repository_local_fsmonitor_is_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            expected_identity = snapshot_tool.git_identity(root)
            marker = parent / "fsmonitor-ran"
            hook = parent / "fsmonitor"
            hook.write_text(
                f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            hook.chmod(0o700)
            redirected_worktree = parent / "redirected-worktree"
            redirected_worktree.mkdir()
            self.run_git(root, "config", "core.worktree", str(redirected_worktree))
            self.run_git(root, "config", "core.fsmonitor", str(hook))
            self.assertEqual(snapshot_tool.git_identity(root), expected_identity)
            self.assertFalse(marker.exists())

    def test_git_query_timeout_kills_the_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            fake_bin = parent / "bin"
            fake_bin.mkdir()
            fake_git = fake_bin / "git"
            fake_git.write_text("#!/bin/sh\n/bin/sleep 10 &\nexit 0\n", encoding="utf-8")
            fake_git.chmod(0o700)
            descriptor = snapshot_tool._open_root_fd(root)
            try:
                started = __import__("time").monotonic()
                with mock.patch.dict(os.environ, {"PATH": str(fake_bin)}), mock.patch.object(
                    snapshot_tool, "GIT_TIMEOUT_SECONDS", 1
                ):
                    with self.assertRaisesRegex(snapshot_tool.SnapshotError, "exceeded 1 seconds"):
                        snapshot_tool._run_git(descriptor, "status")
                self.assertLess(__import__("time").monotonic() - started, 5)
            finally:
                os.close(descriptor)

    def run_git_output(self, root: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        ).stdout.strip()

    def test_expected_identities_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            created = snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            self.assertTrue(
                snapshot_tool.verify_artifact(
                    str(artifact),
                    str(scope),
                    created["content_identity"],
                    created["manifest_identity"],
                )["valid"]
            )
            with self.assertRaisesRegex(snapshot_tool.SnapshotError, "expected identity"):
                snapshot_tool.verify_artifact(
                    str(artifact), str(scope), "0" * 64, created["manifest_identity"]
                )
            code, result = snapshot_tool.compare_workspace(
                str(root),
                str(artifact),
                str(scope),
                created["content_identity"],
                created["manifest_identity"],
            )
            self.assertEqual(code, 0, result)
            cli = subprocess.run(
                [
                    sys.executable,
                    str(Path(snapshot_tool.__file__).resolve()),
                    "verify",
                    str(artifact),
                    "--scope",
                    str(scope),
                    "--expected-content-identity",
                    created["content_identity"],
                    "--expected-manifest-identity",
                    created["manifest_identity"],
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)

    def test_failed_staging_is_removed_without_publishing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            with mock.patch.object(
                snapshot_tool,
                "_read_regular_relative",
                side_effect=snapshot_tool.SnapshotError("injected source failure"),
            ):
                with self.assertRaisesRegex(snapshot_tool.SnapshotError, "injected"):
                    snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            self.assertFalse(artifact.exists())
            self.assertEqual(list(parent.glob(".artifact.cdw-stage-*")), [])

    def test_atomic_publication_fails_closed_without_renameat2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            (parent / "stage").mkdir()
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with mock.patch.object(snapshot_tool.ctypes, "CDLL", return_value=object()):
                    with self.assertRaisesRegex(snapshot_tool.SnapshotError, "no-replace"):
                        snapshot_tool._rename_noreplace(descriptor, "stage", "artifact")
            finally:
                os.close(descriptor)
            self.assertTrue((parent / "stage").is_dir())
            self.assertFalse((parent / "artifact").exists())

    def test_baseline_entry_limit_stops_before_fetching_every_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            self.setup_repository(root)
            descriptor = snapshot_tool._open_root_fd(root)
            original_run_git = snapshot_tool._run_git
            cat_file_calls = 0

            def counting_run_git(root_descriptor: int, *args: str, **kwargs: object) -> bytes:
                nonlocal cat_file_calls
                if args[:2] == ("cat-file", "blob"):
                    cat_file_calls += 1
                return original_run_git(root_descriptor, *args, **kwargs)

            try:
                head = snapshot_tool._git_identity(descriptor)["head"]
                with mock.patch.object(snapshot_tool, "MAX_ENTRIES", 1), mock.patch.object(
                    snapshot_tool, "_run_git", counting_run_git
                ):
                    with self.assertRaisesRegex(snapshot_tool.SnapshotError, "baseline entries"):
                        snapshot_tool._baseline_entries(
                            descriptor, ["tracked.txt", "unicode.txt"], head
                        )
            finally:
                os.close(descriptor)
            self.assertEqual(cat_file_calls, 0)

    def test_file_limit_fails_before_artifact_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            with mock.patch.object(snapshot_tool, "MAX_FILE_BYTES", 2):
                with self.assertRaisesRegex(snapshot_tool.SnapshotError, "exceeds 2 bytes"):
                    snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            self.assertFalse(artifact.exists())

    def test_total_and_git_output_limits_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            with mock.patch.object(snapshot_tool, "MAX_TOTAL_BYTES", 4):
                with self.assertRaisesRegex(snapshot_tool.SnapshotError, "exceeds 4 bytes"):
                    snapshot_tool.create_snapshot(
                        str(root), str(scope), str(parent / "artifact")
                    )
            with mock.patch.object(snapshot_tool, "MAX_GIT_OUTPUT_BYTES", 2):
                with self.assertRaisesRegex(snapshot_tool.SnapshotError, "Git output exceeds 2"):
                    snapshot_tool.git_identity(root)

    def test_compare_rechecks_workspace_content_with_stable_git_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            original = snapshot_tool._compare_workspace_entries
            calls = 0

            def mutate_after_read(root_descriptor: int, manifest: dict) -> tuple[int, dict, list[dict]]:
                nonlocal calls
                result = original(root_descriptor, manifest)
                calls += 1
                if calls == 1:
                    (root / "tracked.txt").write_text("same-status-new-bytes\n", encoding="utf-8")
                return result

            with mock.patch.object(snapshot_tool, "_compare_workspace_entries", mutate_after_read):
                code, result = snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))
            self.assertEqual(code, 1)
            self.assertFalse(result["match"])

    def test_transient_repository_path_swap_cannot_redirect_compare(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            replacement = parent / "replacement"
            parked = parent / "parked"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            expected_identity = snapshot_tool.git_identity(root)

            self.setup_repository(replacement)
            (replacement / "tracked.txt").write_text("replacement bytes\n", encoding="utf-8")
            self.run_git(replacement, "add", "-A")
            self.run_git(
                replacement,
                "commit",
                "-qm",
                "replacement",
                environment={
                    "GIT_AUTHOR_DATE": "@2 +0000",
                    "GIT_COMMITTER_DATE": "@2 +0000",
                },
            )
            original_compare = snapshot_tool._compare_workspace_entries
            swaps = 0

            def compare_during_swap(
                root_descriptor: int, manifest: dict
            ) -> tuple[int, dict, list[dict]]:
                nonlocal swaps
                os.rename(root, parked)
                os.rename(replacement, root)
                try:
                    self.assertEqual(
                        snapshot_tool._read_regular_relative(root_descriptor, "tracked.txt"),
                        b"changed\n",
                    )
                    self.assertEqual(snapshot_tool._git_identity(root_descriptor), expected_identity)
                    swaps += 1
                    return original_compare(root_descriptor, manifest)
                finally:
                    os.rename(root, replacement)
                    os.rename(parked, root)

            with mock.patch.object(
                snapshot_tool, "_compare_workspace_entries", compare_during_swap
            ):
                code, result = snapshot_tool.compare_workspace(
                    str(root), str(artifact), str(scope)
                )
            self.assertEqual(code, 0, result)
            self.assertEqual(swaps, 2)

    def test_create_rejects_late_repository_path_swap_and_removes_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            parked = parent / "parked"
            replacement = parent / "replacement"
            self.setup_repository(root)
            replacement.mkdir()
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            original_verify = snapshot_tool._verify_artifact_descriptor
            swapped = False

            def swap_after_verify(*args: object, **kwargs: object) -> dict:
                nonlocal swapped
                manifest = original_verify(*args, **kwargs)
                if not swapped:
                    os.rename(root, parked)
                    os.rename(replacement, root)
                    swapped = True
                return manifest

            try:
                with mock.patch.object(
                    snapshot_tool, "_verify_artifact_descriptor", swap_after_verify
                ):
                    with self.assertRaisesRegex(snapshot_tool.SnapshotError, "replaced"):
                        snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            finally:
                if swapped:
                    os.rename(root, replacement)
                    os.rename(parked, root)
            self.assertFalse(artifact.exists())

    def test_create_rejects_late_output_parent_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            output_parent = parent / "artifacts"
            parked_parent = parent / "parked-artifacts"
            self.setup_repository(root)
            output_parent.mkdir()
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = output_parent / "artifact"
            original_rename = snapshot_tool._rename_noreplace
            swapped = False

            def swap_after_publish(
                parent_descriptor: int, source_name: str, target_name: str
            ) -> None:
                nonlocal swapped
                original_rename(parent_descriptor, source_name, target_name)
                os.rename(output_parent, parked_parent)
                output_parent.mkdir()
                swapped = True

            try:
                with mock.patch.object(snapshot_tool, "_rename_noreplace", swap_after_publish):
                    with self.assertRaisesRegex(snapshot_tool.SnapshotError, "no longer bound"):
                        snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            finally:
                if swapped:
                    output_parent.rmdir()
                    os.rename(parked_parent, output_parent)
            self.assertFalse(artifact.exists())

    def test_compare_rejects_late_repository_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            parked = parent / "parked"
            replacement = parent / "replacement"
            self.setup_repository(root)
            replacement.mkdir()
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            original_verify = snapshot_tool._verify_artifact_descriptor
            calls = 0
            swapped = False

            def swap_after_final_verify(*args: object, **kwargs: object) -> dict:
                nonlocal calls, swapped
                manifest = original_verify(*args, **kwargs)
                calls += 1
                if calls == 2:
                    os.rename(root, parked)
                    os.rename(replacement, root)
                    swapped = True
                return manifest

            try:
                with mock.patch.object(
                    snapshot_tool, "_verify_artifact_descriptor", swap_after_final_verify
                ):
                    with self.assertRaisesRegex(snapshot_tool.SnapshotError, "replaced"):
                        snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))
            finally:
                if swapped:
                    os.rename(root, replacement)
                    os.rename(parked, root)

    def test_compare_rejects_artifact_path_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "repo"
            self.setup_repository(root)
            scope = parent / "scope.json"
            self.write_scope(scope)
            artifact = parent / "artifact"
            replacement = parent / "replacement"
            snapshot_tool.create_snapshot(str(root), str(scope), str(artifact))
            snapshot_tool.create_snapshot(str(root), str(scope), str(replacement))
            original = snapshot_tool._verify_artifact_descriptor
            calls = 0

            def swap_after_verify(*args: object, **kwargs: object) -> dict:
                nonlocal calls
                manifest = original(*args, **kwargs)
                calls += 1
                if calls == 1:
                    os.rename(artifact, parent / "original")
                    os.rename(replacement, artifact)
                return manifest

            with mock.patch.object(snapshot_tool, "_verify_artifact_descriptor", swap_after_verify):
                with self.assertRaisesRegex(snapshot_tool.SnapshotError, "replaced"):
                    snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))


if __name__ == "__main__":
    unittest.main()
