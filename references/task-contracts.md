# Task and Result Contracts (ContractV2)

[`contracts-v2.json`](contracts-v2.json) is authoritative for record fields,
limits, status values, capability names, and hash domains. Use the dependency-free
helper instead of copying those details into prompts:

```bash
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind task_spec
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind role_result
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe \
  --path records.role_result.fields.status
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" validate \
  --kind acceptance_evidence \
  "$CDW_SKILL_DIR/examples/acceptance_evidence.json"
```

`describe --kind` prints an expanded record definition. `describe --path` prints
one subtree. Exit `0` means valid; exit `2` means invalid input or contract, with a
machine-readable JSON diagnostic. V1 names are unsupported, and a record must not
mix versions.
The [complete portable example](../examples/acceptance_evidence.json) contains a
reviewer TaskSpec. `mode` selects portable or strict operation; `binding_mode`
separately records provisional transport binding or runtime-atomic binding.
Every newly produced delegated task includes a `model-request-v2` selected under
[`model-selection.md`](model-selection.md). The request records strategy, requested
model/effort, fallback, and reviewer diversity without prescribing a global model.
It is schema-optional only for compatibility with 2.0.0 records. `model_profile`
records profile data exposed at dispatch.
`budget` is an `execution-budget-v2` object. The parent preassigns proof IDs and
supplies the snapshot ID (the manifest identity), content identity, and artifact
path; the reviewer does not invent them. For a reviewer, select the Standard or
Extended budget profile in [`review-runtime.md`](review-runtime.md) before freezing
and copy all three caps into this object. The budget is a protected execution
window, not a signal to interrupt the reviewer early.

## Task specifications

Create and validate one `task_spec` before each delegation. Keep it in the parent
plan or runtime ledger; the child must not infer missing scope from the repository.
A discovery task may use a preliminary read and impact scope under
[`workflow.md`](workflow.md), but it has its own run ID and returns suggestions,
not authority or acceptance evidence.

The portable [acceptance example](../examples/acceptance_evidence.json) is
fictional conformance data, not reusable runtime evidence. Its reviewer TaskSpec
shows these separate choices:

- `mode` selects portable or strict operation.
- `binding_mode` records provisional transport binding or runtime-atomic binding.
- `model_request` records selection intent under
  [`model-selection.md`](model-selection.md); `model_profile` records facts exposed
  at dispatch.
- `budget` has validation ceilings of 86,400 seconds, 128 turns, and 4,194,304
  output bytes. Choose smaller task limits when possible.

The parent preassigns proof IDs and supplies the snapshot ID, content identity,
and artifact path. A child must not invent them. Only an implementer may have a
nonempty `write_scope`. That scope must be covered by `impact_scope.review_paths`
and must cover every intended `changed_paths` entry.

## Scope and focused checks

Inspect the exact shapes when preparing them:

```bash
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind impact_scope
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind focused_check
```

`changed_paths` is the intended integration and commit set. `review_paths` must
cover it and every repository path named as a direct caller, consumer, test, or
configuration file. A file location such as `README.md:12` names `README.md`.
For a name that is not a path, use `api:`, `command:`, `module:`, `package:`,
`service:`, or `symbol:`. If a real path begins with one of those prefixes or
looks like a file location, prefix it with `file:`; for example,
`file:module:parser` names the root file `module:parser`. Snapshots use
`review_paths`. Add other supporting material needed for review. This scope design
does not prove that the reviewer is unable to read the live workspace.

Paths are repository-relative POSIX text. The helper converts backslashes to `/`,
removes empty and dot components, sorts set-like collections, and rejects absolute
paths, parent traversal, NUL bytes, and duplicates after normalization. It also
rejects oversized collections instead of truncating them.

`review_paths` and path-shaped exclusions must not equal, contain, or be contained
by each other. Required checks must collectively cover every changed path; a
matching check ID alone is not coverage. Each required check must also overlap the
review paths. Semantic IDs, including check, criterion, and artifact IDs, must be
unique within their collections.

## Capability preflight

Before any edit or spawn, create and validate a `capability_preflight`:
Before any edit or spawn, record a `capability-preflight-v2` value and validate it.
Portable mode uses `modes.required_capabilities.portable`, including the protected
Reviewer wait capability; strict mode adds
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

When a successful reviewer task carries `model_request`, its result profile binds
the resolved `model`, `effort`, and `selection_outcome` (`honored`, `fallback`, or
`unknown`). The review-round validator rejects a disallowed fallback, an
unattested fail-closed selection, a mismatched honored explicit request, or an
unsatisfied `different_required` comparison.

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
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe \
  --kind capability_preflight
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe \
  --path modes.required_capabilities.portable
