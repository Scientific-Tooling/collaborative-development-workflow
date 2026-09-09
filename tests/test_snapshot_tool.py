from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
            self.assertEqual(created["content_identity"], "3c71cda0bde6a6144bde515e0fa3a02c10ef9c86b70d6a6d1a948292e75d2ce2")
            self.assertEqual(created["manifest_identity"], "27c91a8f3947780277c3f0d3e7dc9b85663d48bf7474fc0a0c7e578df7a6071f")
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
                stream.seek(0)
                stream.truncate()
                json.dump(value, stream)
            with self.assertRaises(snapshot_tool.SnapshotError):
                snapshot_tool.create_snapshot(str(root), str(scope), str(parent / "artifact"))

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

            original_git_identity = snapshot_tool.git_identity
            calls = 0

            def changing_git_identity(path: Path) -> dict[str, str]:
                nonlocal calls
                calls += 1
                identity = original_git_identity(path)
                if calls >= 2:
                    identity = dict(identity, worktree_status_digest="0" * 64)
                return identity

            snapshot_tool.git_identity = changing_git_identity  # type: ignore[assignment]
            try:
                code, result = snapshot_tool.compare_workspace(str(root), str(artifact), str(scope))
            finally:
                snapshot_tool.git_identity = original_git_identity  # type: ignore[assignment]
            self.assertEqual(code, 1)
            self.assertFalse(result["match"])
            self.assertEqual(result["reason"], "GIT_IDENTITY_CHANGED_DURING_COMPARE")


if __name__ == "__main__":
    unittest.main()
