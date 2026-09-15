#!/usr/bin/env python3
"""Create workflow records, render delegated prompts, and check acceptance."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

_previous_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    import snapshot_tool
    from contract_tool import (
        CONTRACT,
        PORTABLE_CAPABILITIES,
        SENTINELS,
        ContractError,
        canonicalize_record,
        digest_record,
        load_json_file,
        normalize_repo_path,
        _validate_resolved_model_selection,
        validate_record,
    )
finally:
    sys.dont_write_bytecode = _previous_dont_write_bytecode
    del _previous_dont_write_bytecode


class WorkflowToolError(ValueError):
    """An invalid workflow-tool request."""


LIVE_CONFIRMATIONS = (
    "acceptance_criteria_satisfaction",
    "complete_diff_and_status",
    "model_selection_resolution",
    "preflight_freshness",
    "reviewer_artifact_access",
    "reviewer_fresh_context",
    "reviewer_invocation_and_result_delivery",
    "reviewer_read_only_enforcement",
    "reviewer_scope_coverage",
    "validation_execution",
    "writers_paused_and_post_freeze_immutability",
)


ROLE_INSTRUCTIONS = {
    "planner": (
        "Inspect only the declared read and impact scope. Produce the smallest "
        "dependency-aware plan or critique. Do not edit or run repository-wide "
        "checks. Treat suggested paths as evidence for the parent, not authority "
        "to expand scope."
    ),
    "researcher": (
        "Answer only the focused research question from the declared sources and "
        "paths. Do not edit, decide for the user, or run heavyweight validation. "
        "Label any suggested scope changes for the parent to decide."
    ),
    "implementer": (
        "Implement only the accepted milestone within write_scope. Portable mode "
        "does not permit a delegated writer. In strict mode, edit only after the "
        "runtime supplied the required atomic binding. Run focused checks only and "
        "stop at a coherent checkpoint."
    ),
    "verifier": (
        "Run only the assigned focused checks for the changed paths and immediate "
        "callers. Do not edit, broaden scope, or declare final acceptance."
    ),
    "reviewer": (
        "Review only the exact immutable artifact named by artifact_path, "
        "snapshot_id, and content_identity. Reconstruct the scoped diff from its "
        "baseline/ and files/ entries; inspect every review_path and named direct "
        "caller or consumer. Check correctness, regressions, edge cases, security "
        "and privacy, and relevant performance, accessibility, and test coverage. "
        "Do not inspect the moving workspace or edit anything. CLEAN requires "
        "complete reviewed_paths, passing checks, and no open finding or risk. "
        "FINDINGS requires bounded actionable findings with locations and evidence. "
        "A reviewer cannot claim acceptance or authorize a commit."
    ),
}


ROLE_PAYLOAD_TEMPLATES: dict[str, dict[str, Any]] = {
    "planner": {
        "planning_role": "<planner or critic>",
        "plan_or_critique": "<bounded text>",
        "milestones": [],
        "prerequisites": [],
        "write_scope": [],
        "integration_order": [],
        "mode": "<portable or strict>",
        "exclusions": [],
        "focused_checks": [],
        "risks": [],
        "material_decisions": [],
    },
    "researcher": {
        "research_scope": "<bounded text>",
        "evidence": [],
        "open_questions": [],
        "recommendation": "<bounded text>",
    },
    "implementer": {
        "milestone": "<bounded text>",
        "remaining_risks": [],
        "next_milestone": "<bounded text>",
    },
    "verifier": {
        "verification_results": [],
        "reproducible_failures": [],
    },
    "reviewer": {},
}


RUNTIME_PROVENANCE_FIELDS = (
    "attempt",
    "agent_id",
    "agent_channel",
    "transport_invocation_association",
    "runtime_completion_event_id",
    "runtime_terminal_event_id",
    "content_identity",
)


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep command-line failures machine-readable."""

    def error(self, message: str) -> None:
        raise WorkflowToolError(message)


