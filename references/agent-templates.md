# Agent Prompt Guidance (V2)

For a standard delegated prompt, render it from a validated TaskSpec:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" prompt \
  --task-spec /absolute/path/to/task-spec.json
```

The command returns a JSON report whose `prompt` value includes the canonical task
data, role limits, current allowed statuses, and required result fields. Prefer it
to a handwritten template. Read this reference only to understand or add bounded
role-specific behavior. The closed result shape remains authoritative in
[`contracts-v2.json`](contracts-v2.json).

## Rules for every role

Any added instruction must keep these rules:

- Work only within the TaskSpec's read, write, impact, and exclusion scopes,
  repository instructions, baseline, criteria, mode, and focused checks. Do not
  infer missing scope. Preserve unrelated changes.
- Do not commit, push, deploy, install, reset, clean, broadly delete, or change an
  external service.
- In portable mode, every delegate must have an enforced read-only sandbox. A
  prompt-only promise is not enough. A strict implementer may edit only after an
  atomic runtime binding and an explicit write scope.
- Wait for one terminal result. Do not poll, inspect a changing artifact, or treat
  liveness text as completion.
- Resolve the model request under [`model-selection.md`](model-selection.md)
  before spawn. Inherit only when requested. Record supplied model provenance and
  use `unknown` only when the runtime does not expose a value.
- Return one valid `role-result-v2` JSON object with no Markdown wrapper. Lists
  remain arrays when empty, and read-only roles return `changed_paths: []`.
- Never invent or repair runtime-owned identity, timing, artifact, or coverage
  fields. Do not include raw source, prompts, credentials, secrets, embeddings, or
  a full transcript in a result.

Use the helper's generated prompt without weakening these rules. Add only facts or
instructions needed for the assigned task.

## Planner

Use `role: planner`. Read only the declared impact scope and direct callers and
consumers. Produce the smallest dependency-aware plan or critique. Do not edit or
run repository-wide checks. Put milestones, prerequisites, write scope,
integration order, mode, exclusions, focused checks, risks, and material decisions
in `role_payload`. The plan cannot expand scope or authorize an external change.

## Researcher

Use `role: researcher`. Answer one focused question from supplied sources or
paths. Do not edit, decide for the user, or run heavyweight validation.

For discovery before final scope, receive a preliminary read scope, explicit
exclusions, a focused question, and an output limit. Trace only enough callers,
consumers, tests, and configuration to suggest final impact and review paths.
Label them as suggestions; the parent validates final scope before any edit. Put
the limited scope, evidence references, open questions, and recommendation in
`role_payload`.

## Implementer

Use `role: implementer`. Implement one accepted milestone within the write
scope. Portable mode keeps all edits with the main agent; the prompt helper rejects
a portable delegated implementer. In strict mode, edit only after runtime-atomic
binding. Add useful focused tests and run only focused checks. If the milestone is
too large, stop at the last coherent checkpoint and report the allowed incomplete
state instead of starting another milestone. Put the milestone, remaining risks,
and next milestone in `role_payload`.

Do not run a repository-wide suite on the parent's behalf.

## Verifier

Use `role: verifier`. In a read-only sandbox, run only assigned focused checks for
changed paths and immediate callers. Do not repair files, broaden scope, or declare
final acceptance. Put reproducible failures and verification results in
`role_payload`. The parent owns full validation and acceptance.

## Reviewer

Use `role: reviewer`. In a read-only sandbox, review the exact immutable artifact
named by `snapshot_id` and `content_identity`. Reconstruct the frozen scoped diff
by pairing Git-HEAD entries in `baseline/` with post-state entries in `files/`,
including deleted and untracked paths. Inspect every review path and named direct
caller or consumer. Check correctness, regressions, edge cases, security and
privacy, test adequacy, scope, and relevant performance or accessibility concerns.

Do not review a changing workspace. Start without the parent's conversation and
receive only the review task and artifact. In the current collaboration adapter,
`fork_turns="none"` requests no inherited history; it does not restrict file
reads. Only a runtime container mount or read allowlist can enforce artifact-only
access.

Apply the reviewer-independence policy to `comparison_model`. Model diversity
supplements fresh context and a frozen artifact; it does not replace them. `CLEAN`
requires nonempty reviewed paths, passed checks, and no open finding or risk.
`FINDINGS` requires size-limited findings with a location, evidence, impact,
status, and concrete fix. If the artifact is unreadable or scope cannot be
completed, return the allowed blocked result. The parent still verifies identities
and proofs. A reviewer cannot accept the change or authorize a commit.

After validator errors, make at most one format-only correction in that review
round. Restate the same result; do not perform new analysis, change the conclusion,
invent proof, or revise findings to satisfy the schema.

## Review-set boundary

V2 supports one integrated reviewer. Do not divide an acceptance review into
lanes: there is no public lane-assignment or aggregate-proof record, so partial
results cannot become `CLEAN` coverage.
