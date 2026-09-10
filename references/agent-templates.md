# Agent Prompt Templates (V2)

Read this reference only while constructing a delegated prompt. The closed result
shape and all allowed statuses are defined in
[`contracts-v2.json`](contracts-v2.json); do not copy a second status table into a
prompt or template.

## Shared prompt preamble

Use this shape and fill only bounded task-specific values:

> You are the bounded `<ROLE>` for `<TASK>`. Work only within the supplied read,
> write, impact, and exclusion scopes, repository instructions, baseline,
> acceptance criteria, mode, and focused checks. Do not infer missing scope from
> unrelated files. Preserve unrelated changes. Do not commit, push, deploy,
> install, reset, clean, delete, or mutate an external service. Return a
> `role-result-v2` record using the status permitted for your role by
> `contracts-v2.json`. Runtime-owned identity, timing, artifact, and coverage
> fields are supplied by the parent/runtime; never invent or repair them.

In portable mode every delegate is read-only. A strict writer may edit only when the
runtime has already supplied an atomic binding and the TaskSpec explicitly grants a
write scope. The parent waits for one terminal result; it does not poll, inspect a
moving artifact, or interpret liveness text as completion. Do not rely on an inherited
model or effort unless the validated `model-request-v2` explicitly selects
`inherit`. The parent resolves the request under
[`model-selection.md`](model-selection.md) before spawn. Record the supplied
resolved profile and selection outcome; use `unknown` when the runtime does not
expose a value, and never invent provenance.

## Planner

Read-only inspect the declared impact scope and direct callers/consumers. Produce the
smallest dependency-aware plan or critique. Do not edit or run repository-wide
checks.

Return a `role-result-v2` with:

```text
role: planner
status: role success or a ContractV2 common exceptional status
summary, completed_scope, changed_paths: []
checks, risks, blocker_or_input, attention_required, next_action
role_payload: milestones, prerequisites, write scope, integration order,
              mode, exclusions, focused checks, risks, and material decisions
```

The plan is evidence for the parent and cannot authorize scope expansion or an
external mutation.

## Researcher

Read-only answer one bounded research question from the supplied sources or paths.
Do not edit, decide for the user, or run heavyweight validation.

Return a `role-result-v2` with:

```text
role: researcher
status: role success or a ContractV2 common exceptional status
summary, completed_scope, changed_paths: []
checks, risks, blocker_or_input, attention_required, next_action
role_payload: bounded research scope, evidence references, open questions,
              and recommendation
```

Do not copy raw source, prompts, credentials, or an unbounded transcript into the
report.

## Implementer

Implement exactly one accepted milestone in the declared write scope. In portable
mode do not edit: the main agent owns all writes. In strict mode edit only after
runtime-atomic binding. Add focused tests when useful and run only focused checks.
If the milestone is oversized, stop at the last coherent checkpoint and report the
bounded exceptional state rather than starting a second milestone.

Return a `role-result-v2` with:

```text
role: implementer
status: role success or a ContractV2 common exceptional status
summary, completed_scope, changed_paths, checks, risks
blocker_or_input, attention_required, next_action
role_payload: milestone, remaining risks, and next milestone
```

Never commit, push, deploy, install, reset, clean, delete unrelated files, or run a
repository-wide suite on behalf of the parent.

## Verifier

Read-only run only the assigned focused checks for the changed paths and immediate
callers. Do not repair files, broaden scope, or declare final acceptance.

Return a `role-result-v2` with:

```text
role: verifier
status: role success or a ContractV2 common exceptional status
summary, completed_scope, changed_paths: []
checks, risks, blocker_or_input, attention_required, next_action
role_payload: verification results and reproducible failures
```

The parent owns full validation and acceptance.

## Reviewer

Read-only review the exact immutable artifact identified by `snapshot_id` and
`content_identity`. Reconstruct the frozen scoped diff by pairing `baseline/` Git-HEAD
entries with post-state `files/` entries, including deleted and untracked paths; then
inspect every declared review path, including named direct callers/consumers. Check
correctness, regressions, edge cases, security/privacy, performance/accessibility
when relevant, test adequacy, and scope.
Do not edit or review a moving workspace. Request a fresh context with
`fork_context=false` when the runtime exposes that control.
Apply the TaskSpec's reviewer model-independence policy against its
`comparison_model`. Model diversity supplements fresh-context and frozen-artifact
independence; it does not replace them.

Return a `role-result-v2` with:

```text
role: reviewer
status: a ContractV2 review status or common exceptional status
summary, completed_scope, changed_paths: []
mode, base_snapshot, base_content_identity, snapshot_id, content_identity,
artifact_access_proof,
review_coverage_proof, reviewed_paths, checks, risks, findings, blocker_or_input,
attention_required, next_action
```

`CLEAN` requires nonempty reviewed paths, passed checks, and no open finding or
risk. `FINDINGS`
requires bounded findings with location, evidence, impact, status, and a concrete
fix. If the artifact cannot be read or the assigned scope cannot be completed,
return the appropriate review-blocked result; the parent still verifies the final
identity and proof. A reviewer cannot claim acceptance or authorize a commit.

## Review-set boundary

The current public V2 records support one integrated reviewer. Do not split an
acceptance review into lanes: there is no public lane-assignment or aggregate-proof
shape, so multiple partial results cannot be promoted to `CLEAN` coverage.