def _emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _failure(
    command: str,
    stage: str,
    *,
    error: str | None = None,
    errors: Iterable[str] = (),
    **details: Any,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "command": command,
        "ok": False,
        "stage": stage,
    }
    bounded_errors = sorted(set(errors))
    if error is not None:
        bounded_errors = sorted(set((*bounded_errors, error)))
    report["errors"] = bounded_errors
    report.update(details)
    return report


def _pretty_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _canonical_paths(values: Iterable[str]) -> list[str]:
    return sorted({normalize_repo_path(value) for value in values})


def _starter_records(
    git_identity: dict[str, str],
    changed_paths: Iterable[str],
    review_paths: Iterable[str],
) -> list[tuple[str, str, dict[str, Any]]]:
    changed = _canonical_paths(changed_paths)
    review = sorted(set(_canonical_paths(review_paths)) | set(changed))
    if not changed:
        raise WorkflowToolError("at least one changed path is required")
    if not review:
        raise WorkflowToolError("at least one review path is required")

    scope = {
        "version": "impact-scope-v2",
        "changed_paths": changed,
        "review_paths": review,
        "direct_callers": [],
        "direct_consumers": [],
        "mapped_tests_or_configuration": [],
        "explicit_exclusions": [],
    }
    scope = canonicalize_record(scope, "impact_scope")
    task_id = f"task-{secrets.token_hex(16)}"
    run_id = f"run-{secrets.token_hex(16)}"

    checks: list[dict[str, Any]] = []
    for index in range(0, len(review), 64):
        number = index // 64 + 1
        check_id = "focused" if len(review) <= 64 else f"focused-{number}"
        check = {
            "version": "focused-check-v2",
            "id": check_id,
            "command_or_assertion": "Replace with the repository's focused validation command.",
            "covered_scope": review[index : index + 64],
            "required": True,
        }
        checks.append(canonicalize_record(check, "focused_check"))

    model_request = canonicalize_record(
        {
            "version": "model-request-v2",
            "strategy": "inherit",
            "requested_model": None,
            "requested_effort": None,
            "fallback": "fail",
            "reviewer_independence": "same_allowed",
            "comparison_model": None,
        },
        "model_request",
    )
    preflight = canonicalize_record(
        {
            "version": "capability-preflight-v2",
            "run_id": run_id,
            "mode": "portable",
            "result": "NOT_READY",
            "capabilities": {name: False for name in PORTABLE_CAPABILITIES},
            "missing": sorted(PORTABLE_CAPABILITIES),
            "authority": "observed_tool_surface",
            "read_only_enforcement": "unverified",
            "artifact_only_read_enforcement": "unknown",
        },
        "capability_preflight",
    )

    task = canonicalize_record(
        {
            "version": "task-spec-v2",
            "task_id": task_id,
            "parent_task_id": None,
            "run_id": run_id,
            "invocation_id": None,
            "binding_token": None,
            "binding_mode": "transport_bound_provisional",
            "role": "planner",
            "mode": "portable",
            "objective": "Plan the requested change within the declared impact scope.",
            "depends_on": [],
            "acceptance_criteria": [
                {
                    "criterion_id": "requested-change",
                    "status": "pending",
                    "summary": "Replace with an observable result for the requested change.",
                }
            ],
            "read_scope": review,
            "write_scope": [],
            "impact_scope": scope,
            "base_snapshot": git_identity["head"],
            "base_content_identity": git_identity["index_tree"],
            "focused_checks": checks,
            "execution": "read-only",
            "isolation": "fresh-context",
            "budget": {
                "version": "execution-budget-v2",
                "wall_clock_seconds": 600,
                "max_turns": 8,
                "max_output_bytes": 65536,
            },
            "resumable": False,
            "model_request": model_request,
            "model_profile": {"model": "unknown"},
            "full_suite_owner": "main",
            "snapshot_id": None,
            "content_identity": None,
            "artifact_path": None,
            "artifact_access_proof": None,
            "review_coverage_proof": None,
        },
        "task_spec",
    )

    records: list[tuple[str, str, dict[str, Any]]] = [
        ("impact_scope.json", "impact_scope", scope),
        ("model_request.json", "model_request", model_request),
        ("capability_preflight.json", "capability_preflight", preflight),
        ("task_spec.json", "task_spec", task),
    ]
    for index, check in enumerate(checks, start=1):
        filename = "focused_check.json" if len(checks) == 1 else f"focused_check-{index:02d}.json"
        records.append((filename, "focused_check", check))
    return records


