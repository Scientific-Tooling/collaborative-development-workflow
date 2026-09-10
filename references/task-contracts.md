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
`contract_tool.py` before delegation. Query the effective closed shape instead of
copying a field inventory into a prompt:

```bash
python3 scripts/contract_tool.py describe --kind task_spec
python3 scripts/contract_tool.py validate --kind acceptance_evidence \
  examples/acceptance_evidence.json
```

The [complete portable example](../examples/acceptance_evidence.json) contains a
reviewer TaskSpec. `mode` selects portable or strict operation; `binding_mode`
separately records provisional transport binding or runtime-atomic binding.
`model_profile` records exposed provenance without prescribing a global model.
`budget` is an `execution-budget-v2` object. The parent preassigns proof IDs and
supplies the snapshot ID (the manifest identity), content identity, and artifact
path; the reviewer does not invent them.

## Scope and focused checks

The closed V2 shapes are:

```text
ImpactScopeV2 = {
  version: "impact-scope-v2",
  changed_paths: 0..128 unique repository-relative paths,
  review_paths: 0..128 unique repository-relative paths,
  direct_callers: 0..128 unique bounded references,
  direct_consumers: 0..128 unique bounded references,
  mapped_tests_or_configuration: 0..128 unique bounded references,
  explicit_exclusions: 0..128 unique bounded references
}

FocusedCheckV2 = {
  version: "focused-check-v2",
  id: bounded token,
  command_or_assertion: bounded text,
  covered_scope: 0..64 unique bounded references (nonempty when required),
  required: boolean
}
```

`changed_paths` is the intended integration and commit set. `review_paths` must
cover it and also includes callers, consumers, tests, configuration, and supporting
material needed without live-workspace access. Snapshots use `review_paths`.

Path collections are set-like in V2 and are sorted in their canonical form. A
path is repository-relative POSIX text: backslashes are converted to `/`, empty
and dot components are normalized away, while absolute paths, parent traversal,
NUL bytes, and duplicates after normalization are rejected. Scope and check
entries are bounded; do not silently truncate them.

`review_paths` and path-shaped `explicit_exclusions` must be disjoint scopes:
neither may equal, contain, or be contained by the other. In particular, an
excluded descendant cannot ride along through a recursively captured review
directory.

Within each collection, semantic identifiers such as check `id`,
`criterion_id`, and `artifact_id` are unique. Every required focused check must
overlap the declared review paths, and the required checks collectively cover all
`changed_paths`; a matching check ID alone is not coverage.

## Capability preflight

Before any edit or spawn, record a `capability-preflight-v2` value and validate it.
Portable mode uses `modes.required_capabilities.portable`; strict mode adds
`modes.required_capabilities.strict_additional`. `contract_tool.py` deliberately
does not attest `STRICT_READY`: the authoritative runtime adapter must supply and
validate that proof.

Capability maps contain exactly the keys required by their selected mode. Portable
records do not carry placeholder values for strict-only capabilities.

The portable observation may be based on the exposed tool surface. A strict result
must be an authoritative runtime record. `NOT_READY` blocks the requested mode;
strict is never silently downgraded to portable.

## Status and runtime references

Status values and role-specific success rules are declared only in
`contracts-v2.json: statuses`. `BLOCKED` is a bounded role result and must not be
inferred from silence. `REVIEW_UNAVAILABLE` means no usable independent reviewer
result was delivered; `REVIEW_BLOCKED` means a result exists but its evidence cannot
be validated. Both are non-accepting in portable mode.

Runtime event shapes are declared in `contracts-v2.json`; strict binding, timing,
and lifecycle semantics belong to
[`review-runtime-strict.md`](review-runtime-strict.md). Portable review result
classification belongs to [`review-runtime.md`](review-runtime.md). The machine records are
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
snapshot/content identity, preassigned artifact-access and coverage proof IDs, reviewed
paths, findings, the bounded closed `role_payload`, and the actual model profile. A
child may repeat provenance for correlation but cannot invent or repair runtime-owned
fields.

Role success is selected by role in the JSON definition. Read-only roles report
`changed_paths=[]`; `REVIEW_UNAVAILABLE` is a parent disposition, not a child
result. A reviewer `CLEAN` binds the task/run, snapshot, content, and proof IDs,
covers every review path, passes every reported check, and contains no open finding
or risk. `FINDINGS` includes at least one bounded actionable finding inside the
reviewed paths. A clean test result alone is never a review result.

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
python3 scripts/contract_tool.py describe --path records.role_result.fields.status
python3 scripts/contract_tool.py describe --path modes.required_capabilities.portable
```

`describe --path` resolves a dotted path against the effective expanded ContractV2
and emits only that subtree, which is preferable to reading the whole JSON file.
Exit `0` means valid; exit `2` means invalid input or contract. Diagnostics are
machine-readable JSON.

## Snapshot and review proofs

`TaskSpecV2` names the exact snapshot and content identity. The manifest contract is
`contracts-v2.json: artifact_contracts.snapshot_manifest`; it owns the version,
closed fields, entry values, artifact prefixes, and bounded counts. Creation,
verification, permissions, symlink/race checks, baseline capture, and workspace
comparison are owned by `scripts/snapshot_tool.py` and described in
[`review-runtime.md`](review-runtime.md).

Artifact access, coverage, and review rounds are structured records whose digests
are recomputed by the validator. A `workflow-outcome-v2` is a disposition, not
sufficient acceptance evidence by itself. `acceptance-evidence-v2` binds the
preflight, one to three contiguous review rounds, required checks, full-validation
result, and final snapshot identities. Accepted evidence requires every final
TaskSpec acceptance criterion to be `met`. Every content-changing findings fix
starts a new fully covered round; coverage never transfers across identities.
Objective, criterion definitions, focused checks, and impact scope are immutable
within one round sequence. A change to any of them starts a newly authorized task
and evidence bundle.

## Context handoff

`context-handoff-v2` is a bounded parent-owned handoff. `mode=independent` has no
checkpoint, artifacts, or active work; it starts a new TaskSpec and does not inherit
unrelated history. `mode=continuation` has exactly one validated checkpoint and must
include an artifact with the matching `content_identity` whenever that checkpoint
names content. The full capture/resume procedure is in
[`context-rollover.md`](context-rollover.md).

Neither a result, snapshot, nor handoff grants permission to expand scope or perform
an external mutation. Acceptance and commit remain parent-owned gates.
