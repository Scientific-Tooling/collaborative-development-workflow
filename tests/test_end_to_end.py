from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, "scripts")

import contract_tool
import snapshot_tool


ROOT = Path(__file__).resolve().parents[1]


class EndToEndEvidenceTests(unittest.TestCase):
    def run_git(self, root: Path, *args: str, environment: dict[str, str] | None = None) -> None:
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        if environment:
            env.update(environment)
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def test_real_snapshot_and_comparison_bind_accepted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository = parent / "repository"
            repository.mkdir()
            self.run_git(repository, "-c", "init.defaultBranch=main", "init", "-q")
            self.run_git(repository, "config", "user.email", "test@example.invalid")
            self.run_git(repository, "config", "user.name", "Evidence Test")
            readme = repository / "README.md"
            readme.write_text("before\n", encoding="utf-8")
            self.run_git(repository, "add", "README.md")
            self.run_git(
                repository,
                "commit",
                "-qm",
                "base",
                environment={
                    "GIT_AUTHOR_DATE": "@1 +0000",
                    "GIT_COMMITTER_DATE": "@1 +0000",
                },
            )
            readme.write_text("after\n", encoding="utf-8")

            scope = {
                "version": "impact-scope-v2",
                "changed_paths": ["README.md"],
                "review_paths": ["README.md"],
                "direct_callers": [],
                "direct_consumers": [],
                "mapped_tests_or_configuration": [],
                "explicit_exclusions": [],
            }
            scope_path = parent / "scope.json"
            scope_path.write_text(json.dumps(scope), encoding="utf-8")
            artifact_path = parent / "artifact"
            created = snapshot_tool.create_snapshot(
                str(repository), str(scope_path), str(artifact_path)
            )
            verified = snapshot_tool.verify_artifact(
                str(artifact_path),
                str(scope_path),
                created["content_identity"],
                created["manifest_identity"],
            )
            compare_code, compared = snapshot_tool.compare_workspace(
                str(repository),
                str(artifact_path),
                str(scope_path),
                created["content_identity"],
                created["manifest_identity"],
            )
            self.assertTrue(verified["valid"])
            self.assertEqual(compare_code, 0, compared)

            manifest = json.loads(
                (artifact_path / "manifest.json").read_text(encoding="utf-8")
            )
            evidence = copy.deepcopy(
                contract_tool.load_json_file(str(ROOT / "examples" / "acceptance_evidence.json"))
            )
            review_round = evidence["review_rounds"][0]
            task = review_round["task_spec"]
            result = review_round["review_result"]
            artifact_proof = review_round["artifact_access_proof"]
            coverage = review_round["review_coverage_proof"]
            task.update(
                read_scope=["README.md"],
                impact_scope=scope,
                base_snapshot=manifest["base_git_identity"]["head"],
                base_content_identity=manifest["base_git_identity"]["index_tree"],
                snapshot_id=created["manifest_identity"],
                content_identity=created["content_identity"],
                artifact_path=str(artifact_path),
            )
            task["focused_checks"][0]["covered_scope"] = ["README.md"]
            result.update(
                completed_scope=["README.md"],
                base_snapshot=task["base_snapshot"],
                base_content_identity=task["base_content_identity"],
                snapshot_id=task["snapshot_id"],
                content_identity=task["content_identity"],
                reviewed_paths=["README.md"],
            )
            result_digest = contract_tool.digest_record(result, "role_result")
            scope_digest = contract_tool.digest_record(scope, "impact_scope")
            artifact_proof.update(
                base_snapshot=task["base_snapshot"],
                base_content_identity=task["base_content_identity"],
                snapshot_id=task["snapshot_id"],
                manifest_identity=task["snapshot_id"],
                content_identity=task["content_identity"],
                impact_scope_digest=scope_digest,
                scope_paths=["README.md"],
                reviewer_result_digest=result_digest,
            )
            coverage.update(
                snapshot_id=task["snapshot_id"],
                content_identity=task["content_identity"],
                impact_scope_digest=scope_digest,
                artifact_access_proof_digest=contract_tool.digest_record(
                    artifact_proof, "artifact_access_proof"
                ),
                reviewer_result_digest=result_digest,
                review_paths=["README.md"],
                completed_scope=["README.md"],
            )
            outcome = evidence["workflow_outcome"]
            outcome.update(
                capability_preflight_digest=contract_tool.digest_record(
                    evidence["capability_preflight"], "capability_preflight"
                ),
                final_review_round_digest=contract_tool.digest_record(
                    review_round, "review_round"
                ),
                snapshot_id=task["snapshot_id"],
                content_identity=task["content_identity"],
            )
            self.assertEqual(
                contract_tool.validate_record(evidence, "acceptance_evidence"), []
            )


if __name__ == "__main__":
    unittest.main()
