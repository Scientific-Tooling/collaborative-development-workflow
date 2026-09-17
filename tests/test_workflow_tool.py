from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))

import contract_tool
import snapshot_tool
import workflow_tool
from deadline_guard import fail_if_call_blocks
from git_fixture import GitRepositoryTemplate, fixture_git_environment


def _run_fixture_git(
    root: Path, *args: str, environment: dict[str, str] | None = None
) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        env=fixture_git_environment(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _build_workflow_repository(repository: Path) -> None:
    repository.mkdir()
    _run_fixture_git(repository, "-c", "init.defaultBranch=main", "init", "-q")
    _run_fixture_git(repository, "config", "user.email", "test@example.invalid")
    _run_fixture_git(repository, "config", "user.name", "Workflow Tool Test")
    (repository / "README.md").write_text("before\n", encoding="utf-8")
    (repository / "outside.txt").write_text("outside\n", encoding="utf-8")
    _run_fixture_git(repository, "add", "README.md", "outside.txt")
    _run_fixture_git(
        repository,
        "commit",
        "-qm",
        "base",
        environment={
            "GIT_AUTHOR_DATE": "@1 +0000",
            "GIT_COMMITTER_DATE": "@1 +0000",
        },
    )


class WorkflowToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.repository_template = GitRepositoryTemplate(
            _build_workflow_repository
        )
        cls.addClassCleanup(cls.repository_template.cleanup)

    def run_git(self, root: Path, *args: str) -> None:
        _run_fixture_git(root, *args)

    def test_extended_reviewer_profile_uses_five_hour_budget(self) -> None:
        self.assertEqual(
            workflow_tool.REVIEW_PROFILES["extended"]["wall_clock_seconds"],
            18000,
        )

    def make_repository(self, parent: Path) -> Path:
        repository = parent / "repository"
        return self.repository_template.copy_to(repository)

    def test_guide_is_the_single_machine_readable_policy_source(self) -> None:
        code, listing = workflow_tool.describe_guide(list_topics=True)
        self.assertEqual(code, 0, listing)
        self.assertEqual(listing["guide_version"], workflow_tool.GUIDE_VERSION)
        self.assertEqual(set(listing["topics"]), set(workflow_tool.GUIDES))

        for topic in listing["topics"]:
            with self.subTest(topic=topic):
                code, report = workflow_tool.describe_guide(topic)
                self.assertEqual(code, 0, report)
                self.assertEqual(report["topic"], topic)
                self.assertEqual(report["guide_version"], workflow_tool.GUIDE_VERSION)
                self.assertTrue(report["guide"])

        code, report = workflow_tool.describe_guide("not-a-topic")
        self.assertEqual(code, 2)
        self.assertFalse(report["ok"])

    def reviewer_task(self) -> dict:
        evidence = contract_tool.load_json_file(
            str(ROOT / "examples" / "acceptance_evidence.json")
        )
        return copy.deepcopy(evidence["review_rounds"][0]["task_spec"])

    def task_for_role(self, role: str) -> dict:
        task = self.reviewer_task()
        task["role"] = role
        if role != "reviewer":
            for field in (
                "snapshot_id",
                "content_identity",
                "artifact_path",
                "artifact_access_proof",
                "review_coverage_proof",
            ):
                task[field] = None
        if role == "implementer":
            task.update(
                mode="strict",
                binding_mode="runtime_atomic",
                binding_token="binding-1",
                execution="write",
                write_scope=task["impact_scope"]["changed_paths"],
            )
        return task

    def write_task(self, parent: Path, task: dict) -> Path:
        path = parent / "task-spec.json"
        path.write_text(json.dumps(task), encoding="utf-8")
        return path

    def build_live_evidence(
        self, parent: Path
    ) -> tuple[Path, Path, Path, dict]:
        repository = self.make_repository(parent)
        (repository / "README.md").write_text("after\n", encoding="utf-8")
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
        artifact = parent / "artifact"
        created = snapshot_tool.create_snapshot(
            str(repository), str(scope_path), str(artifact)
        )
        manifest = json.loads(
            (artifact / "manifest.json").read_text(encoding="utf-8")
        )

        evidence = copy.deepcopy(
            contract_tool.load_json_file(
                str(ROOT / "examples" / "acceptance_evidence.json")
            )
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
            artifact_path=str(artifact),
        )
        task["focused_checks"][0]["covered_scope"] = ["README.md"]
        if "run_id" in evidence["capability_preflight"]:
            evidence["capability_preflight"]["run_id"] = task["run_id"]
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
        evidence_path = parent / "evidence.json"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        return repository, artifact, evidence_path, evidence

    def build_observations(self, parent: Path) -> tuple[Path, Path, Path, dict]:
        repository, artifact, _, evidence = self.build_live_evidence(parent)
        review_round = evidence["review_rounds"][0]
        task = copy.deepcopy(review_round["task_spec"])
        for field in (
            "base_snapshot",
            "base_content_identity",
            "snapshot_id",
            "content_identity",
            "artifact_path",
            "artifact_access_proof",
            "review_coverage_proof",
        ):
            task[field] = None
        result = copy.deepcopy(review_round["review_result"])
        for field in (
            "version",
            "role",
            "mode",
            "run_id",
            "task_id",
            "invocation_id",
            "report_id",
            "base_snapshot",
            "base_content_identity",
            "snapshot_id",
            "content_identity",
            "artifact_access_proof",
            "review_coverage_proof",
        ):
            result.pop(field, None)
        observations = {
            "version": "workflow-observations-v2",
            "capability_preflight": copy.deepcopy(
                evidence["capability_preflight"]
            ),
            "review_rounds": [
                {
                    "task_spec": task,
                    "review_result": result,
                    "artifact_path": str(artifact),
                }
            ],
            "validation_checks": [
                {"id": "full", "status": "PASSED", "summary": "suite passed"}
            ],
            "full_validation_check_id": "full",
            "commit_requested": False,
        }
        observations_path = parent / "observations.json"
        observations_path.write_text(
            json.dumps(observations), encoding="utf-8"
        )
        return repository, artifact, observations_path, observations

    def test_generate_assembles_evidence_and_a_plain_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, artifact, observations_path, _ = self.build_observations(parent)
            evidence_path = parent / "generated-evidence.json"
            report_path = parent / "user-report.md"
            code, report = workflow_tool.generate_evidence(
                str(observations_path),
                str(repository),
                str(evidence_path),
                str(report_path),
            )
            self.assertEqual(code, 0, report)
            generated = contract_tool.load_json_file(str(evidence_path))
            self.assertEqual(
                contract_tool.validate_record(generated, "acceptance_evidence"), []
            )
            self.assertTrue(generated["workflow_outcome"]["accepted"])
            self.assertEqual(
                generated["workflow_outcome"]["outcome"], "ACCEPTED_PORTABLE"
            )
            final_round = generated["review_rounds"][-1]
            self.assertEqual(final_round["task_spec"]["artifact_path"], str(artifact))
            self.assertEqual(
                final_round["task_spec"]["artifact_access_proof"],
                final_round["artifact_access_proof"]["proof_id"],
            )
            self.assertEqual(
                final_round["task_spec"]["review_coverage_proof"],
                final_round["review_coverage_proof"]["proof_id"],
            )
            self.assertTrue(report_path.exists())
            user_report = report_path.read_text(encoding="utf-8")
            self.assertIn("Acceptance: ready for portable acceptance.", user_report)
            for internal_name in (
                "run_id",
                "snapshot_id",
                "content_identity",
                "acceptance-evidence digest",
            ):
                self.assertNotIn(internal_name, user_report)

            accept_code, accept_report = workflow_tool.accept_evidence(
                str(evidence_path),
                str(repository),
                generated["review_rounds"][-1]["task_spec"]["run_id"],
            )
            self.assertEqual(accept_code, 0, accept_report)

    def test_generate_does_not_claim_acceptance_without_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, _, observations_path, observations = self.build_observations(parent)
            observations["validation_checks"] = []
            observations.pop("full_validation_check_id")
            observations_path.write_text(json.dumps(observations), encoding="utf-8")
            evidence_path = parent / "generated-evidence.json"
            report_path = parent / "user-report.md"
            code, report = workflow_tool.generate_evidence(
                str(observations_path),
                str(repository),
                str(evidence_path),
                str(report_path),
            )
            self.assertEqual(code, 0, report)
            generated = contract_tool.load_json_file(str(evidence_path))
            outcome = generated["workflow_outcome"]
            self.assertFalse(outcome["accepted"])
            self.assertEqual(outcome["outcome"], "NOT_ACCEPTED")
            self.assertEqual(outcome["validation_status"], "NOT_RUN")
            self.assertEqual(outcome["reason"], "EVIDENCE_INVALID")
            self.assertEqual(
                contract_tool.validate_record(generated, "acceptance_evidence"), []
            )
            self.assertIn("not accepted", report["summary"])

    def test_generate_binds_a_new_snapshot_for_a_findings_round(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, _, observations_path, observations = self.build_observations(parent)
            first = observations["review_rounds"][0]
            first["review_result"].update(
                status="FINDINGS",
                summary="one issue found",
                findings=[
                    {
                        "id": "finding-1",
                        "severity": "MEDIUM",
                        "path": "README.md",
                        "line": 1,
                        "summary": "The change needs a follow-up.",
                        "evidence": "The first review observed the old text.",
                        "impact": "The result is incomplete.",
                        "fix": "Apply the follow-up change.",
                        "status": "OPEN",
                    }
                ],
            )
            (repository / "README.md").write_text("after fixed\n", encoding="utf-8")
            second_artifact = parent / "artifact-two"
            snapshot_tool.create_snapshot(
                str(repository), str(parent / "scope.json"), str(second_artifact)
            )
            second_task = copy.deepcopy(first["task_spec"])
            second_task["invocation_id"] = "invocation-2"
            second_result = copy.deepcopy(first["review_result"])
            second_result.update(
                status="CLEAN",
                summary="no issues found",
                findings=[],
            )
            observations["review_rounds"].append(
                {
                    "task_spec": second_task,
                    "review_result": second_result,
                    "artifact_path": str(second_artifact),
                }
            )
            observations_path.write_text(json.dumps(observations), encoding="utf-8")
            evidence_path = parent / "generated-evidence.json"
            code, report = workflow_tool.generate_evidence(
                str(observations_path),
                str(repository),
                str(evidence_path),
                None,
            )
            self.assertEqual(code, 0, report)
            generated = contract_tool.load_json_file(str(evidence_path))
            self.assertEqual(
                contract_tool.validate_record(generated, "acceptance_evidence"), []
            )
            self.assertTrue(generated["workflow_outcome"]["accepted"])
            self.assertEqual(len(generated["review_rounds"]), 2)
            self.assertEqual(
                generated["review_rounds"][1]["review_coverage_proof"][
                    "addressed_finding_ids"
                ],
                ["finding-1"],
            )
            self.assertNotEqual(
                generated["review_rounds"][0]["task_spec"]["content_identity"],
                generated["review_rounds"][1]["task_spec"]["content_identity"],
            )

    def test_generate_refuses_to_replace_an_existing_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, _, observations_path, _ = self.build_observations(parent)
            output = parent / "generated-evidence.json"
            output.write_text("keep\n", encoding="utf-8")
            code, report = workflow_tool.generate_evidence(
                str(observations_path),
                str(repository),
                str(output),
                None,
            )
            self.assertEqual(code, 2)
            self.assertFalse(report["ok"])
            self.assertEqual(output.read_text(encoding="utf-8"), "keep\n")

    def test_prompt_renders_a_valid_reviewer_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task = self.reviewer_task()
            task_path = self.write_task(parent, task)
            code, report = workflow_tool.render_prompt(str(task_path))

        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["role"], "reviewer")
        self.assertEqual(report["task_id"], task["task_id"])
        self.assertEqual(report["run_id"], task["run_id"])
        self.assertEqual(
            report["task_digest"],
            contract_tool.digest_record(
                contract_tool.canonicalize_record(task, "task_spec"), "task_spec"
            ),
        )
        self.assertIn("exact immutable artifact", report["prompt"])
        self.assertIn("one permitted status: CLEAN, FINDINGS", report["prompt"])
        self.assertIn("wall-clock, turn, and output ceilings", report["prompt"])
        self.assertIn("changed_paths must be []", report["prompt"])
        self.assertIn("CLEAN and FINDINGS both require at least one check", report["prompt"])
        template_text, rest = report["prompt"].split(
            "RESULT_TEMPLATE_JSON\n", 1
        )[1].split("\nITEM_SHAPES_JSON\n", 1)
        item_shapes_text, task_text = rest.split("\nTASK_SPEC_JSON\n", 1)
        result_template = json.loads(template_text)
        item_shapes = json.loads(item_shapes_text)
        rendered_task = json.loads(task_text)
        self.assertEqual(
            item_shapes["check"]["status"],
            "<one of: {}>".format(
                ", ".join(contract_tool.CONTRACT["statuses"]["check"])
            ),
        )
        self.assertEqual(
            set(item_shapes["finding"]),
            set(contract_tool.CONTRACT["nested_records"]["finding"]["required"]),
        )
        self.assertEqual(
            set(result_template["model_profile"]),
            {"model", "effort", "selection_outcome"},
        )
        self.assertEqual(result_template["reviewed_paths"], [])
        self.assertIn("copy impact_scope.review_paths exactly", report["prompt"])
        self.assertEqual(
            rendered_task, contract_tool.canonicalize_record(task, "task_spec")
        )

    def test_prompt_rejects_an_invalid_task_spec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_path = self.write_task(Path(temporary), {})
            code, report = workflow_tool.render_prompt(str(task_path))

        self.assertEqual(code, 2)
        self.assertFalse(report["ok"])
        self.assertEqual(report["stage"], "task_validation")
        self.assertTrue(report["errors"])

    def test_prompt_rejects_a_portable_implementer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task = self.reviewer_task()
            task["role"] = "implementer"
            task["impact_scope"]["changed_paths"] = []
            self.assertEqual(
                contract_tool.validate_record(task, "task_spec"), []
            )
            task_path = self.write_task(Path(temporary), task)
            code, report = workflow_tool.render_prompt(str(task_path))

        self.assertEqual(code, 2)
        self.assertEqual(report["stage"], "prompt_rendering")
        self.assertTrue(
            any(
                "portable mode keeps the main agent as the writer" in error
                for error in report["errors"]
            ),
            report,
        )

    def test_prompt_cli_emits_one_compact_json_report_and_no_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task_path = self.write_task(parent, self.reviewer_task())
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "workflow_tool.py"),
                    "prompt",
                    "--task-spec",
                    str(task_path),
                ],
                cwd=parent,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertEqual(
            result.stdout,
            json.dumps(
                report,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
        )
        self.assertTrue(report["ok"])

    def test_prompt_templates_are_role_specific_and_bounded(self) -> None:
        expected_payload_fields = {
            "planner": {
                "planning_role",
                "plan_or_critique",
                "milestones",
                "prerequisites",
                "write_scope",
                "integration_order",
                "mode",
                "exclusions",
                "focused_checks",
                "risks",
                "material_decisions",
            },
            "researcher": {
                "research_scope",
                "evidence",
                "open_questions",
                "recommendation",
            },
            "implementer": {"milestone", "remaining_risks", "next_milestone"},
            "verifier": {"verification_results", "reproducible_failures"},
            "reviewer": {"mode"},
        }
        for role, payload_fields in expected_payload_fields.items():
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                task = self.task_for_role(role)
                self.assertEqual(contract_tool.validate_record(task, "task_spec"), [])
                task_path = self.write_task(Path(temporary), task)
                code, report = workflow_tool.render_prompt(str(task_path))
                self.assertEqual(code, 0, report)
                prompt = report["prompt"]
                template_text = prompt.split("RESULT_TEMPLATE_JSON\n", 1)[1].split(
                    "\nITEM_SHAPES_JSON\n", 1
                )[0]
                template = json.loads(template_text)
                self.assertEqual(set(template["role_payload"]), payload_fields)
                task_json = json.dumps(
                    contract_tool.canonicalize_record(task, "task_spec"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                self.assertLessEqual(
                    len(prompt.encode("utf-8")) - len(task_json.encode("utf-8")),
                    4200,
                )
                if role == "reviewer":
                    self.assertIn("reviewed_paths", template)
                else:
                    self.assertNotIn("reviewed_paths", template)
                    self.assertIn("reviewer-only", prompt)

    def test_prompt_overhead_is_bounded_for_maximum_review_scope(self) -> None:
        task = self.reviewer_task()
        paths = [
            "scope/{:03d}-{}".format(index, "x" * 470)
            for index in range(128)
        ]
        task["read_scope"] = paths
        task["impact_scope"].update(
            changed_paths=[paths[0]],
            review_paths=paths,
            direct_callers=[],
            direct_consumers=[],
            mapped_tests_or_configuration=[],
            explicit_exclusions=[],
        )
        task["focused_checks"] = [
            {
                "version": "focused-check-v2",
                "id": "focused",
                "command_or_assertion": "check",
                "covered_scope": [paths[0]],
                "required": True,
            }
        ]
        task["budget"]["max_output_bytes"] = 4_194_304
        self.assertEqual(contract_tool.validate_record(task, "task_spec"), [])
        canonical = contract_tool.canonicalize_record(task, "task_spec")
        prompt = workflow_tool._render_prompt_text(canonical)
        task_json = json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertLessEqual(
            len(prompt.encode("utf-8")) - len(task_json.encode("utf-8")),
            4200,
        )
        self.assertNotIn(paths[-1], prompt.split("RESULT_TEMPLATE_JSON\n", 1)[1].split(
            "\nITEM_SHAPES_JSON\n", 1
        )[0])

    def test_prompt_rejects_an_impossible_result_budget(self) -> None:
        task = self.reviewer_task()
        canonical = contract_tool.canonicalize_record(task, "task_spec")
        minimum_result = workflow_tool._minimum_success_result(canonical)
        self.assertEqual(
            contract_tool.validate_record(minimum_result, "role_result"), []
        )
        contract_tool._validate_resolved_model_selection(
            canonical["model_request"],
            canonical["model_profile"],
            minimum_result["model_profile"],
        )
        minimum = workflow_tool._minimum_success_result_bytes(canonical)
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            task["budget"]["max_output_bytes"] = minimum - 1
            task_path = self.write_task(parent, task)
            code, report = workflow_tool.render_prompt(str(task_path))
            self.assertEqual(code, 2)
            self.assertEqual(report["stage"], "prompt_rendering")
            self.assertTrue(
                any("cannot hold the smallest" in error for error in report["errors"])
            )

            task["budget"]["max_output_bytes"] = minimum
            task_path.write_text(json.dumps(task), encoding="utf-8")
            code, report = workflow_tool.render_prompt(str(task_path))
            self.assertEqual(code, 0, report)
            self.assertEqual(report["minimum_success_result_bytes"], minimum)

    def test_prompt_budget_uses_the_shortest_allowed_model_resolution(self) -> None:
        task = self.reviewer_task()
        task["model_request"].update(
            strategy="explicit",
            requested_model="a-very-long-explicit-review-model-name",
            requested_effort="a-very-long-explicit-effort-name",
            fallback="allow_runtime_default",
        )
        self.assertEqual(contract_tool.validate_record(task, "task_spec"), [])
        canonical = contract_tool.canonicalize_record(task, "task_spec")
        minimum_result = workflow_tool._minimum_success_result(canonical)
        contract_tool._validate_resolved_model_selection(
            canonical["model_request"],
            canonical["model_profile"],
            minimum_result["model_profile"],
        )
        self.assertEqual(
            minimum_result["model_profile"]["selection_outcome"], "unknown"
        )

        fallback_result = copy.deepcopy(minimum_result)
        fallback_result["model_profile"] = {
            "model": "x",
            "effort": "x",
            "selection_outcome": "fallback",
        }
        honored_result = copy.deepcopy(minimum_result)
        honored_result["model_profile"] = {
            "model": task["model_request"]["requested_model"],
            "effort": task["model_request"]["requested_effort"],
            "selection_outcome": "honored",
        }

        def result_bytes(result: dict) -> int:
            return len(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )

        fallback_bytes = result_bytes(fallback_result)
        self.assertLessEqual(
            workflow_tool._minimum_success_result_bytes(canonical), fallback_bytes
        )
        self.assertLess(fallback_bytes, result_bytes(honored_result))
        task["budget"]["max_output_bytes"] = fallback_bytes
        with tempfile.TemporaryDirectory() as temporary:
            task_path = self.write_task(Path(temporary), task)
            code, report = workflow_tool.render_prompt(str(task_path))
        self.assertEqual(code, 0, report)

    def test_minimum_success_profile_honors_nonfallback_requests(self) -> None:
        cases = (
            (
                "explicit",
                {
                    "strategy": "explicit",
                    "requested_model": "required-model",
                    "requested_effort": "high",
                    "fallback": "fail",
                },
                {"model": "unknown", "effort": "unknown"},
            ),
            (
                "inherit",
                {
                    "strategy": "inherit",
                    "requested_model": None,
                    "requested_effort": None,
                    "fallback": "fail",
                },
                {"model": "parent-model", "effort": "medium"},
            ),
            (
                "different-required",
                {
                    "strategy": "runtime_default",
                    "requested_model": None,
                    "requested_effort": None,
                    "fallback": "fail",
                    "reviewer_independence": "different_required",
                    "comparison_model": "x",
                },
                {"model": "unknown", "effort": "unknown"},
            ),
        )
        for name, request_updates, dispatch_profile in cases:
            with self.subTest(name=name):
                task = self.reviewer_task()
                task["model_request"].update(request_updates)
                task["model_profile"] = dispatch_profile
                self.assertEqual(contract_tool.validate_record(task, "task_spec"), [])
                canonical = contract_tool.canonicalize_record(task, "task_spec")
                profile = workflow_tool._minimum_success_model_profile(canonical)
                contract_tool._validate_resolved_model_selection(
                    canonical["model_request"], canonical["model_profile"], profile
                )
                self.assertEqual(profile["selection_outcome"], "honored")
                if name == "explicit":
                    self.assertEqual(profile["model"], "required-model")
                    self.assertEqual(profile["effort"], "high")
                elif name == "inherit":
                    self.assertEqual(profile["model"], "parent-model")
                    self.assertEqual(profile["effort"], "medium")
                else:
                    self.assertNotEqual(profile["model"], "x")

    def test_init_cli_creates_valid_starters_from_an_unrelated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository = self.make_repository(parent)
            output = parent / "starter"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "workflow_tool.py"),
                    "init",
                    "--output",
                    str(output),
                    "--root",
                    str(repository),
                    "--changed-path",
                    "src/./main.py",
                    "--review-path",
                    "tests/test_main.py",
                ],
                cwd=parent,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result.stderr, "")
            report = json.loads(result.stdout)
            self.assertTrue(report["ok"])
            self.assertEqual(report["preflight_result"], "NOT_READY")
            self.assertEqual(
                result.stdout,
                json.dumps(
                    report,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
            )

            by_kind = {item["kind"]: item for item in report["files"]}
            self.assertEqual(
                set(by_kind),
                {
                    "capability_preflight",
                    "focused_check",
                    "impact_scope",
                    "model_request",
                    "task_spec",
                },
            )
            for item in report["files"]:
                record = contract_tool.load_json_file(item["path"])
                self.assertEqual(
                    contract_tool.validate_record(record, item["kind"]), []
                )
                self.assertEqual(
                    contract_tool.digest_record(record, item["kind"]), item["digest"]
                )
            scope = contract_tool.load_json_file(by_kind["impact_scope"]["path"])
            self.assertEqual(
                scope["review_paths"], ["src/main.py", "tests/test_main.py"]
            )
            preflight = contract_tool.load_json_file(
                by_kind["capability_preflight"]["path"]
            )
            self.assertFalse(any(preflight["capabilities"].values()))
            self.assertEqual(preflight["read_only_enforcement"], "unverified")
            task = contract_tool.load_json_file(by_kind["task_spec"]["path"])
            self.assertEqual(preflight["run_id"], task["run_id"])
            self.assertEqual(report["run_id"], task["run_id"])
            self.assertEqual(report["task_id"], task["task_id"])
            self.assertIn("model_request", task)
            self.assertEqual(task["model_request"]["strategy"], "inherit")

    def test_init_refuses_to_replace_an_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository = self.make_repository(parent)
            output = parent / "starter"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("keep\n", encoding="utf-8")
            code, report = workflow_tool.initialize(
                str(output), str(repository), ["README.md"], ["README.md"]
            )
            self.assertEqual(code, 2)
            self.assertFalse(report["ok"])
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
            self.assertEqual(
                sorted(path.name for path in parent.iterdir()),
                ["repository", "starter"],
            )

    def test_init_uses_fresh_identifiers_for_the_same_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository = self.make_repository(parent)
            reports = []
            for name in ("starter-one", "starter-two"):
                code, report = workflow_tool.initialize(
                    str(parent / name),
                    str(repository),
                    ["README.md"],
                    ["README.md"],
                )
                self.assertEqual(code, 0, report)
                reports.append(report)

            generated = []
            for report in reports:
                paths = {item["kind"]: item["path"] for item in report["files"]}
                task = contract_tool.load_json_file(paths["task_spec"])
                preflight = contract_tool.load_json_file(
                    paths["capability_preflight"]
                )
                self.assertEqual(preflight["run_id"], task["run_id"])
                generated.append((task["task_id"], task["run_id"]))
            self.assertNotEqual(generated[0][0], generated[1][0])
            self.assertNotEqual(generated[0][1], generated[1][1])

    def test_init_rejects_output_inside_repository_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository = self.make_repository(parent)
            equal_code, equal_report = workflow_tool.initialize(
                str(repository), str(repository), ["README.md"], ["README.md"]
            )
            self.assertEqual(equal_code, 2)
            self.assertIn(
                "output directory must be outside the repository root",
                equal_report["errors"],
            )

            output = repository / "generated" / "starter"
            code, report = workflow_tool.initialize(
                str(output), str(repository), ["README.md"], ["README.md"]
            )
            self.assertEqual(code, 2)
            self.assertFalse(report["ok"])
            self.assertIn(
                "output directory must be outside the repository root",
                report["errors"],
            )
            self.assertFalse(output.exists())
            self.assertFalse(output.parent.exists())

    def test_init_requires_absolute_root_and_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository = self.make_repository(parent)
            output = parent / "starter"

            code, report = workflow_tool.initialize(
                str(output), "repository", ["README.md"], ["README.md"]
            )
            self.assertEqual(code, 2)
            self.assertIn(
                "repository root must be an absolute path", report["errors"]
            )

            code, report = workflow_tool.initialize(
                "starter", str(repository), ["README.md"], ["README.md"]
            )
            self.assertEqual(code, 2)
            self.assertIn(
                "output directory must be an absolute path", report["errors"]
            )
            self.assertFalse(output.exists())

    def test_init_rejects_double_slash_aliases_inside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository = self.make_repository(parent)
            cases = (
                ("/" + str(repository), str(repository / "starter-root-alias")),
                (str(repository), "/" + str(repository / "starter-output-alias")),
            )
            for root_arg, output_arg in cases:
                with self.subTest(root_arg=root_arg, output_arg=output_arg):
                    code, report = workflow_tool.initialize(
                        output_arg,
                        root_arg,
                        ["README.md"],
                        ["README.md"],
                    )
                    self.assertEqual(code, 2)
                    self.assertIn(
                        "output directory must be outside the repository root",
                        report["errors"],
                    )
                    self.assertFalse(
                        snapshot_tool._canonical_absolute_path(output_arg).exists()
                    )

    def test_accept_verifies_the_artifact_and_workspace_live(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, artifact, evidence_path, evidence = self.build_live_evidence(
                parent
            )
            run_id = evidence["review_rounds"][-1]["task_spec"]["run_id"]
            code, report = workflow_tool.accept_evidence(
                str(evidence_path), str(repository), run_id
            )
            self.assertEqual(code, 0, report)
            self.assertNotIn("accepted", report)
            self.assertTrue(report["evidence_and_review_scope_valid"])
            self.assertFalse(report["helper_confirmed_acceptance"])
            self.assertEqual(report["recorded_outcome"], "ACCEPTED_PORTABLE")
            self.assertEqual(
                report["live_confirmation_required"],
                list(workflow_tool.LIVE_CONFIRMATIONS),
            )
            self.assertIn(
                "writers_paused_and_post_freeze_immutability",
                report["live_confirmation_required"],
            )
            self.assertTrue(report["artifact_verified"])
            self.assertTrue(report["base_identity_match"])
            self.assertTrue(report["review_scope_match"])
            self.assertNotIn("workspace_match", report)
            self.assertEqual(report["artifact"], str(artifact))
            self.assertEqual(report["run_id"], run_id)
            self.assertIn(
                report["preflight_freshness"]["status"],
                {"RUN_BOUND_NOT_TIME_VERIFIED"},
            )

    def test_accept_rejects_a_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, artifact, evidence_path, evidence = self.build_live_evidence(parent)
            artifact.rename(parent / "moved-artifact")
            run_id = evidence["review_rounds"][-1]["task_spec"]["run_id"]
            code, report = workflow_tool.accept_evidence(
                str(evidence_path), str(repository), run_id
            )
            self.assertEqual(code, 2)
            self.assertFalse(report["ok"])
            self.assertEqual(report["stage"], "artifact_verification")
            self.assertTrue(report["evidence_valid"])
            self.assertEqual(
                report["preflight_freshness"]["status"],
                "RUN_BOUND_NOT_TIME_VERIFIED",
            )

    def test_accept_rejects_artifact_inside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, artifact, evidence_path, evidence = self.build_live_evidence(
                parent
            )
            inside_artifact = repository / "review-artifact"
            os.chmod(artifact, 0o700)
            artifact.rename(inside_artifact)
            review_round = evidence["review_rounds"][-1]
            task = review_round["task_spec"]

            for artifact_path in (
                str(inside_artifact),
                "/" + str(inside_artifact),
            ):
                with self.subTest(artifact_path=artifact_path):
                    task["artifact_path"] = artifact_path
                    evidence["workflow_outcome"]["final_review_round_digest"] = (
                        contract_tool.digest_record(review_round, "review_round")
                    )
                    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
                    code, report = workflow_tool.accept_evidence(
                        str(evidence_path), str(repository), task["run_id"]
                    )
                    self.assertEqual(code, 2)
                    self.assertEqual(report["stage"], "artifact_binding")
                    self.assertIn(
                        "review artifact must be outside the repository root",
                        report["errors"],
                    )

    def test_accept_rejects_a_workspace_change_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, _, evidence_path, evidence = self.build_live_evidence(parent)
            (repository / "README.md").write_text("changed again\n", encoding="utf-8")
            run_id = evidence["review_rounds"][-1]["task_spec"]["run_id"]
            code, report = workflow_tool.accept_evidence(
                str(evidence_path), str(repository), run_id
            )
            self.assertEqual(code, 1)
            self.assertFalse(report["ok"])
            self.assertTrue(report["artifact_verified"])
            self.assertFalse(report["review_scope_match"])
            self.assertNotIn("workspace_match", report)
            self.assertEqual(report["stage"], "review_scope_comparison")

    def test_accept_binds_claimed_base_identities_to_the_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, _, evidence_path, evidence = self.build_live_evidence(parent)
            review_round = evidence["review_rounds"][-1]
            task = review_round["task_spec"]
            result = review_round["review_result"]
            artifact_proof = review_round["artifact_access_proof"]
            coverage = review_round["review_coverage_proof"]

            task["base_snapshot"] = "false-head"
            task["base_content_identity"] = "false-index"
            result["base_snapshot"] = task["base_snapshot"]
            result["base_content_identity"] = task["base_content_identity"]
            artifact_proof["base_snapshot"] = task["base_snapshot"]
            artifact_proof["base_content_identity"] = task["base_content_identity"]
            result_digest = contract_tool.digest_record(result, "role_result")
            artifact_proof["reviewer_result_digest"] = result_digest
            coverage["reviewer_result_digest"] = result_digest
            coverage["artifact_access_proof_digest"] = contract_tool.digest_record(
                artifact_proof, "artifact_access_proof"
            )
            evidence["workflow_outcome"][
                "final_review_round_digest"
            ] = contract_tool.digest_record(review_round, "review_round")
            self.assertEqual(
                contract_tool.validate_record(evidence, "acceptance_evidence"), []
            )
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

            code, report = workflow_tool.accept_evidence(
                str(evidence_path), str(repository), task["run_id"]
            )
            self.assertEqual(code, 2)
            self.assertEqual(report["stage"], "artifact_evidence_binding")
            self.assertTrue(report["artifact_verified"])
            self.assertFalse(report["base_identity_match"])

    def test_accept_does_not_claim_unscoped_tracked_content_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, _, evidence_path, evidence = self.build_live_evidence(parent)
            (repository / "outside.txt").write_text("changed outside scope\n", encoding="utf-8")
            run_id = evidence["review_rounds"][-1]["task_spec"]["run_id"]
            code, report = workflow_tool.accept_evidence(
                str(evidence_path), str(repository), run_id
            )
            self.assertEqual(code, 0, report)
            self.assertTrue(report["review_scope_match"])
            self.assertNotIn("workspace_match", report)

    def test_accept_checks_an_expected_run_id_before_artifact_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, artifact, evidence_path, _ = self.build_live_evidence(parent)
            artifact.rename(parent / "moved-artifact")
            code, report = workflow_tool.accept_evidence(
                str(evidence_path), str(repository), "different-run"
            )
            self.assertEqual(code, 2)
            self.assertEqual(report["stage"], "run_binding")
            self.assertEqual(report["expected_run_id"], "different-run")

    def test_accept_requires_the_live_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            repository, _, evidence_path, _ = self.build_live_evidence(parent)
            code, report = workflow_tool.accept_evidence(
                str(evidence_path), str(repository), None
            )
            self.assertEqual(code, 2)
            self.assertEqual(report["stage"], "run_binding")
            self.assertIn("accept requires the live workflow run ID", report["errors"])

    def test_accept_requires_an_absolute_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            _, _, evidence_path, evidence = self.build_live_evidence(parent)
            run_id = evidence["review_rounds"][-1]["task_spec"]["run_id"]
            code, report = workflow_tool.accept_evidence(
                str(evidence_path), "repository", run_id
            )
            self.assertEqual(code, 2)
            self.assertEqual(report["stage"], "argument_binding")
            self.assertIn(
                "repository root must be an absolute path", report["errors"]
            )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "named pipes require POSIX")
    def test_accept_rejects_evidence_fifo_without_waiting_for_a_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            evidence_fifo = parent / "evidence.fifo"
            os.mkfifo(evidence_fifo)
            with fail_if_call_blocks():
                code, report = workflow_tool.accept_evidence(
                    str(evidence_fifo), str(parent), "run-1"
                )
            self.assertEqual(code, 2)
            self.assertEqual(report["stage"], "evidence_loading")
            self.assertTrue(
                any("input is not a regular file" in error for error in report["errors"])
            )


if __name__ == "__main__":
    unittest.main()