```

Portable mode requires exactly its portable capability keys. Strict mode also
requires the strict keys. Do not add strict placeholders to a portable record.
The contract helper cannot attest `STRICT_READY`; only the runtime adapter can.
`NOT_READY` blocks the requested mode, and strict mode is never silently reduced
to portable mode.

The preflight `run_id` must match every reviewer TaskSpec in its acceptance
record. Record `read_only_enforcement` from the live reviewer invocation:

- Use `parent_sandbox` only after confirming that the reviewer inherits an
  effective read-only sandbox.
- Use `custom_agent_sandbox` only after installing, selecting, and confirming an
  effective read-only custom agent. A template file alone is not proof.
- Use `unverified` for prompt-only limits, unknown settings, or anything not
  checked live. It cannot produce a ready result.

Record the readable-path boundary separately in
`artifact_only_read_enforcement`: `container_mount` means only mounted review
input is visible; `read_allowlist` means only named paths are readable; `none`
means a shared readable filesystem; and `unknown` means it was not checked. A
read-only sandbox prevents writes but does not prove limited reads. `none` and
`unknown` do not by themselves block portable acceptance, but they cannot support
a claim of exclusive artifact access or confidentiality.

A saved example or previous preflight cannot prove current readiness.

## Results and status

Status values and role-specific success rules live in
`contracts-v2.json: statuses`; inspect them with `describe --path`. `BLOCKED` is a
bounded result, not an inference from silence. `REVIEW_UNAVAILABLE` means no usable
independent reviewer result arrived. `REVIEW_BLOCKED` means a result arrived but
its evidence could not be validated. Neither can support portable acceptance.

Runtime event shapes are also defined in the contract. Strict binding, timing,
and lifecycle rules are in [`review-runtime-strict.md`](review-runtime-strict.md),
and portable classification is in [`review-runtime.md`](review-runtime.md). A
model-written completion string is never a runtime event.

Return a complete `role-result-v2` using actual JSON types. In particular,
`attention_required` and the other list fields remain arrays when empty. The
required `model_profile` contains `model`, `effort`, and `selection_outcome`; use
`unknown` for a value the runtime does not expose. A child may repeat supplied
provenance for correlation, but it must not invent or repair runtime-owned fields.

When a successful reviewer TaskSpec has a model request, its result must satisfy
the resolved model, effort, fallback, outcome, and reviewer-diversity rules. A
read-only role has `changed_paths=[]`. A reviewer `CLEAN` result binds the task,
run, snapshot, content, and proof IDs; covers every review path; passes each
reported check; and has no open finding or risk. `FINDINGS` needs at least one
actionable finding in the reviewed paths. Passing tests alone is not a review.

A workflow outcome must agree with its check records. `PASSED` requires at least
one check, all checks passed, and a matching `full_validation_check_id`. `FAILED`
requires at least one failed check and a named full-validation check. `NOT_RUN`
cannot contain passed or failed checks or a full-validation ID. When acceptance
evidence contains a delivered review round, its disposition cannot be
`REVIEW_UNAVAILABLE`; `CLEAN` and `FINDINGS` must match the final result, while
`REVIEW_BLOCKED` records evidence that could not be trusted.

## Data and digests

The ledger and runtime journal contain metadata only: IDs, roles, paths, states,
hashes, budgets, timestamps, commands, and outcomes. A bounded report may add a
short summary, check results, and path/line findings. Do not put raw prompts,
credentials, secrets, complete source files, embeddings, or a full model
transcript in them. Scoped source may exist only in a private review artifact.

For `validate` and `digest`, the helper rejects malformed UTF-8, duplicate keys,
non-finite numbers, floating-point values, invalid shapes, and out-of-bounds data.
It normalizes paths and set-like collections, then encodes canonical UTF-8 JSON
with sorted object keys and no extra whitespace. The digest is SHA-256 over the
contract's `record` domain prefix followed by the exact canonical bytes of
`{"kind": KIND, "record": RECORD}`. Unicode is preserved without implicit locale
or normalization changes.

```bash
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" validate \
  --kind impact_scope /absolute/path/to/scope.json
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" digest \
  --kind role_result /absolute/path/to/result.json
```

These commands check record shape and internal agreement. They do not observe live
capabilities, start a reviewer, read a snapshot, or compare it with the workspace.
Complete the live checks in [`workflow.md`](workflow.md) before acceptance.

## Snapshot, review, and handoff records

The snapshot manifest shape is at
`artifact_contracts.snapshot_manifest`. Creation, verification, permissions,
symlink and race checks, baseline capture, and workspace comparison belong to
`snapshot_tool.py` and [`review-runtime.md`](review-runtime.md). The workflow
helper also compares the final TaskSpec and artifact-access proof base identities
with the verified manifest's Git HEAD and index identity.

Artifact-access, coverage, and review-round digests prove record consistency, not
runtime behavior. A workflow outcome alone is not acceptance evidence. An
`acceptance_evidence` record binds the preflight, one to three contiguous rounds,
required checks, full validation, and final snapshot identities. Every final
criterion must be met. A content-changing fix starts a new fully covered round;
coverage does not transfer. Changing the objective, criteria, checks, or scope
starts a new authorized task and evidence bundle. Even valid acceptance evidence
still requires the live sandbox, artifact, reviewer, and workspace checks.

For a `context_handoff`, independent mode carries no old checkpoint, artifact, or
active work. Continuation mode has one validated checkpoint and, when that
checkpoint names content, a matching artifact. See
[`context-rollover.md`](context-rollover.md).

No result, snapshot, or handoff grants permission to expand scope or perform an
external change. Acceptance and commit remain parent-owned decisions.
