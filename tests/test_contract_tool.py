from __future__ import annotations

import json
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, "scripts")

import contract_tool


class ContractToolTests(unittest.TestCase):
    def impact_scope(self) -> dict:
        return {
            "version": "impact-scope-v2",
            "changed_paths": ["src/./main.py", "README.md"],
            "review_paths": ["src/./main.py", "README.md", "tests/test_main.py"],
            "direct_callers": ["module:entry"],
            "direct_consumers": ["tests/test_main.py"],
            "mapped_tests_or_configuration": ["pyproject.toml"],
            "explicit_exclusions": ["docs/legacy"],
        }

    def role_result(self, role: str = "planner", status: str = "PLAN_READY") -> dict:
        return {
            "version": "role-result-v2",
            "role": role,
            "status": status,
            "summary": "bounded result",
            "completed_scope": ["src/main.py"],
            "changed_paths": [],
            "checks": [{"id": "focused", "status": "PASSED", "summary": "check passed"}],
            "risks": [{"id": "risk", "status": "OPEN", "summary": "review risk"}],
            "blocker_or_input": None,
            "attention_required": [],
            "next_action": None,
        }

    def golden_records(self) -> dict[str, dict]:
        scope = self.impact_scope()
        completion = {
            "version": "runtime-completion-event-v2",
            "event_type": "RUNTIME_COMPLETION_EVENT",
            "event_id": "event-1",
            "run_id": "run-1",
            "task_id": "task-1",
            "invocation_id": "invocation-1",
            "attempt": 1,
            "agent_id": "agent-1",
            "agent_channel": "channel-1",
            "transport_invocation_association": "association-1",
            "runtime_target": "target-1",
            "binding_token": "binding-1",
            "clock_source": "monotonic",
            "status": "completed",
            "result_delivery_confirmed": "yes",
            "terminal_at": "123.4",
            "terminal_reason": "result delivered",
        }
        terminal = {
            "version": "runtime-terminal-event-v2",
            "event_type": "RUNTIME_TERMINAL_EVENT",
            "event_id": "event-2",
            "run_id": "run-1",
            "task_id": "task-1",
            "invocation_id": "invocation-1",
            "attempt": 1,
            "agent_id": "agent-1",
            "agent_channel": "channel-1",
            "transport_invocation_association": "association-1",
            "runtime_target": "target-1",
            "binding_token": "binding-1",
            "clock_source": "monotonic",
            "status": "failed",
            "child_started": "yes",
            "stop_confirmed": "yes",
            "cancel_confirmed_at": None,
            "terminal_at": "123.4",
            "terminal_reason": "execution failed",
        }
        stop = {
            "version": "runtime-stop-event-v2",
            "event_type": "RUNTIME_STOP_EVENT",
            "event_id": "event-4",
            "run_id": "run-1",
            "task_id": "task-1",
            "invocation_id": "invocation-1",
            "attempt": 1,
            "agent_id": "agent-1",
            "agent_channel": "channel-1",
            "transport_invocation_association": "association-1",
            "runtime_target": "target-1",
            "binding_token": "binding-1",
            "clock_source": "monotonic",
            "status": "stopped",
            "stop_confirmed": "yes",
            "stopped_at": "123.5",
            "stop_reason": "deadline",
        }
        task = {
            "version": "task-spec-v2",
            "task_id": "task-1",
            "parent_task_id": None,
            "run_id": "run-1",
            "invocation_id": "invocation-1",
            "binding_token": None,
            "binding_mode": "transport_bound_provisional",
            "role": "reviewer",
            "mode": "portable",
            "objective": "review",
            "depends_on": [],
            "acceptance_criteria": [{"criterion_id": "criterion-1", "status": "pending", "summary": "review"}],
            "read_scope": ["README.md", "src/main.py", "tests/test_main.py"],
            "write_scope": [],
            "impact_scope": scope,
            "base_snapshot": "snapshot-1",
            "base_content_identity": "content-1",
            "focused_checks": [],
            "execution": "read-only",
            "isolation": "frozen-artifact",
            "budget": {
                "version": "execution-budget-v2",
                "wall_clock_seconds": 600,
                "max_turns": 8,
                "max_output_bytes": 65536,
            },
            "resumable": False,
            "model_profile": {"model": "unknown"},
            "full_suite_owner": "main",
            "snapshot_id": "a" * 64,
            "content_identity": "b" * 64,
            "artifact_path": "/tmp/artifact",
            "artifact_access_proof": "proof-1",
            "review_coverage_proof": "coverage-1",
        }
        handoff = {
            "version": "context-handoff-v2",
            "handoff_id": "handoff-1",
            "task_id": "task-1",
            "run_id": "run-1",
            "parent_task_id": None,
            "source_task_id": None,
            "source_run_id": None,
            "mode": "independent",
            "original_request": "start a separate bounded task",
            "accepted_plan": [{"step_id": "step-1", "status": "done", "summary": "scope"}],
            "baseline": {"branch": "main", "head": "abc123", "status_digest": "status123"},
            "scope": scope,
            "acceptance_criteria": [{"criterion_id": "criterion-1", "status": "pending", "summary": "review"}],
            "focused_checks": [],
            "checkpoint": None,
            "artifacts": [],
            "active_work": [],
            "open_risks": [],
            "decisions": [],
        }
        capability = {
            "version": "capability-preflight-v2",
            "mode": "portable",
            "result": "PORTABLE_READY",
            "capabilities": {name: True for name in contract_tool.PORTABLE_CAPABILITIES},
            "missing": [],
            "authority": "observed_tool_surface",
        }
        workflow = {
            "version": "workflow-outcome-v2",
            "mode": "portable",
            "outcome": "ACCEPTED_PORTABLE",
            "accepted": True,
            "review_status": "CLEAN",
            "reason": None,
            "validation_status": "PASSED",
            "capability_preflight_digest": "c" * 64,
            "final_review_round_digest": "d" * 64,
            "validation_checks": [
                {"id": "full", "status": "PASSED", "summary": "suite passed"}
            ],
            "full_validation_check_id": "full",
            "commit_requested": False,
            "commit_status": "NOT_REQUESTED",
            "commit_blocker": None,
            "snapshot_id": "a" * 64,
            "content_identity": "b" * 64,
            "commit_id": None,
        }
        focused = {
            "version": "focused-check-v2",
            "id": "focused",
            "command_or_assertion": "python -m unittest",
            "covered_scope": ["scripts/contract_tool.py"],
            "required": True,
        }
        return {
            "impact_scope": scope,
            "focused_check": focused,
            "capability_preflight": capability,
            "role_result": self.role_result(),
            "workflow_outcome": workflow,
            "task_spec": task,
            "runtime_completion_event": completion,
            "runtime_terminal_event": terminal,
            "runtime_stop_event": stop,
            "runtime_event_sequence": {
                "version": "runtime-event-sequence-v2",
                "events": [stop, dict(terminal, terminal_at="123.5")],
            },
            "context_handoff": handoff,
        }

    def acceptance_evidence(self) -> dict:
        records = self.golden_records()
        task = copy.deepcopy(records["task_spec"])
        task["acceptance_criteria"][0]["status"] = "met"
        task["focused_checks"] = [copy.deepcopy(records["focused_check"])]
        task["focused_checks"][0]["covered_scope"] = ["src/main.py", "README.md"]
        result = self.role_result("reviewer", "CLEAN")
        result.update(
            {
                "mode": "portable",
                "run_id": task["run_id"],
                "task_id": task["task_id"],
                "invocation_id": task["invocation_id"],
                "report_id": "report-1",
                "base_snapshot": task["base_snapshot"],
                "base_content_identity": task["base_content_identity"],
                "snapshot_id": task["snapshot_id"],
                "content_identity": task["content_identity"],
                "artifact_access_proof": task["artifact_access_proof"],
                "review_coverage_proof": task["review_coverage_proof"],
                "reviewed_paths": ["README.md", "src/main.py", "tests/test_main.py"],
                "findings": [],
                "risks": [],
            }
        )
        result_digest = contract_tool.digest_record(result, "role_result")
        scope_digest = contract_tool.digest_record(task["impact_scope"], "impact_scope")
        artifact = {
            "version": "artifact-access-proof-v2",
            "proof_id": task["artifact_access_proof"],
            "mode": task["mode"],
            "base_snapshot": task["base_snapshot"],
            "base_content_identity": task["base_content_identity"],
            "snapshot_id": task["snapshot_id"],
            "manifest_identity": task["snapshot_id"],
            "content_identity": task["content_identity"],
            "impact_scope_digest": scope_digest,
            "scope_paths": ["README.md", "src/main.py", "tests/test_main.py"],
            "reviewer_result_digest": result_digest,
            "artifact_verification_status": "PASSED",
            "workspace_compare_status": "PASSED",
        }
        coverage = {
            "version": "review-coverage-proof-v2",
            "proof_id": task["review_coverage_proof"],
            "round": 1,
            "snapshot_id": task["snapshot_id"],
            "content_identity": task["content_identity"],
            "impact_scope_digest": scope_digest,
            "artifact_access_proof_digest": contract_tool.digest_record(
                artifact, "artifact_access_proof"
            ),
            "reviewer_result_digest": result_digest,
            "review_paths": ["README.md", "src/main.py", "tests/test_main.py"],
            "completed_scope": result["completed_scope"],
            "required_check_ids": ["focused"],
            "passed_check_ids": ["focused"],
            "prior_reviewer_result_digest": None,
            "addressed_finding_ids": [],
            "coverage_status": "COMPLETE",
        }
        review_round = {
            "version": "review-round-v2",
            "round": 1,
            "task_spec": task,
            "review_result": result,
            "artifact_access_proof": artifact,
            "review_coverage_proof": coverage,
        }
        preflight = copy.deepcopy(records["capability_preflight"])
        outcome = copy.deepcopy(records["workflow_outcome"])
        outcome["capability_preflight_digest"] = contract_tool.digest_record(
            preflight, "capability_preflight"
        )
        outcome["final_review_round_digest"] = contract_tool.digest_record(
            review_round, "review_round"
        )
        return {
            "version": "acceptance-evidence-v2",
            "capability_preflight": preflight,
            "review_rounds": [review_round],
            "workflow_outcome": outcome,
        }

    def two_round_evidence(self) -> dict:
        def rebind(review_round: dict) -> None:
            task = review_round["task_spec"]
            result = review_round["review_result"]
            artifact = review_round["artifact_access_proof"]
            coverage = review_round["review_coverage_proof"]
            result_digest = contract_tool.digest_record(result, "role_result")
            artifact.update(
                proof_id=task["artifact_access_proof"],
                snapshot_id=task["snapshot_id"],
                manifest_identity=task["snapshot_id"],
                content_identity=task["content_identity"],
                reviewer_result_digest=result_digest,
            )
            coverage.update(
                proof_id=task["review_coverage_proof"],
                round=review_round["round"],
                snapshot_id=task["snapshot_id"],
                content_identity=task["content_identity"],
                reviewer_result_digest=result_digest,
                artifact_access_proof_digest=contract_tool.digest_record(
                    artifact, "artifact_access_proof"
                ),
            )

        evidence = self.acceptance_evidence()
        first = evidence["review_rounds"][0]
        first["review_result"].update(
            status="FINDINGS",
            findings=[
                {
                    "id": "finding-1",
                    "severity": "HIGH",
                    "status": "OPEN",
                    "path": "src/main.py",
                    "line": 1,
                    "summary": "bug",
                    "evidence": "observed",
                    "impact": "wrong result",
                    "fix": "repair",
                }
            ],
        )
        rebind(first)
        second = copy.deepcopy(first)
        second["round"] = 2
        second["task_spec"].update(
            invocation_id="invocation-2",
            snapshot_id="c" * 64,
            content_identity="d" * 64,
            artifact_access_proof="proof-2",
            review_coverage_proof="coverage-2",
        )
        second["review_result"].update(
            invocation_id="invocation-2",
            report_id="report-2",
            status="CLEAN",
            snapshot_id="c" * 64,
            content_identity="d" * 64,
            artifact_access_proof="proof-2",
            review_coverage_proof="coverage-2",
            findings=[],
        )
        second["review_coverage_proof"].update(
            prior_reviewer_result_digest=contract_tool.digest_record(
                first["review_result"], "role_result"
            ),
            addressed_finding_ids=["finding-1"],
        )
        rebind(second)
        evidence["review_rounds"] = [first, second]
        evidence["workflow_outcome"].update(
            snapshot_id="c" * 64,
            content_identity="d" * 64,
            final_review_round_digest=contract_tool.digest_record(second, "review_round"),
        )
        return evidence

    def test_existing_record_digests_are_golden(self) -> None:
        expected = {
            "impact_scope": "4ce5162032edabc332e71c84a22d4aafb684b01649d78d92dd67c110b7cf5edc",
            "focused_check": "2bb24f579713cd777c7a596e6e1bd30ca33e9f9ef15eaaa19eee02b84c4df85a",
            "capability_preflight": "cffd5cf2dc3876692c4bbb4a8d01ae75db6ab156877eb4381dd8bf920a4f6273",
            "role_result": "b37d64d311f784ca7cbea4681d92becb334cf1da0b3a7bcd561b10a2c12fb18b",
            "workflow_outcome": "fe281cc1bfd24a839e2c48c7ed9a6048c13ae37b3f11aa67d73df1a2b26df827",
            "task_spec": "78ba6dc8d1feabb1025710e8d7654f74e2828c3efc25733c6aeb478a3febbb69",
            "runtime_completion_event": "35bf53446008f84a8ce8ba9cdadc849f16602dfa0071f300e879bd15f42d46b3",
            "runtime_terminal_event": "e2534b584bc4f0c2a34e14b49fc427a759fe7a3cc2f00319722ab09adb462d71",
            "runtime_stop_event": "4b7e7c6db8206befdb31bad2b79457ec0accf210b656f15e1cad6311bf14da3b",
            "runtime_event_sequence": "81db69007e02192f2368cb6d380b89373d486cdd2049b164814ec443313f7282",
            "context_handoff": "68507785858c9b34bf0495abd9fa23a02983899b418d222b369e87042dfd2c93",
        }
        records = self.golden_records()
        self.assertEqual(set(records), set(expected))
        for kind, record in records.items():
            self.assertEqual(contract_tool.digest_record(record, kind), expected[kind], kind)

    def test_structured_acceptance_evidence_binds_every_record(self) -> None:
        evidence = self.acceptance_evidence()
        self.assertEqual(contract_tool.validate_record(evidence, "acceptance_evidence"), [])
        for path, value in (
            (("review_rounds", 0, "review_result", "content_identity"), "f" * 64),
            (("review_rounds", 0, "artifact_access_proof", "scope_paths"), ["README.md"]),
            (("review_rounds", 0, "review_coverage_proof", "passed_check_ids"), []),
            (("review_rounds", 0, "review_coverage_proof", "content_identity"), "f" * 64),
            (("workflow_outcome", "final_review_round_digest"), "0" * 64),
        ):
            tampered = copy.deepcopy(evidence)
            target: object = tampered
            for component in path[:-1]:
                target = target[component]  # type: ignore[index]
            target[path[-1]] = value  # type: ignore[index]
            self.assertTrue(
                contract_tool.validate_record(tampered, "acceptance_evidence"), path
            )

    def test_accepted_outcome_requires_acceptance_state(self) -> None:
        outcome = copy.deepcopy(self.golden_records()["workflow_outcome"])
        outcome.update(accepted=False, review_status="FINDINGS", validation_status="FAILED")
        self.assertTrue(contract_tool.validate_record(outcome, "workflow_outcome"))

    def test_accepted_evidence_requires_met_criteria_and_one_mode(self) -> None:
        evidence = self.acceptance_evidence()
        evidence["review_rounds"][0]["task_spec"]["acceptance_criteria"][0]["status"] = "blocked"
        evidence["workflow_outcome"]["final_review_round_digest"] = contract_tool.digest_record(
            evidence["review_rounds"][0], "review_round"
        )
        self.assertTrue(contract_tool.validate_record(evidence, "acceptance_evidence"))

        task = copy.deepcopy(self.golden_records()["task_spec"])
        task["acceptance_criteria"] = []
        self.assertTrue(contract_tool.validate_record(task, "task_spec"))

        evidence = self.acceptance_evidence()
        review_round = evidence["review_rounds"][0]
        review_round["task_spec"]["mode"] = "strict"
        review_round["task_spec"]["binding_mode"] = "runtime_atomic"
        review_round["task_spec"]["binding_token"] = "binding-1"
        review_round["review_result"]["mode"] = "strict"
        review_round["artifact_access_proof"]["mode"] = "strict"
        result_digest = contract_tool.digest_record(review_round["review_result"], "role_result")
        review_round["artifact_access_proof"]["reviewer_result_digest"] = result_digest
        review_round["review_coverage_proof"]["reviewer_result_digest"] = result_digest
        review_round["review_coverage_proof"]["artifact_access_proof_digest"] = contract_tool.digest_record(
            review_round["artifact_access_proof"], "artifact_access_proof"
        )
        evidence["workflow_outcome"]["final_review_round_digest"] = contract_tool.digest_record(
            review_round, "review_round"
        )
        self.assertTrue(contract_tool.validate_record(evidence, "acceptance_evidence"))

    def test_semantic_identifiers_are_unique(self) -> None:
        evidence = self.acceptance_evidence()
        duplicate = copy.deepcopy(evidence["review_rounds"][0]["task_spec"]["focused_checks"][0])
        duplicate["command_or_assertion"] = "different command"
        evidence["review_rounds"][0]["task_spec"]["focused_checks"].append(duplicate)
        self.assertTrue(contract_tool.validate_record(evidence, "acceptance_evidence"))

        result = self.role_result()
        result["checks"].append({"id": "focused", "status": "FAILED", "summary": "duplicate id"})
        self.assertTrue(contract_tool.validate_record(result, "role_result"))

    def test_required_checks_must_cover_changed_paths(self) -> None:
        evidence = self.acceptance_evidence()
        evidence["review_rounds"][0]["task_spec"]["focused_checks"][0]["covered_scope"] = [
            "src/main.py/smaller-scope"
        ]
        self.assertTrue(contract_tool.validate_record(evidence, "acceptance_evidence"))

    def test_review_rounds_cannot_replay_an_older_snapshot_identity(self) -> None:
        def finding(identifier: str) -> dict:
            return {
                "id": identifier,
                "severity": "HIGH",
                "status": "OPEN",
                "path": "src/main.py",
                "line": 1,
                "summary": "bug",
                "evidence": "observed",
                "impact": "wrong result",
                "fix": "repair",
            }

        def rebind(review_round: dict) -> None:
            task = review_round["task_spec"]
            result = review_round["review_result"]
            artifact = review_round["artifact_access_proof"]
            coverage = review_round["review_coverage_proof"]
            result_digest = contract_tool.digest_record(result, "role_result")
            artifact.update(
                proof_id=task["artifact_access_proof"],
                snapshot_id=task["snapshot_id"],
                manifest_identity=task["snapshot_id"],
                content_identity=task["content_identity"],
                reviewer_result_digest=result_digest,
            )
            coverage.update(
                proof_id=task["review_coverage_proof"],
                round=review_round["round"],
                snapshot_id=task["snapshot_id"],
                content_identity=task["content_identity"],
                reviewer_result_digest=result_digest,
                artifact_access_proof_digest=contract_tool.digest_record(
                    artifact, "artifact_access_proof"
                ),
            )

        evidence = self.acceptance_evidence()
        first = evidence["review_rounds"][0]
        first["review_result"].update(status="FINDINGS", findings=[finding("finding-1")])
        rebind(first)

        second = copy.deepcopy(first)
        second["round"] = 2
        second["task_spec"].update(
            invocation_id="invocation-2",
            snapshot_id="c" * 64,
            content_identity="d" * 64,
            artifact_access_proof="proof-2",
            review_coverage_proof="coverage-2",
        )
        second["review_result"].update(
            invocation_id="invocation-2",
            report_id="report-2",
            snapshot_id="c" * 64,
            content_identity="d" * 64,
            artifact_access_proof="proof-2",
            review_coverage_proof="coverage-2",
            findings=[finding("finding-2")],
        )
        second["review_coverage_proof"].update(
            prior_reviewer_result_digest=contract_tool.digest_record(
                first["review_result"], "role_result"
            ),
            addressed_finding_ids=["finding-1"],
        )
        rebind(second)

        third = copy.deepcopy(second)
        third["round"] = 3
        third["task_spec"].update(
            invocation_id="invocation-3",
            snapshot_id="a" * 64,
            content_identity="b" * 64,
            artifact_access_proof="proof-3",
            review_coverage_proof="coverage-3",
        )
        third["review_result"].update(
            invocation_id="invocation-3",
            report_id="report-3",
            status="CLEAN",
            snapshot_id="a" * 64,
            content_identity="b" * 64,
            artifact_access_proof="proof-3",
            review_coverage_proof="coverage-3",
            findings=[],
        )
        third["review_coverage_proof"].update(
            prior_reviewer_result_digest=contract_tool.digest_record(
                second["review_result"], "role_result"
            ),
            addressed_finding_ids=["finding-2"],
        )
        rebind(third)
        evidence["review_rounds"] = [first, second, third]
        evidence["workflow_outcome"].update(
            snapshot_id="a" * 64,
            content_identity="b" * 64,
            final_review_round_digest=contract_tool.digest_record(third, "review_round"),
        )
        self.assertTrue(contract_tool.validate_record(evidence, "acceptance_evidence"))

    def test_review_rounds_cannot_rewrite_task_semantics_or_scope(self) -> None:
        evidence = self.two_round_evidence()
        self.assertEqual(contract_tool.validate_record(evidence, "acceptance_evidence"), [])
        second = evidence["review_rounds"][1]
        task = second["task_spec"]
        result = second["review_result"]
        artifact = second["artifact_access_proof"]
        coverage = second["review_coverage_proof"]
        narrowed_scope = {
            "version": "impact-scope-v2",
            "changed_paths": [],
            "review_paths": ["unrelated.txt"],
            "direct_callers": [],
            "direct_consumers": [],
            "mapped_tests_or_configuration": [],
            "explicit_exclusions": [],
        }
        task.update(
            impact_scope=narrowed_scope,
            read_scope=["unrelated.txt"],
        )
        task["focused_checks"][0]["covered_scope"] = ["unrelated.txt"]
        result.update(completed_scope=["unrelated.txt"], reviewed_paths=["unrelated.txt"])
        result_digest = contract_tool.digest_record(result, "role_result")
        scope_digest = contract_tool.digest_record(narrowed_scope, "impact_scope")
        artifact.update(
            impact_scope_digest=scope_digest,
            scope_paths=["unrelated.txt"],
            reviewer_result_digest=result_digest,
        )
        coverage.update(
            impact_scope_digest=scope_digest,
            artifact_access_proof_digest=contract_tool.digest_record(
                artifact, "artifact_access_proof"
            ),
            reviewer_result_digest=result_digest,
            review_paths=["unrelated.txt"],
            completed_scope=["unrelated.txt"],
        )
        evidence["workflow_outcome"]["final_review_round_digest"] = contract_tool.digest_record(
            second, "review_round"
        )
        self.assertTrue(contract_tool.validate_record(evidence, "acceptance_evidence"))

        for field, replacement in (
            ("objective", "rewritten objective"),
            ("acceptance_criteria", [{"criterion_id": "criterion-1", "status": "met", "summary": "rewritten"}]),
        ):
            changed = self.two_round_evidence()
            changed_round = changed["review_rounds"][1]
            changed_round["task_spec"][field] = replacement
            changed["workflow_outcome"]["final_review_round_digest"] = contract_tool.digest_record(
                changed_round, "review_round"
            )
            self.assertTrue(contract_tool.validate_record(changed, "acceptance_evidence"), field)

        changed = self.two_round_evidence()
        changed_round = changed["review_rounds"][1]
        changed_round["task_spec"]["focused_checks"][0][
            "command_or_assertion"
        ] = "rewritten check"
        changed["workflow_outcome"]["final_review_round_digest"] = contract_tool.digest_record(
            changed_round, "review_round"
        )
        self.assertTrue(contract_tool.validate_record(changed, "acceptance_evidence"))

    def test_portable_capability_maps_are_mode_specific(self) -> None:
        portable = copy.deepcopy(self.golden_records()["capability_preflight"])
        self.assertEqual(contract_tool.validate_record(portable, "capability_preflight"), [])
        portable["capabilities"]["cas_state"] = False
        self.assertTrue(contract_tool.validate_record(portable, "capability_preflight"))
        del portable["capabilities"]["cas_state"]
        del portable["capabilities"]["shared_snapshot_access"]
        self.assertTrue(contract_tool.validate_record(portable, "capability_preflight"))

    def test_read_only_write_scope_and_required_check_coverage_fail_closed(self) -> None:
        task = copy.deepcopy(self.golden_records()["task_spec"])
        task["write_scope"] = ["src/main.py"]
        self.assertTrue(contract_tool.validate_record(task, "task_spec"))
        focused = copy.deepcopy(self.golden_records()["focused_check"])
        focused["covered_scope"] = []
        self.assertTrue(contract_tool.validate_record(focused, "focused_check"))

    def test_canonicalization_normalizes_and_sorts_set_like_paths(self) -> None:
        first = self.impact_scope()
        second = dict(first)
        second["changed_paths"] = ["README.md", "src/main.py"]
        self.assertEqual(contract_tool.digest_record(first, "impact_scope"), contract_tool.digest_record(second, "impact_scope"))
        canonical = contract_tool.canonicalize_record(first, "impact_scope")
        self.assertEqual(canonical["changed_paths"], ["README.md", "src/main.py"])

    def test_internal_mixins_expand_and_derive_runtime_metadata(self) -> None:
        raw = contract_tool.load_json_file(str(contract_tool.CONTRACT_PATH))
        effective = contract_tool._prepare_contract(raw)
        self.assertNotIn("mixins", effective["records"]["runtime_completion_event"])
        for kind in ("runtime_completion_event", "runtime_terminal_event", "runtime_stop_event"):
            fields = effective["records"][kind]["fields"]
            self.assertIn("event_id", fields)
            self.assertIn("binding_token", fields)
            self.assertIn("clock_source", fields)
        self.assertEqual(
            effective["runtime_events"]["record_kinds"],
            {
                "RUNTIME_COMPLETION_EVENT": "runtime_completion_event",
                "RUNTIME_TERMINAL_EVENT": "runtime_terminal_event",
                "RUNTIME_STOP_EVENT": "runtime_stop_event",
            },
        )
        self.assertEqual(
            tuple(effective["modes"]["required_capabilities"]["portable"])
            + tuple(effective["modes"]["required_capabilities"]["strict_additional"]),
            contract_tool.ALL_CAPABILITIES,
        )

    def test_internal_mixin_references_cycles_and_field_conflicts_are_rejected(self) -> None:
        raw = contract_tool.load_json_file(str(contract_tool.CONTRACT_PATH))

        unknown = copy.deepcopy(raw)
        unknown["mixins"]["broken"] = {"field_sets": ["missing"]}
        with self.assertRaises(contract_tool.ContractError):
            contract_tool._prepare_contract(unknown)

        cycle = copy.deepcopy(raw)
        cycle["mixins"]["first"] = {"mixins": ["second"]}
        cycle["mixins"]["second"] = {"mixins": ["first"]}
        with self.assertRaises(contract_tool.ContractError):
            contract_tool._prepare_contract(cycle)

        conflict = copy.deepcopy(raw)
        conflict["field_sets"]["same_field"] = {
            "required": ["event_id"],
            "fields": {"event_id": {"type": "identifier"}},
        }
        conflict["mixins"]["conflict"] = {
            "field_sets": ["runtime_event_identity", "same_field"]
        }
        with self.assertRaises(contract_tool.ContractError):
            contract_tool._prepare_contract(conflict)

        unknown_field_spec = copy.deepcopy(raw)
        unknown_field_spec["field_sets"]["runtime_event_identity"]["fields"]["event_id"]["unexpected"] = True
        with self.assertRaises(contract_tool.ContractError):
            contract_tool._prepare_contract(unknown_field_spec)

    def test_describe_returns_effective_record_and_section_json(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        record_result = subprocess.run(
            [sys.executable, str(repository / "scripts" / "contract_tool.py"), "describe", "--kind", "role_result"],
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(record_result.returncode, 0, record_result.stderr)
        self.assertEqual(json.loads(record_result.stdout), contract_tool.CONTRACT["records"]["role_result"])

        section_result = subprocess.run(
            [sys.executable, str(repository / "scripts" / "contract_tool.py"), "describe", "--section", "modes"],
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(section_result.returncode, 0, section_result.stderr)
        self.assertEqual(json.loads(section_result.stdout), contract_tool.CONTRACT["modes"])

        path_result = subprocess.run(
            [
                sys.executable,
                str(repository / "scripts" / "contract_tool.py"),
                "describe",
                "--path",
                "records.role_result.fields.status",
            ],
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(path_result.returncode, 0, path_result.stderr)
        self.assertEqual(
            json.loads(path_result.stdout),
            contract_tool.CONTRACT["records"]["role_result"]["fields"]["status"],
        )

        invalid_path_result = subprocess.run(
            [
                sys.executable,
                str(repository / "scripts" / "contract_tool.py"),
                "describe",
                "--path",
                "records.role_result.fields.missing",
            ],
            cwd=repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(invalid_path_result.returncode, 2)
        self.assertIn("unknown ContractV2 path", json.loads(invalid_path_result.stdout)["errors"][0])

    def test_runtime_references_keep_portable_and_strict_details_separate(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        runtime = (repository / "references" / "review-runtime.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(runtime.encode("utf-8")), 7500)
        for path in (
            "modes.required_capabilities.portable",
            "artifact_contracts.snapshot_manifest",
        ):
            self.assertIn(path, runtime)
        self.assertNotIn("modes.required_capabilities.strict_additional", runtime)
        strict_runtime = (repository / "references" / "review-runtime-strict.md").read_text(encoding="utf-8")
        for path in (
            "modes.required_capabilities.strict_additional",
            "records.runtime_completion_event",
            "records.runtime_terminal_event",
            "records.runtime_stop_event",
            "records.runtime_event_sequence",
        ):
            self.assertIn(path, strict_runtime)

    def test_unknown_fields_are_rejected(self) -> None:
        record = self.impact_scope()
        record["unexpected"] = True
        errors = contract_tool.validate_record(record, "impact_scope")
        self.assertTrue(any("unknown" in error for error in errors))

    def test_duplicate_and_traversal_paths_are_rejected(self) -> None:
        duplicate = self.impact_scope()
        duplicate["changed_paths"] = ["src/main.py", "src/./main.py"]
        self.assertTrue(contract_tool.validate_record(duplicate, "impact_scope"))

        traversal = self.impact_scope()
        traversal["changed_paths"] = ["../secret"]
        self.assertTrue(contract_tool.validate_record(traversal, "impact_scope"))

        absolute = self.impact_scope()
        absolute["changed_paths"] = ["/tmp/secret"]
        self.assertTrue(contract_tool.validate_record(absolute, "impact_scope"))

    def test_review_paths_and_explicit_exclusions_cannot_overlap_by_ancestry(self) -> None:
        for review_path, excluded_path in (
            ("src", "src/secret.txt"),
            ("src/secret.txt", "src"),
            ("src/secret.txt", "src/secret.txt"),
        ):
            with self.subTest(review_path=review_path, excluded_path=excluded_path):
                record = self.impact_scope()
                record["changed_paths"] = []
                record["review_paths"] = [review_path]
                record["explicit_exclusions"] = [excluded_path]
                errors = contract_tool.validate_record(record, "impact_scope")
                self.assertTrue(
                    any("review_paths overlap explicit_exclusions" in error for error in errors)
                )

    def test_duplicate_keys_and_non_finite_numbers_are_rejected(self) -> None:
        with self.assertRaises(contract_tool.ContractError):
            contract_tool.load_json_bytes(b'{"version":"impact-scope-v2","version":"again"}')
        with self.assertRaises(contract_tool.ContractError):
            contract_tool.load_json_bytes(b'{"value":NaN}')

    def test_stdin_loader_requests_only_one_byte_over_the_contract_limit(self) -> None:
        class BoundedInput:
            def __init__(self) -> None:
                self.buffer = self
                self.requested: int | None = None

            def read(self, size: int = -1) -> bytes:
                self.requested = size
                return b"{}"

        original = sys.stdin
        stream = BoundedInput()
        try:
            sys.stdin = stream  # type: ignore[assignment]
            self.assertEqual(contract_tool.load_json_file("-"), {})
        finally:
            sys.stdin = original
        self.assertEqual(stream.requested, contract_tool._input_limit() + 1)

    def test_common_exceptional_statuses_are_valid_for_each_role(self) -> None:
        for role, success in contract_tool.CONTRACT["statuses"]["role_success"].items():
            result = self.role_result(role, success)
            self.assertEqual(contract_tool.validate_record(result, "role_result"), [])
            result["status"] = "BLOCKED"
            result["blocker_or_input"] = "bounded blocker"
            self.assertEqual(contract_tool.validate_record(result, "role_result"), [])

    def test_capability_preflight_cannot_claim_unavailable_strict_mode(self) -> None:
        all_capabilities = {name: True for name in contract_tool.ALL_CAPABILITIES}
        strict = {
            "version": "capability-preflight-v2",
            "mode": "strict",
            "result": "STRICT_READY",
            "capabilities": all_capabilities,
            "missing": [],
            "authority": "authoritative_runtime_record",
        }
        self.assertTrue(contract_tool.validate_record(strict, "capability_preflight"))
        strict["result"] = "NOT_READY"
        strict["missing"] = []
        strict["authority"] = "observed_tool_surface"
        self.assertEqual(contract_tool.validate_record(strict, "capability_preflight"), [])

        strict["authority"] = "authoritative_runtime_record"
        strict["result"] = "NOT_READY"
        strict["capabilities"]["cas_state"] = False
        strict["missing"] = ["cas_state"]
        self.assertEqual(contract_tool.validate_record(strict, "capability_preflight"), [])
        strict["missing"] = ["cas_state", "cas_state"]
        self.assertTrue(contract_tool.validate_record(strict, "capability_preflight"))

    def test_runtime_events_have_terminal_identity_and_successful_completion_shapes(self) -> None:
        completion = {
            "version": "runtime-completion-event-v2",
            "event_type": "RUNTIME_COMPLETION_EVENT",
            "event_id": "event-1",
            "run_id": "run-1",
            "task_id": "task-1",
            "invocation_id": "invocation-1",
            "attempt": 1,
            "agent_id": "agent-1",
            "agent_channel": "channel-1",
            "transport_invocation_association": "association-1",
            "runtime_target": "target-1",
            "binding_token": "binding-1",
            "clock_source": "monotonic",
            "status": "completed",
            "result_delivery_confirmed": "yes",
            "terminal_at": "123.4",
            "terminal_reason": "result delivered",
        }
        self.assertEqual(contract_tool.validate_record(completion, "runtime_completion_event"), [])
        completion["clock_source"] = "wall-clock"
        self.assertTrue(contract_tool.validate_record(completion, "runtime_completion_event"))
        completion["clock_source"] = "monotonic"
        completion["terminal_at"] = "NaN"
        self.assertTrue(contract_tool.validate_record(completion, "runtime_completion_event"))
        completion["terminal_at"] = "123.4"
        completion["binding_token"] = "NONE"
        self.assertTrue(contract_tool.validate_record(completion, "runtime_completion_event"))

        terminal = {
            "version": "runtime-terminal-event-v2",
            "event_type": "RUNTIME_TERMINAL_EVENT",
            "event_id": "event-2",
            "run_id": "run-1",
            "task_id": "task-1",
            "invocation_id": "invocation-1",
            "attempt": 1,
            "agent_id": "agent-1",
            "agent_channel": "channel-1",
            "transport_invocation_association": "association-1",
            "runtime_target": "target-1",
            "binding_token": "binding-1",
            "clock_source": "monotonic",
            "status": "failed",
            "child_started": "yes",
            "stop_confirmed": "yes",
            "cancel_confirmed_at": None,
            "terminal_at": "123.4",
            "terminal_reason": "execution failed",
        }
        self.assertEqual(contract_tool.validate_record(terminal, "runtime_terminal_event"), [])
        cancelled = dict(terminal, status="cancelled")
        self.assertTrue(contract_tool.validate_record(cancelled, "runtime_terminal_event"))
        cancelled["cancel_confirmed_at"] = "123.3"
        self.assertEqual(contract_tool.validate_record(cancelled, "runtime_terminal_event"), [])
        terminal["stop_confirmed"] = "not_applicable"
        self.assertTrue(contract_tool.validate_record(terminal, "runtime_terminal_event"))

        spawn_failed = dict(terminal)
        spawn_failed.update(
            {
                "event_id": "event-3",
                "run_id": None,
                "task_id": None,
                "invocation_id": None,
                "agent_id": "UNASSIGNED",
                "agent_channel": "UNASSIGNED",
                "transport_invocation_association": "UNASSIGNED",
                "runtime_target": "NONE",
                "binding_token": "NONE",
                "status": "spawn_failed",
                "child_started": "no",
                "stop_confirmed": "not_applicable",
            }
        )
        self.assertEqual(contract_tool.validate_record(spawn_failed, "runtime_terminal_event"), [])

        stop = {
            "version": "runtime-stop-event-v2",
            "event_type": "RUNTIME_STOP_EVENT",
            "event_id": "event-4",
            "run_id": "run-1",
            "task_id": "task-1",
            "invocation_id": "invocation-1",
            "attempt": 1,
            "agent_id": "agent-1",
            "agent_channel": "channel-1",
            "transport_invocation_association": "association-1",
            "runtime_target": "target-1",
            "binding_token": "binding-1",
            "clock_source": "monotonic",
            "status": "stopped",
            "stop_confirmed": "yes",
            "stopped_at": "123.5",
            "stop_reason": "deadline",
        }
        self.assertEqual(contract_tool.validate_record(stop, "runtime_stop_event"), [])

        terminal["stop_confirmed"] = "yes"
        stop["stopped_at"] = "123.3"
        terminal["terminal_at"] = "123.5"
        sequence = {
            "version": "runtime-event-sequence-v2",
            "events": [stop, terminal],
        }
        self.assertEqual(contract_tool.validate_record(sequence, "runtime_event_sequence"), [])
        canonical = contract_tool.canonicalize_record(sequence, "runtime_event_sequence")
        self.assertEqual(
            [event["event_id"] for event in canonical["events"]],
            ["event-4", "event-2"],
        )
        sequence["events"] = [terminal, stop]
        self.assertTrue(contract_tool.validate_record(sequence, "runtime_event_sequence"))
        sequence["events"] = [stop, dict(terminal, event_id="event-4")]
        self.assertTrue(contract_tool.validate_record(sequence, "runtime_event_sequence"))
        sequence["events"] = [stop, dict(terminal, run_id="run-2")]
        self.assertTrue(contract_tool.validate_record(sequence, "runtime_event_sequence"))
        self.assertTrue(
            contract_tool.validate_record(
                {"version": "runtime-event-sequence-v2", "events": []},
                "runtime_event_sequence",
            )
        )
        malformed = dict(stop, event_type=[])
        self.assertTrue(
            contract_tool.validate_record(
                {"version": "runtime-event-sequence-v2", "events": [malformed]},
                "runtime_event_sequence",
            )
        )

    def test_role_specific_status_is_rejected(self) -> None:
        result = self.role_result("planner", "CLEAN")
        self.assertTrue(contract_tool.validate_record(result, "role_result"))

        unavailable = self.role_result("reviewer", "REVIEW_UNAVAILABLE")
        self.assertTrue(contract_tool.validate_record(unavailable, "role_result"))

    def test_findings_and_clean_have_required_review_payloads(self) -> None:
        findings = self.role_result("reviewer", "FINDINGS")
        self.assertTrue(contract_tool.validate_record(findings, "role_result"))
        finding = {
            "id": "finding-1",
            "severity": "HIGH",
            "path": "src/main.py",
            "line": 4,
            "summary": "unsafe behavior",
            "evidence": "focused evidence",
            "impact": "could regress behavior",
            "fix": "apply a bounded fix",
            "status": "OPEN",
        }
        findings["findings"] = [finding]
        findings.update(
            {
                "mode": "portable",
                "run_id": "run-1",
                "task_id": "task-1",
                "invocation_id": "invocation-1",
                "report_id": "report-1",
                "base_snapshot": "snapshot-1",
                "base_content_identity": "content-1",
                "snapshot_id": "a" * 64,
                "content_identity": "b" * 64,
                "artifact_access_proof": "artifact-proof-1",
                "review_coverage_proof": "coverage-proof-1",
                "reviewed_paths": ["src/main.py"],
            }
        )
        self.assertEqual(contract_tool.validate_record(findings, "role_result"), [])
        findings["checks"] = [{"id": "focused", "status": "FAILED", "summary": "check failed"}]
        self.assertEqual(contract_tool.validate_record(findings, "role_result"), [])

        clean = self.role_result("reviewer", "CLEAN")
        clean.update(
            {
                "mode": "portable",
                "run_id": "run-1",
                "task_id": "task-1",
                "invocation_id": "invocation-1",
                "report_id": "report-1",
                "base_snapshot": "snapshot-1",
                "base_content_identity": "content-1",
                "snapshot_id": "a" * 64,
                "content_identity": "b" * 64,
                "artifact_access_proof": "artifact-proof-1",
                "review_coverage_proof": "coverage-proof-1",
            }
        )
        clean["reviewed_paths"] = ["src/main.py"]
        clean["risks"] = []
        clean["findings"] = [dict(finding, status="OPEN")]
        self.assertTrue(contract_tool.validate_record(clean, "role_result"))
        clean["findings"] = [dict(finding, status="ADDRESSED", path="src/./main.py")]
        self.assertEqual(contract_tool.validate_record(clean, "role_result"), [])

        clean["role_payload"] = {"mode": "portable", "risks": ["bounded risk"]}
        self.assertEqual(contract_tool.validate_record(clean, "role_result"), [])
        canonical = contract_tool.canonicalize_record(clean, "role_result")
        self.assertEqual(canonical["findings"][0]["path"], "src/main.py")

    def test_workflow_outcome_requires_consistent_acceptance(self) -> None:
        accepted = copy.deepcopy(self.golden_records()["workflow_outcome"])
        self.assertEqual(contract_tool.validate_record(accepted, "workflow_outcome"), [])
        accepted["snapshot_id"] = None
        self.assertTrue(contract_tool.validate_record(accepted, "workflow_outcome"))
        accepted["snapshot_id"] = "a" * 64
        accepted["snapshot_id"] = "NONE"
        accepted["content_identity"] = "UNKNOWN"
        self.assertTrue(contract_tool.validate_record(accepted, "workflow_outcome"))
        accepted["snapshot_id"] = "a" * 64
        accepted["content_identity"] = "b" * 64
        accepted["accepted"] = False
        self.assertTrue(contract_tool.validate_record(accepted, "workflow_outcome"))

        unavailable = copy.deepcopy(self.golden_records()["workflow_outcome"])
        unavailable.update(
            {
                "outcome": "NOT_ACCEPTED",
                "accepted": False,
                "review_status": "REVIEW_UNAVAILABLE",
                "reason": "REVIEW_UNAVAILABLE",
                "validation_status": "NOT_RUN",
                "capability_preflight_digest": None,
                "final_review_round_digest": None,
                "validation_checks": [],
                "full_validation_check_id": None,
                "snapshot_id": None,
                "content_identity": None,
            }
        )
        self.assertEqual(contract_tool.validate_record(unavailable, "workflow_outcome"), [])
        unavailable["commit_requested"] = True
        self.assertTrue(contract_tool.validate_record(unavailable, "workflow_outcome"))

    def test_workflow_reason_and_commit_metadata_are_cross_checked(self) -> None:
        accepted = copy.deepcopy(self.golden_records()["workflow_outcome"])
        accepted["commit_id"] = "commit-1"
        self.assertTrue(contract_tool.validate_record(accepted, "workflow_outcome"))
        accepted["commit_requested"] = True
        accepted["commit_status"] = "CREATED"
        self.assertEqual(contract_tool.validate_record(accepted, "workflow_outcome"), [])

        strict_reason = dict(accepted)
        strict_reason.update(
            {
                "mode": "portable",
                "outcome": "NOT_ACCEPTED",
                "accepted": False,
                "review_status": "REVIEW_BLOCKED",
                "reason": "STRICT_CAPABILITY_MISSING",
                "commit_requested": False,
                "commit_status": "NOT_REQUESTED",
                "commit_blocker": None,
                "commit_id": None,
                "capability_preflight_digest": None,
                "final_review_round_digest": None,
                "validation_checks": [],
                "full_validation_check_id": None,
                "snapshot_id": None,
                "content_identity": None,
            }
        )
        self.assertTrue(contract_tool.validate_record(strict_reason, "workflow_outcome"))
        strict_reason["mode"] = "strict"
        self.assertEqual(contract_tool.validate_record(strict_reason, "workflow_outcome"), [])

        scope_changed = dict(strict_reason)
        scope_changed["reason"] = "SCOPE_CHANGED"
        scope_changed["review_status"] = "CLEAN"
        self.assertTrue(contract_tool.validate_record(scope_changed, "workflow_outcome"))

        not_accepted_commit = dict(strict_reason)
        not_accepted_commit["commit_id"] = "commit-2"
        self.assertTrue(contract_tool.validate_record(not_accepted_commit, "workflow_outcome"))

    def test_malformed_unicode_is_invalid_input_not_a_traceback(self) -> None:
        result = self.role_result()
        result["summary"] = "\ud800"
        self.assertTrue(contract_tool.validate_record(result, "role_result"))

    def test_contract_cli_escapes_malformed_unicode_diagnostics(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "invalid.json"
            input_path.write_bytes(b'{"\\ud800": true}')
            result = subprocess.run(
                [
                    sys.executable,
                    str(repository / "scripts" / "contract_tool.py"),
                    "validate",
                    "--kind",
                    "impact_scope",
                    str(input_path),
                ],
                cwd=repository,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(json.loads(result.stdout)["valid"])

    def test_file_inputs_reject_symlinks_and_deep_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory / "target.json"
            target.write_text(json.dumps(self.impact_scope()), encoding="utf-8")
            linked = directory / "linked.json"
            linked.symlink_to(target)
            with self.assertRaises(contract_tool.ContractError):
                contract_tool.load_json_file(str(linked))

            deep = directory / "deep.json"
            deep.write_text("[" * 70 + "0" + "]" * 70, encoding="ascii")
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "contract_tool.py"),
                    "validate",
                    "--kind",
                    "impact_scope",
                    str(deep),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(json.loads(result.stdout)["valid"])

    def test_context_handoff_distinguishes_independent_and_continuation(self) -> None:
        base = {
            "branch": "main",
            "head": "abc123",
            "status_digest": "status123",
        }
        common = {
            "version": "context-handoff-v2",
            "handoff_id": "handoff-1",
            "task_id": "task-1",
            "run_id": "run-1",
            "parent_task_id": None,
            "source_task_id": None,
            "source_run_id": None,
            "mode": "independent",
            "original_request": "start a separate bounded task",
            "accepted_plan": [{"step_id": "step-1", "status": "done", "summary": "scope"}],
            "baseline": base,
            "scope": self.impact_scope(),
            "acceptance_criteria": [{"criterion_id": "criterion-1", "status": "pending", "summary": "review"}],
            "focused_checks": [],
            "checkpoint": None,
            "artifacts": [],
            "active_work": [],
            "open_risks": [],
            "decisions": [],
        }
        self.assertEqual(contract_tool.validate_record(common, "context_handoff"), [])
        common["mode"] = "continuation"
        self.assertTrue(contract_tool.validate_record(common, "context_handoff"))
        common["checkpoint"] = {
            "checkpoint_id": "checkpoint-1",
            "status": "CHECKPOINT_READY",
            "completed_milestone": "bounded work",
            "changed_paths": ["src/main.py"],
            "content_identity": "content-1",
            "next_action": "review",
        }
        common["source_task_id"] = "task-1"
        common["source_run_id"] = "run-1"
        self.assertTrue(contract_tool.validate_record(common, "context_handoff"))
        common["artifacts"] = [
            {
                "kind": "checkpoint",
                "artifact_id": "artifact-1",
                "artifact_path": "/tmp/checkpoint-artifact",
                "content_identity": "content-1",
            }
        ]
        self.assertEqual(contract_tool.validate_record(common, "context_handoff"), [])
        common["artifacts"][0]["content_identity"] = "content-2"
        self.assertTrue(contract_tool.validate_record(common, "context_handoff"))

    def test_task_spec_is_closed_and_binding_mode_matches(self) -> None:
        task = copy.deepcopy(self.golden_records()["task_spec"])
        self.assertEqual(contract_tool.validate_record(task, "task_spec"), [])
        task["binding_mode"] = "runtime_atomic"
        self.assertTrue(contract_tool.validate_record(task, "task_spec"))

    def test_templates_use_v2_roles_and_do_not_redeclare_statuses(self) -> None:
        template = (Path(__file__).resolve().parents[1] / "references" / "agent-templates.md").read_text(
            encoding="utf-8"
        )
        for role in contract_tool.CONTRACT["statuses"]["roles"]:
            self.assertIn(f"role: {role}", template)
        self.assertIn("role-result-v2", template)
        self.assertIn("contracts-v2.json", template)
        self.assertNotIn("role-result-v1", template)
        self.assertNotIn("task-spec-v1", template)


if __name__ == "__main__":
    unittest.main()
