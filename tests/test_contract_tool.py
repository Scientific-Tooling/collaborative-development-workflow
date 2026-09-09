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
            "invocation_id": None,
            "binding_token": None,
            "binding_mode": "portable",
            "role": "reviewer",
            "mode": "portable",
            "objective": "review",
            "depends_on": [],
            "acceptance_criteria": [{"criterion_id": "criterion-1", "status": "pending", "summary": "review"}],
            "read_scope": ["src/main.py"],
            "write_scope": [],
            "impact_scope": scope,
            "base_snapshot": "snapshot-1",
            "base_content_identity": "content-1",
            "focused_checks": [],
            "execution": "read-only",
            "isolation": "frozen-artifact",
            "budget": "bounded",
            "resumable": False,
            "model_profile": {"model": "unknown"},
            "full_suite_owner": "main",
            "snapshot_id": "snapshot-1",
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
            "capabilities": {name: True for name in contract_tool.ALL_CAPABILITIES},
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
            "commit_requested": False,
            "snapshot_id": "snapshot-1",
            "content_identity": "content-1",
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

    def test_existing_record_digests_are_golden(self) -> None:
        expected = {
            "impact_scope": "95cdeeb0517a03d348565a8fda2c99ca3e7c169728c040786b2ac909f3e5b9e9",
            "focused_check": "2bb24f579713cd777c7a596e6e1bd30ca33e9f9ef15eaaa19eee02b84c4df85a",
            "capability_preflight": "0168c0d6c66b4bff50aa1b977afa4e7e55c342be69d41e0a93670939c555cdec",
            "role_result": "b37d64d311f784ca7cbea4681d92becb334cf1da0b3a7bcd561b10a2c12fb18b",
            "workflow_outcome": "12b1cddb79a160f4d167303a09e30bdfc4ea8fbdf8e25f63ae04c07e29ab421c",
            "task_spec": "c7a3da4dae9a974eac20383793f737ec6da8b261a391ef4d931b767f06599bb4",
            "runtime_completion_event": "35bf53446008f84a8ce8ba9cdadc849f16602dfa0071f300e879bd15f42d46b3",
            "runtime_terminal_event": "e2534b584bc4f0c2a34e14b49fc427a759fe7a3cc2f00319722ab09adb462d71",
            "runtime_stop_event": "4b7e7c6db8206befdb31bad2b79457ec0accf210b656f15e1cad6311bf14da3b",
            "runtime_event_sequence": "81db69007e02192f2368cb6d380b89373d486cdd2049b164814ec443313f7282",
            "context_handoff": "146be8b4c7f0abe273fb61e0f0cb2e67ecf79af4c6c299c942b45f1765d8b254",
        }
        records = self.golden_records()
        self.assertEqual(set(records), set(expected))
        for kind, record in records.items():
            self.assertEqual(contract_tool.digest_record(record, kind), expected[kind], kind)

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

    def test_runtime_reference_points_to_contract_without_schema_duplication(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        runtime = (repository / "references" / "review-runtime.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(runtime.encode("utf-8")), 7500)
        for path in (
            "modes.required_capabilities.portable",
            "modes.required_capabilities.strict_additional",
            "records.runtime_completion_event",
            "records.runtime_terminal_event",
            "records.runtime_stop_event",
            "artifact_contracts.snapshot_manifest",
        ):
            self.assertIn(path, runtime)
        self.assertNotIn("EVENT_ID, RUN_ID, TASK_ID", runtime)
        self.assertNotIn("STATUS: failed | cancelled | errored", runtime)

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
        self.assertEqual(contract_tool.validate_record(unavailable, "role_result"), [])

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
                "base_snapshot": "snapshot-1",
                "base_content_identity": "content-1",
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
                "base_snapshot": "snapshot-1",
                "base_content_identity": "content-1",
                "artifact_access_proof": "artifact-proof-1",
                "review_coverage_proof": "coverage-proof-1",
            }
        )
        clean["reviewed_paths"] = ["src/main.py"]
        clean["findings"] = [dict(finding, status="OPEN")]
        self.assertTrue(contract_tool.validate_record(clean, "role_result"))
        clean["findings"] = [dict(finding, status="ADDRESSED", path="src/./main.py")]
        self.assertEqual(contract_tool.validate_record(clean, "role_result"), [])

        clean["role_payload"] = {"mode": "portable", "risks": ["bounded risk"]}
        self.assertEqual(contract_tool.validate_record(clean, "role_result"), [])
        canonical = contract_tool.canonicalize_record(clean, "role_result")
        self.assertEqual(canonical["findings"][0]["path"], "src/main.py")

    def test_workflow_outcome_requires_consistent_acceptance(self) -> None:
        accepted = {
            "version": "workflow-outcome-v2",
            "mode": "portable",
            "outcome": "ACCEPTED_PORTABLE",
            "accepted": True,
            "review_status": "CLEAN",
            "reason": None,
            "validation_status": "PASSED",
            "commit_requested": False,
            "snapshot_id": "snapshot-1",
            "content_identity": "content-1",
            "commit_id": None,
        }
        self.assertEqual(contract_tool.validate_record(accepted, "workflow_outcome"), [])
        accepted["snapshot_id"] = None
        self.assertTrue(contract_tool.validate_record(accepted, "workflow_outcome"))
        accepted["snapshot_id"] = "snapshot-1"
        accepted["snapshot_id"] = "NONE"
        accepted["content_identity"] = "UNKNOWN"
        self.assertTrue(contract_tool.validate_record(accepted, "workflow_outcome"))
        accepted["snapshot_id"] = "snapshot-1"
        accepted["content_identity"] = "content-1"
        accepted["accepted"] = False
        self.assertTrue(contract_tool.validate_record(accepted, "workflow_outcome"))

        unavailable = dict(accepted)
        unavailable.update(
            {
                "outcome": "NOT_ACCEPTED",
                "review_status": "CLEAN",
                "reason": "REVIEW_UNAVAILABLE",
            }
        )
        self.assertTrue(contract_tool.validate_record(unavailable, "workflow_outcome"))
        unavailable["commit_requested"] = True
        self.assertTrue(contract_tool.validate_record(unavailable, "workflow_outcome"))

    def test_workflow_reason_and_commit_metadata_are_cross_checked(self) -> None:
        accepted = {
            "version": "workflow-outcome-v2",
            "mode": "portable",
            "outcome": "ACCEPTED_PORTABLE",
            "accepted": True,
            "review_status": "CLEAN",
            "reason": None,
            "validation_status": "PASSED",
            "commit_requested": False,
            "snapshot_id": "snapshot-1",
            "content_identity": "content-1",
            "commit_id": "commit-1",
        }
        self.assertTrue(contract_tool.validate_record(accepted, "workflow_outcome"))
        accepted["commit_requested"] = True
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
                "commit_id": None,
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
        self.assertEqual(contract_tool.validate_record(common, "context_handoff"), [])

    def test_task_spec_is_closed_and_binding_mode_matches(self) -> None:
        task = {
            "version": "task-spec-v2",
            "task_id": "task-1",
            "parent_task_id": None,
            "run_id": "run-1",
            "invocation_id": None,
            "binding_token": None,
            "binding_mode": "portable",
            "role": "reviewer",
            "mode": "portable",
            "objective": "review the bounded artifact",
            "depends_on": [],
            "acceptance_criteria": [
                {"criterion_id": "criterion-1", "status": "pending", "summary": "review"}
            ],
            "read_scope": ["src/main.py"],
            "write_scope": [],
            "impact_scope": self.impact_scope(),
            "base_snapshot": "snapshot-1",
            "base_content_identity": "content-1",
            "focused_checks": [],
            "execution": "read-only",
            "isolation": "frozen-artifact",
            "budget": "bounded",
            "resumable": False,
            "model_profile": {"model": "unknown"},
            "full_suite_owner": "main",
            "snapshot_id": "snapshot-1",
            "artifact_path": "/tmp/artifact",
            "artifact_access_proof": "proof-1",
            "review_coverage_proof": "coverage-1",
        }
        self.assertEqual(contract_tool.validate_record(task, "task_spec"), [])
        task["binding_mode"] = "strict"
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
