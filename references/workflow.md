# Workflow and Acceptance (V2)

This file owns live preflight and acceptance. Contract fields are in
[`contracts-v2.json`](contracts-v2.json); detailed snapshot guarantees are in
[`review-runtime.md`](review-runtime.md).

Set `CDW_SKILL_DIR` to the absolute directory containing the selected `SKILL.md`,
not from the target repository's working directory.

## 1. Inspect, preflight, and scope

Before any target-repository edit or delegation:

1. Restate the outcome and acceptance criteria. Read applicable `AGENTS.md`, project
   guidance, likely paths and consumers, tests, configuration, and validation.
2. Record Git status, branch, HEAD, index, and baseline. Preserve all existing work
   and report overlap with required files.
3. For broad or unclear work, use only the bounded discovery procedure in
   [`coordination-protocol.md`](coordination-protocol.md). Its provisional scope and
   separate run ID cannot become acceptance evidence.
4. Before editing, finalize `ImpactScopeV2`, risks, dependencies, checks,
   exclusions, and acceptance target. Put every relevant path-shaped caller,
   consumer, test, and configuration item in `review_paths`. Expand only for a
   concrete reason.
5. Default to `portable`. For requested `strict` mode, first follow
   [`review-runtime-strict.md`](review-runtime-strict.md); missing capability stops
   before mutation and never causes a portable fallback.
6. Put `model-request-v2` in every delegated TaskSpec. Normal non-reviewer defaults
   are `inherit` / `fail` / `same_allowed`; reviewer defaults are
   `runtime_default` / `fail` / `same_allowed`. Read
   [`model-selection.md`](model-selection.md) only for an explicit model or effort,
   fallback, strict selection, or reviewer diversity.

To create starter records outside the repository:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" init \
  --output /tmp/cdw-task-1 --root /absolute/path/to/repository \
  --changed-path src/example.py --review-path tests/test_example.py
```

`init` emits `NOT_READY`. Replace placeholders only with observed facts, then
validate `capability-preflight-v2`. Portable readiness requires Linux snapshots,
an identifiable enforced-read-only reviewer, terminal result delivery, and shared
snapshot access. Prompts, examples, and saved `ready` records are not live checks.
The `run_id` must match the review TaskSpec. `custom_agent_sandbox` is truthful only
after that installed agent and its effective settings are confirmed; `unverified`
cannot be ready. Record the actual `artifact_only_read_enforcement`; it does not
gate readiness, and read-only alone does not prove exclusive artifact access.

For an ordinary delegation, validate and render the final TaskSpec:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" prompt \
  --task-spec /absolute/path/to/task-spec.json
```

Use the returned `prompt`; the parent also supplies applicable repository
instructions and live runtime facts. [`agent-templates.md`](agent-templates.md) is
only a fallback/customization path and cannot weaken TaskSpec scope or result shape.

## 2. Plan and implement

Record scope, dependencies, milestones, ownership, checks, exclusions, risks,
integration order, and one main-agent-owned full-validation command. A delegated
plan cannot expand scope. Pause for decisions changing safety, behavior, scope,
cost, or authorization.

The main agent is the only portable writer. Change only declared work and validate
child results. For multiple assignments or worktrees, follow
[`coordination-protocol.md`](coordination-protocol.md). Review only the integrated
identity, never a moving workspace or child worktree.

## 3. Validate, freeze, and review

Run every focused check and full validation. Inspect all diff/status and remove
only task-created disposable output. Freeze exactly `review_paths` outside the
repository, then record and verify both emitted identities:

```bash
TASK_ID=task-1
ARTIFACT="/tmp/cdw-review-${TASK_ID}"
python3 "$CDW_SKILL_DIR/scripts/snapshot_tool.py" create /absolute/path/to/repository \
  --scope /absolute/path/to/task-scope.json --output "$ARTIFACT"
python3 "$CDW_SKILL_DIR/scripts/snapshot_tool.py" verify "$ARTIFACT" \
  --scope /absolute/path/to/task-scope.json \
  --expected-content-identity CONTENT_IDENTITY \
  --expected-manifest-identity MANIFEST_IDENTITY
```

Pause all writers and edits. Start one fresh no-history reviewer for that artifact
with the validated prompt; confirm its effective read-only sandbox and disabled approvals,
and record the actual artifact read boundary. Read
[`review-runtime.md`](review-runtime.md) only for reviewer setup, helper guarantees,
or troubleshooting.

ContractV2 accepts one integrated reviewer; partial lanes cannot combine into
`CLEAN`. Validate identity, scope, artifact access, coverage, checks, and model
provenance. Route findings, malformed output, and unavailable review to
[`review-recovery.md`](review-recovery.md). `REVIEW_UNAVAILABLE` or
`REVIEW_BLOCKED` yields `NOT_ACCEPTED`, never a commit.

## 4. Accept

After review, do only read-only acceptance work:

1. Inspect the full diff, status, staged boundary, and generated, secret, and debug
   changes. Focused checks must cover every changed path.
2. Create complete `acceptance-evidence-v2` with the result, proofs, checks, model
   policy and resolution, full validation, and outcome.
3. Run:

   ```bash
   python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" accept \
     --evidence /absolute/path/to/acceptance-evidence.json \
     --root /absolute/path/to/repository --expected-run-id RUN_ID
   ```

Exit `0` means consistent evidence, a reopened verified artifact, matching claimed
base Git identities, and a workspace matching the declared review scope. Exit `1`
means a scope mismatch; `2` means invalid input, binding, record, or artifact.
`review_scope_match` excludes unrelated tracked files; still inspect all diff/status.

The helper cannot observe reviewer invocation/sandbox or prove preflight timing;
`RUN_BOUND_NOT_TIME_VERIFIED` states this. It always leaves
`helper_confirmed_acceptance=false`. Confirm every `live_confirmation_required`
item, including paused writers and unchanged scoped content and Git identity. Exit
`0` alone is never acceptance.

Do not run a mutating formatter, generator, or test after freezing. Any changed
scoped content or Git identity invalidates review and requires validation and a new
snapshot.

Report `ACCEPTED_PORTABLE` only with a usable independent review, enforced
read-only sandbox, matching scope/artifact/workspace identities, passing required
and full checks, all criteria `met`, and consistent evidence. This does not prove
exclusive artifact access unless enforced and recorded. Only an outside adapter
can produce `ACCEPTED_STRICT`. Otherwise report `NOT_ACCEPTED` with the exact V2
reason.

Report what changed, checks, review conclusion, acceptance status, and remaining
risks. Mention a commit only if created. For a blocked handoff or requested commit,
use [`failure-and-reporting.md`](failure-and-reporting.md). External changes require
separate authorization.
