# Task and Result Contracts (ContractV2)

This reference explains the machine-readable source at
[`contracts-v2.json`](contracts-v2.json). The JSON definition is authoritative
for field names, closed shapes, limits, status values, capability names, and hash
domains. `scripts/contract_tool.py` is the dependency-free conformance checker.

Text from older installations that uses V1 record names is legacy and unsupported
by the V2 helpers. A record must not mix versions.

## TaskSpecV2

Create one bounded task specification before every delegation. Keep it in the
parent plan or runtime ledger; a child never infers missing scope from the
repository. The machine-readable record kind is `task_spec`; validate it with
`contract_tool.py` before delegation.

```text
TASK_ID, PARENT_TASK_ID, RUN_ID
INVOCATION_ID, BINDING_TOKEN, BINDING_MODE
ROLE: researcher | planner | implementer | reviewer | verifier
MODE: portable | strict
OBJECTIVE, DEPENDS_ON, ACCEPTANCE_CRITERIA
READ_SCOPE, WRITE_SCOPE, IMPACT_SCOPE, BASE_SNAPSHOT, BASE_CONTENT_IDENTITY
FOCUSED_CHECKS, EXECUTION, ISOLATION, BUDGET, RESUMABLE
MODEL_PROFILE: actual model/effort when exposed, otherwise UNKNOWN
FULL_SUITE_OWNER: main
SNAPSHOT: snapshot_id, artifact_path, content_identity
ARTIFACT_ACCESS_PROOF, REVIEW_COVERAGE_PROOF
TIMING, RECOVERY: strict runtime fields only when strict is available
```

`MODEL_PROFILE` records provenance; it does not prescribe a globally hard-coded
model or effort. The initial child prompt omits runtime-owned identity, target,
terminal, timing, and proof fields. The parent/runtime supplies them after the
appropriate preflight.

## Scope and focused checks

The closed V2 shapes are:

```text
ImpactScopeV2 = {
  version: "impact-scope-v2",
  changed_paths: 0..128 unique repository-relative paths,
  direct_callers: 0..128 unique bounded references,
  direct_consumers: 0..128 unique bounded references,
  mapped_tests_or_configuration: 0..128 unique bounded references,
  explicit_exclusions: 0..128 unique bounded references
}

FocusedCheckV2 = {
  version: "focused-check-v2",
  id: bounded token,
  command_or_assertion: bounded text,
  covered_scope: 0..64 unique bounded references,
  required: boolean
}
```

Path collections are set-like in V2 and are sorted in their canonical form. A
path is repository-relative POSIX text: backslashes are converted to `/`, empty
and dot components are normalized away, while absolute paths, parent traversal,
NUL bytes, and duplicates after normalization are rejected. Scope and check
entries are bounded; do not silently truncate them.

## Capability preflight

Before any edit or spawn, record a `capability-preflight-v2` value and validate it.
Portable mode uses `modes.required_capabilities.portable`; strict mode adds
`modes.required_capabilities.strict_additional`. `contract_tool.py` deliberately
does not attest `STRICT_READY`: the authoritative runtime adapter must supply and
validate that proof.

The portable observation may be based on the exposed tool surface. A strict result
must be an authoritative runtime record. `NOT_READY` blocks the requested mode;
strict is never silently downgraded to portable.

## Status and runtime references

Status values and role-specific success rules are declared only in
`contracts-v2.json: statuses`. `BLOCKED` is a bounded role result and must not be
inferred from silence. `REVIEW_UNAVAILABLE` means no usable independent reviewer
result was delivered; `REVIEW_BLOCKED` means a result exists but its evidence cannot
be validated. Both are non-accepting in portable mode.

Runtime event shapes and lifecycle semantics belong to
[`review-runtime.md`](review-runtime.md). The machine records are
`records.runtime_completion_event`, `records.runtime_terminal_event`,
`records.runtime_stop_event`, and `records.runtime_event_sequence` in
`contracts-v2.json`; a model-written completion string is never an event.

## Role results

Every `role-result-v2` record has these bounded common fields:

```text
ROLE, STATUS, SUMMARY, COMPLETED_SCOPE, CHANGED_PATHS
CHECKS, RISKS, BLOCKER_OR_INPUT, ATTENTION_REQUIRED, NEXT_ACTION
```

It may include runtime/parent provenance fields such as task and invocation IDs,
snapshot/content identity, artifact access proof, review coverage proof, reviewed
paths, findings, the bounded closed `role_payload`, and the actual model profile. A
child may repeat provenance for correlation but cannot invent or repair runtime-owned
fields.

Role success is selected by role in the JSON definition. Exceptional statuses are
valid only under the common list. A reviewer `CLEAN` must include nonempty
`reviewed_paths`; `FINDINGS` must include at least one bounded finding with severity,
path, line, evidence, impact, status, and a concrete fix. A clean test result alone
is never a review result.

## Report and data boundary

The ledger and runtime event journal contain metadata only: IDs, roles, paths,
states, hashes, budgets, timestamps, commands, and outcomes. A bounded report
artifact may contain a short summary, check results, and path/line findings. It must
not contain raw prompts, credentials, secrets, complete source files, embeddings,
or an unbounded model transcript. Scoped source content may exist only in a private
review artifact and is never copied into the ledger or handoff.

## Canonical JSON and digest

For every helper input:

1. decode UTF-8 and reject malformed text, duplicate object keys, and non-finite
   numbers;
2. validate the closed V2 record and all bounds;
3. normalize repository paths and sort set-like collections;
4. encode with UTF-8, `ensure_ascii=false`, lexicographically sorted object keys,
   compact separators, and no insignificant whitespace; and
5. hash the exact bytes of `{"kind": KIND, "record": RECORD}` with SHA-256 after
   prepending the `record` domain prefix from `contracts-v2.json`.

ContractV2 currently uses integer numeric fields only. Finite floating-point
values are not a contract value; rejecting them avoids cross-runtime number-format
ambiguity. Unicode content is preserved as supplied; no implicit locale or Unicode
normalization is performed.

The command interface is:

```bash
python3 scripts/contract_tool.py validate --kind impact_scope scope.json
python3 scripts/contract_tool.py digest --kind role_result result.json
```

Exit `0` means valid; exit `2` means invalid input or contract. Diagnostics are
machine-readable JSON.

## Snapshot and review proofs

`TaskSpecV2` names the exact snapshot and content identity. The manifest contract is
`contracts-v2.json: artifact_contracts.snapshot_manifest`; it owns the version,
closed fields, entry values, artifact prefixes, and bounded counts. Creation,
verification, permissions, symlink/race checks, baseline capture, and workspace
comparison are owned by `scripts/snapshot_tool.py` and described in
[`review-runtime.md`](review-runtime.md).

## Context handoff

`context-handoff-v2` is a bounded parent-owned handoff. `mode=independent` has no
checkpoint, artifacts, or active work; it starts a new TaskSpec and does not inherit
unrelated history. `mode=continuation` has exactly one validated checkpoint and may
reference its artifact/content identity. The full capture/resume procedure is in
[`context-rollover.md`](context-rollover.md).

Neither a result, snapshot, nor handoff grants permission to expand scope or perform
an external mutation. Acceptance and commit remain parent-owned gates.