def _publish_records(
    output: Path, records: list[tuple[str, str, dict[str, Any]]]
) -> None:
    output = snapshot_tool._canonical_absolute_path(output)
    parent_descriptor, output_name = snapshot_tool._open_absolute_parent(output)
    stage_name = f".cdw-init-stage-{secrets.token_hex(16)}"
    stage_descriptor: int | None = None
    stage_stat: os.stat_result | None = None
    published = False
    try:
        try:
            os.stat(output_name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise WorkflowToolError("output directory already exists")

        os.mkdir(stage_name, 0o700, dir_fd=parent_descriptor)
        stage_descriptor = os.open(
            stage_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
        stage_stat = os.fstat(stage_descriptor)
        for filename, _, record in records:
            descriptor = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=stage_descriptor,
            )
            try:
                data = _pretty_json(record)
                offset = 0
                while offset < len(data):
                    written = os.write(descriptor, data[offset:])
                    if written <= 0:
                        raise WorkflowToolError(f"cannot write starter record: {filename}")
                    offset += written
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        os.fsync(stage_descriptor)
        snapshot_tool._rename_noreplace(parent_descriptor, stage_name, output_name)
        published = True
        final_stat = os.stat(
            output_name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if not snapshot_tool._same_inode(final_stat, stage_stat):
            raise WorkflowToolError(
                "published output is not bound to the staged directory"
            )
        snapshot_tool._assert_directory_path_binding(
            output, stage_descriptor, "published starter bundle"
        )
        os.fsync(parent_descriptor)
    except Exception:
        if stage_stat is not None:
            cleanup_name = output_name if published else stage_name
            snapshot_tool._remove_owned_directory(
                parent_descriptor, cleanup_name, stage_stat
            )
        raise
    finally:
        if stage_descriptor is not None:
            os.close(stage_descriptor)
        os.close(parent_descriptor)


def initialize(
    output_arg: str,
    root_arg: str,
    changed_paths: Iterable[str],
    review_paths: Iterable[str],
) -> tuple[int, dict[str, Any]]:
    try:
        if not Path(root_arg).is_absolute():
            raise WorkflowToolError("repository root must be an absolute path")
        if not Path(output_arg).is_absolute():
            raise WorkflowToolError("output directory must be an absolute path")
        root = snapshot_tool._canonical_absolute_path(root_arg)
        git_identity = snapshot_tool.git_identity(root)
        output = snapshot_tool._canonical_absolute_path(output_arg)
        if snapshot_tool._path_is_at_or_below(root, output):
            raise WorkflowToolError(
                "output directory must be outside the repository root"
            )
        records = _starter_records(git_identity, changed_paths, review_paths)
        task = {kind: record for _, kind, record in records}["task_spec"]
        _publish_records(output, records)
        files = [
            {
                "digest": digest_record(record, kind),
                "kind": kind,
                "path": str(output / filename),
            }
            for filename, kind, record in records
        ]
        return 0, {
            "command": "init",
            "files": sorted(files, key=lambda item: item["path"]),
            "ok": True,
            "output": str(output),
            "preflight_result": "NOT_READY",
            "root": str(root),
            "run_id": task["run_id"],
            "task_id": task["task_id"],
        }
    except (
        ContractError,
        WorkflowToolError,
        snapshot_tool.SnapshotError,
        OSError,
        UnicodeError,
        TypeError,
        KeyError,
        ValueError,
        RecursionError,
    ) as exc:
        return 2, _failure("init", "record_generation", error=str(exc))


def _permitted_statuses(role: str) -> list[str]:
    statuses = CONTRACT["statuses"]
    if role == "reviewer":
        successful = statuses["review"]
    else:
        successful = [statuses["role_success"][role]]
    return list(dict.fromkeys([*successful, *statuses["common_exceptional"]]))


def _copy_task_correlation(template: dict[str, Any], task: dict[str, Any]) -> None:
    fields = [
        "task_id",
        "run_id",
        "invocation_id",
        "mode",
        "base_snapshot",
        "base_content_identity",
    ]
    if task["role"] == "reviewer":
        fields.extend(
            (
                "snapshot_id",
                "content_identity",
                "artifact_access_proof",
                "review_coverage_proof",
            )
        )
    for field in fields:
        if task[field] is not None:
            template[field] = task[field]


def _result_template(task: dict[str, Any]) -> dict[str, Any]:
    role = task["role"]
    payload = {
        key: list(value) if isinstance(value, list) else value
        for key, value in ROLE_PAYLOAD_TEMPLATES[role].items()
    }
    if "mode" in payload:
        payload["mode"] = task["mode"]
    template: dict[str, Any] = {
        "version": CONTRACT["records"]["role_result"]["fields"]["version"]["value"],
        "role": role,
        "status": "<one permitted status>",
        "summary": "<bounded text>",
        "completed_scope": [],
        "changed_paths": [],
        "checks": [],
        "risks": [],
        "blocker_or_input": None,
        "attention_required": [],
        "next_action": None,
        "model_profile": {
            "model": "unknown",
            "effort": "unknown",
            "selection_outcome": "unknown",
        },
        "report_id": "<fresh identifier>",
    }
    _copy_task_correlation(template, task)
    if payload:
        template["role_payload"] = payload
    if role == "reviewer":
        template["reviewed_paths"] = []
        template["findings"] = []
        template["role_payload"] = {"mode": task["mode"]}
    return template


def _choice_placeholder(values: Iterable[str]) -> str:
    return "<one of: {}>".format(", ".join(values))


def _result_item_shapes(role: str) -> dict[str, Any]:
    statuses = CONTRACT["statuses"]
    shapes: dict[str, Any] = {
        "check": {
            "id": "<token>",
            "status": _choice_placeholder(statuses["check"]),
            "summary": "<bounded text>",
        },
        "risk": {
            "id": "<token>",
            "status": _choice_placeholder(statuses["risk_statuses"]),
            "summary": "<bounded text>",
        },
    }
    if role == "reviewer":
        shapes["finding"] = {
            "id": "<token>",
            "severity": _choice_placeholder(statuses["finding_severities"]),
            "path": "<reviewed repository path>",
            "line": 1,
            "summary": "<bounded text>",
            "evidence": "<bounded text>",
            "impact": "<bounded text>",
            "fix": "<bounded text>",
            "status": _choice_placeholder(statuses["review_findings"]),
        }
    return shapes


def _known_profile_value(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return None if value.upper() in SENTINELS else value


def _minimum_success_model_profile(task: dict[str, Any]) -> dict[str, str]:
    request = task["model_request"]
    dispatch = task["model_profile"]
    strategy = request["strategy"]
    if strategy == "explicit":
        model = request["requested_model"]
        effort = request["requested_effort"] or "x"
    elif strategy == "inherit":
        model = _known_profile_value(dispatch.get("model")) or "x"
        effort = _known_profile_value(dispatch.get("effort")) or "x"
    else:
        model = "x"
        effort = "x"
    comparison = request["comparison_model"]
    if request["reviewer_independence"] == "different_required" and model == comparison:
        model = "y" if comparison != "y" else "x"
    candidates = [
        {"model": model, "effort": effort, "selection_outcome": "honored"}
    ]
    if request["fallback"] == "allow_runtime_default":
        candidates.extend(
            (
                {"model": "x", "effort": "x", "selection_outcome": "fallback"},
                {"model": "x", "effort": "x", "selection_outcome": "unknown"},
            )
        )
    valid: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            _validate_resolved_model_selection(request, dispatch, candidate)
        except ContractError:
            continue
        valid.append(candidate)
    if not valid:
        raise WorkflowToolError("TaskSpec has no valid successful model resolution")
    def encoded(candidate: dict[str, str]) -> bytes:
        return json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    return min(valid, key=lambda candidate: (len(encoded(candidate)), encoded(candidate)))


def _minimum_success_result(task: dict[str, Any]) -> dict[str, Any]:
    role = task["role"]
    status = "CLEAN" if role == "reviewer" else CONTRACT["statuses"]["role_success"][role]
    result: dict[str, Any] = {
        "version": "role-result-v2",
        "role": role,
        "status": status,
        "summary": "x",
        "completed_scope": [task["task_id"]],
        "changed_paths": [],
        "checks": [],
        "risks": [],
        "blocker_or_input": None,
        "attention_required": [],
        "next_action": None,
        "model_profile": _minimum_success_model_profile(task),
        "report_id": "r",
    }
    _copy_task_correlation(result, task)
    if role == "planner":
        result["role_payload"] = {"plan_or_critique": "x"}
    elif role == "researcher":
        result["role_payload"] = {"recommendation": "x"}
    elif role == "implementer":
        result["changed_paths"] = task["impact_scope"]["changed_paths"]
        result["role_payload"] = {"milestone": "x"}
    elif role == "verifier":
        result["role_payload"] = {"verification_results": ["x"]}
    else:
        review_paths = task["impact_scope"]["review_paths"]
        result["completed_scope"] = review_paths
        result["reviewed_paths"] = review_paths
        required_ids = [
            check["id"] for check in task["focused_checks"] if check["required"]
        ]
        if not required_ids:
            required_ids = ["review"]
        result["checks"] = [
            {"id": check_id, "status": "PASSED", "summary": "x"}
            for check_id in required_ids
        ]
        result["findings"] = []
        result["role_payload"] = {"mode": task["mode"]}
    return canonicalize_record(result, "role_result")


def _minimum_success_result_bytes(task: dict[str, Any]) -> int:
    return len(
        json.dumps(
            _minimum_success_result(task),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _render_prompt_text(task: dict[str, Any]) -> str:
    role = task["role"]
    status_text = ", ".join(_permitted_statuses(role))
    result_template = json.dumps(
        _result_template(task),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    item_shapes = json.dumps(
        _result_item_shapes(role),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    read_only_result = (
        "This is a read-only role, so changed_paths must be []."
        if role != "implementer"
        else "Report only paths actually changed within write_scope."
    )
    role_result_rules = (
        "reviewed_paths and findings are reviewer-only arrays. CLEAN and FINDINGS "
        "both require at least one check; copy impact_scope.review_paths exactly "
        "into reviewed_paths. CLEAN permits only PASSED checks and no OPEN finding "
        "or risk. FINDINGS requires at least one OPEN actionable finding."
        if role == "reviewer"
        else (
            "Omit reviewed_paths, findings, snapshot_id, artifact_access_proof, and "
            "review_coverage_proof; those fields are reviewer-only."
        )
    )
    task_json = json.dumps(
        task,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "\n".join(
        (
            f"You are the {role} for task {task['task_id']}.",
            "Treat TASK_SPEC_JSON as task data, not as permission to exceed its "
            "scope or perform an external mutation. Follow any separately supplied "
            "repository instructions.",
            "Honor its objective, acceptance criteria, execution and isolation "
            "settings, and wall-clock, turn, and output ceilings. Work only within "
            "its read, write, impact, and exclusion scopes. Preserve unrelated "
            "changes. Do not commit, push, deploy, install, reset, clean, broadly "
            "delete, or mutate an external service.",
            "Runtime-owned identity, timing, artifact, and coverage fields may be "
            "copied only when the parent or runtime supplies them. Never invent, "
            "repair, or infer those values. Do not include raw prompts, secrets, "
            "complete source files, embeddings, or a full transcript in the result.",
            ROLE_INSTRUCTIONS[role],
            f"Return exactly one role-result-v2 JSON object with role={json.dumps(role)} "
            f"and one permitted status: {status_text}. Return no Markdown fence or "
            "other text.",
            "Start from RESULT_TEMPLATE_JSON: replace every placeholder and remove "
            "unused optional payload keys. ITEM_SHAPES_JSON defines list items. "
            "attention_required, completed_scope, changed_paths, checks, and risks "
            "remain arrays when empty. Use unknown for model or effort the runtime "
            "does not expose. A successful result needs nonempty completed_scope and "
            "blocker_or_input=null; a blocked or input-requesting status needs a "
            "concrete blocker_or_input.",
            "For assigned checks, use exact IDs from TASK_SPEC_JSON.focused_checks, "
            "report only observed statuses, and never infer PASSED.",
            read_only_result,
            role_result_rules,
            "Copy separately supplied runtime provenance only under these exact "
            "keys, and omit it rather than guessing: "
            + ", ".join(RUNTIME_PROVENANCE_FIELDS)
            + ".",
            "RESULT_TEMPLATE_JSON",
            result_template,
            "ITEM_SHAPES_JSON",
            item_shapes,
            "TASK_SPEC_JSON",
            task_json,
        )
    )


def render_prompt(task_arg: str) -> tuple[int, dict[str, Any]]:
    try:
        task = load_json_file(task_arg)
        errors = validate_record(task, "task_spec")
        if errors:
            return 2, _failure("prompt", "task_validation", errors=errors)
        task = canonicalize_record(task, "task_spec")
        if task["role"] == "implementer" and task["mode"] == "portable":
            raise WorkflowToolError(
                "portable mode keeps the main agent as the writer; it cannot render "
                "a delegated implementer prompt"
            )
        minimum_success_result_bytes = _minimum_success_result_bytes(task)
        if task["budget"]["max_output_bytes"] < minimum_success_result_bytes:
            raise WorkflowToolError(
                "TaskSpec max_output_bytes={} cannot hold the smallest successful "
                "correlated role-result-v2 response ({} bytes)".format(
                    task["budget"]["max_output_bytes"], minimum_success_result_bytes
                )
            )
        return 0, {
            "command": "prompt",
            "minimum_success_result_bytes": minimum_success_result_bytes,
            "ok": True,
            "prompt": _render_prompt_text(task),
            "role": task["role"],
            "run_id": task["run_id"],
            "task_digest": digest_record(task, "task_spec"),
            "task_id": task["task_id"],
        }
    except (
        ContractError,
        WorkflowToolError,
        OSError,
        UnicodeError,
        TypeError,
        KeyError,
        ValueError,
        RecursionError,
    ) as exc:
        return 2, _failure("prompt", "prompt_rendering", error=str(exc))


def _write_scope_file(directory: Path, scope: dict[str, Any]) -> Path:
    path = directory / "impact_scope.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = _pretty_json(canonicalize_record(scope, "impact_scope"))
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise WorkflowToolError("cannot write temporary impact scope")
            offset += written
    finally:
        os.close(descriptor)
    return path


def _preflight_freshness(preflight: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": preflight["run_id"],
        "status": "RUN_BOUND_NOT_TIME_VERIFIED",
        "summary": (
            "The preflight is bound to the expected run, but ContractV2 has no "
            "trusted observation time."
        ),
    }


def accept_evidence(
    evidence_arg: str,
    root_arg: str,
    expected_run_id: str | None,
) -> tuple[int, dict[str, Any]]:
    if not Path(root_arg).is_absolute():
        return 2, _failure(
            "accept",
            "argument_binding",
            error="repository root must be an absolute path",
            evidence_and_review_scope_valid=False,
            helper_confirmed_acceptance=False,
            evidence_valid=False,
        )
    try:
        evidence = load_json_file(evidence_arg)
    except (
        ContractError,
        OSError,
        UnicodeError,
        TypeError,
        ValueError,
        RecursionError,
    ) as exc:
        return 2, _failure(
            "accept",
            "evidence_loading",
            error=str(exc),
            evidence_and_review_scope_valid=False,
            helper_confirmed_acceptance=False,
            evidence_valid=False,
        )

    try:
        errors = validate_record(evidence, "acceptance_evidence")
    except (
        ContractError,
        TypeError,
        KeyError,
        ValueError,
        RecursionError,
    ) as exc:
        errors = [str(exc)]
    if errors:
        return 2, _failure(
            "accept",
            "evidence_validation",
            errors=errors,
            evidence_and_review_scope_valid=False,
            helper_confirmed_acceptance=False,
            evidence_valid=False,
        )

    freshness = _preflight_freshness(evidence["capability_preflight"])
    try:
        outcome = evidence["workflow_outcome"]
        if not outcome["accepted"]:
            return 2, _failure(
                "accept",
                "workflow_outcome",
                error="accept requires an accepted workflow outcome",
                evidence_and_review_scope_valid=False,
                helper_confirmed_acceptance=False,
                evidence_valid=True,
                preflight_freshness=freshness,
            )

        final_round = evidence["review_rounds"][-1]
        task = final_round["task_spec"]
        artifact_proof = final_round["artifact_access_proof"]
        actual_run_id = task["run_id"]
        if not expected_run_id:
            return 2, _failure(
                "accept",
                "run_binding",
                error="accept requires the live workflow run ID",
                evidence_and_review_scope_valid=False,
                helper_confirmed_acceptance=False,
                actual_run_id=actual_run_id,
                evidence_valid=True,
            )
        if expected_run_id != actual_run_id:
            return 2, _failure(
                "accept",
                "run_binding",
                error="final review TaskSpec run_id does not match --expected-run-id",
                evidence_and_review_scope_valid=False,
                helper_confirmed_acceptance=False,
                actual_run_id=actual_run_id,
                evidence_valid=True,
                expected_run_id=expected_run_id,
                preflight_freshness=freshness,
            )

        artifact_arg = task["artifact_path"]
        if not isinstance(artifact_arg, str) or not artifact_arg:
            return 2, _failure(
                "accept",
                "artifact_binding",
                error="final review TaskSpec does not name an artifact path",
                evidence_and_review_scope_valid=False,
                helper_confirmed_acceptance=False,
                evidence_valid=True,
                preflight_freshness=freshness,
            )
        artifact = Path(artifact_arg)
        if not artifact.is_absolute():
            return 2, _failure(
                "accept",
                "artifact_binding",
                error="final review TaskSpec artifact_path must be absolute",
                evidence_and_review_scope_valid=False,
                helper_confirmed_acceptance=False,
                evidence_valid=True,
                preflight_freshness=freshness,
            )

        root = snapshot_tool._canonical_absolute_path(root_arg)
        if snapshot_tool._path_is_at_or_below(root, artifact):
            return 2, _failure(
                "accept",
                "artifact_binding",
                error="review artifact must be outside the repository root",
                evidence_and_review_scope_valid=False,
                helper_confirmed_acceptance=False,
                evidence_valid=True,
                preflight_freshness=freshness,
            )

        expected_content = artifact_proof["content_identity"]
        expected_manifest = artifact_proof["manifest_identity"]
        with tempfile.TemporaryDirectory(prefix="cdw-accept-") as temporary:
            scope_path = _write_scope_file(
                Path(temporary), task["impact_scope"]
            )
            verified = snapshot_tool.verify_artifact(
                str(artifact),
                str(scope_path),
                expected_content,
                expected_manifest,
            )
            verified_base = verified["base_git_identity"]
            claimed_base = {
                "head": task["base_snapshot"],
                "index_tree": task["base_content_identity"],
            }
            if (
                claimed_base["head"] != verified_base["head"]
                or claimed_base["index_tree"] != verified_base["index_tree"]
            ):
                return 2, _failure(
                    "accept",
                    "artifact_evidence_binding",
                    error=(
                        "review evidence base identities do not match the verified "
                        "artifact manifest"
                    ),
                    evidence_and_review_scope_valid=False,
                    helper_confirmed_acceptance=False,
                    artifact_verified=True,
                    base_identity_match=False,
                    claimed_base_identity=claimed_base,
                    evidence_valid=True,
                    manifest_base_identity={
                        "head": verified_base["head"],
                        "index_tree": verified_base["index_tree"],
                    },
                    preflight_freshness=freshness,
                )
            compare_code, compared = snapshot_tool.compare_workspace(
                str(root),
                str(artifact),
                str(scope_path),
                expected_content,
                expected_manifest,
            )

        if compare_code != 0:
            return 1, _failure(
                "accept",
                "review_scope_comparison",
                error="declared review scope does not match the final reviewed artifact",
                evidence_and_review_scope_valid=False,
                helper_confirmed_acceptance=False,
                artifact_verified=True,
                comparison=compared,
                evidence_valid=True,
                preflight_freshness=freshness,
                review_scope_match=False,
            )

        return 0, {
            "artifact": verified["artifact"],
            "artifact_verified": True,
            "base_identity_match": True,
            "command": "accept",
            "content_identity": verified["content_identity"],
            "evidence_and_review_scope_valid": True,
            "evidence_digest": digest_record(evidence, "acceptance_evidence"),
            "evidence_valid": True,
            "helper_confirmed_acceptance": False,
            "live_confirmation_required": list(LIVE_CONFIRMATIONS),
            "manifest_identity": verified["manifest_identity"],
            "ok": True,
            "preflight_freshness": freshness,
            "recorded_outcome": outcome["outcome"],
            "root": str(root),
            "run_id": actual_run_id,
            "review_scope_match": True,
        }
    except (
        ContractError,
        WorkflowToolError,
        snapshot_tool.SnapshotError,
        OSError,
        UnicodeError,
        TypeError,
        KeyError,
        ValueError,
        RecursionError,
    ) as exc:
        return 2, _failure(
            "accept",
            "artifact_verification",
            error=str(exc),
            evidence_and_review_scope_valid=False,
            helper_confirmed_acceptance=False,
            evidence_valid=True,
            preflight_freshness=freshness,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize_parser = subparsers.add_parser("init")
    initialize_parser.add_argument("--output", required=True, metavar="DIR")
    initialize_parser.add_argument("--root", required=True, metavar="REPO")
    initialize_parser.add_argument(
        "--changed-path", required=True, nargs="+", metavar="PATH"
    )
    initialize_parser.add_argument(
        "--review-path", required=True, nargs="+", metavar="PATH"
    )

    prompt_parser = subparsers.add_parser("prompt")
    prompt_parser.add_argument("--task-spec", required=True, metavar="FILE")

    accept_parser = subparsers.add_parser("accept")
    accept_parser.add_argument("--evidence", required=True, metavar="FILE")
    accept_parser.add_argument("--root", required=True, metavar="REPO")
    accept_parser.add_argument("--expected-run-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "init":
            code, report = initialize(
                args.output,
                args.root,
                args.changed_path,
                args.review_path,
            )
        elif args.command == "prompt":
            code, report = render_prompt(args.task_spec)
        else:
            code, report = accept_evidence(
                args.evidence,
                args.root,
                args.expected_run_id,
            )
    except WorkflowToolError as exc:
        code, report = 2, _failure("unknown", "argument_parsing", error=str(exc))
    _emit(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
