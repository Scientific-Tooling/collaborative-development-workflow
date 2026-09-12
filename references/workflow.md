# Workflow and Acceptance (V2)

This file owns live preflight and acceptance. Contract fields are in
[`contracts-v2.json`](contracts-v2.json); snapshot guarantees are in
[`review-runtime.md`](review-runtime.md).

Set `CDW_SKILL_DIR` to the selected `SKILL.md` directory, not the target repository.

## 1. Inspect, preflight, and scope

Before any target-repository edit or delegation:

1. Restate the outcome and acceptance criteria. Read applicable `AGENTS.md`,
   project guidance, likely paths and consumers, tests, configuration, and
   validation.
2. Record Git status, branch, HEAD, index, and baseline. Preserve existing work
   and report overlap with required files.
3. For broad or unclear work, use the bounded discovery procedure in
   [`coordination-protocol.md`](coordination-protocol.md); its provisional scope
   and run ID cannot become acceptance evidence.
4. Finalize `ImpactScopeV2`, risks, dependencies, checks, exclusions, and target.
   Include every relevant path-shaped caller, consumer, test, and configuration
   item in `review_paths`; expand only for a concrete reason.
5. Default to `portable`. For requested `strict`, follow
   [`review-runtime-strict.md`](review-runtime-strict.md) first; missing capability
   stops before mutation and never causes a portable fallback.
6. Put `model-request-v2` in every delegated TaskSpec. Normal defaults are
   `inherit`/`fail`/`same_allowed`; Reviewer defaults are
   `runtime_default`/`fail`/`same_allowed`.

Use `workflow_tool.py init` to create starter records outside the repository;
it emits `NOT_READY`. Replace placeholders only with observed facts and validate
the preflight. Portable readiness requires Linux snapshots, an identifiable
enforced-read-only Reviewer, terminal delivery, shared snapshot access, and a
protected wait. Saved records are not live checks. Confirm effective
`custom_agent_sandbox` settings, record `artifact_only_read_enforcement`, and
match the preflight `run_id` to the review TaskSpec. Classify roles before work:
use the fast path only for at most five explicit files in one component with
deterministic criteria and no API/schema/generated/integration/security or
concurrency/lifecycle risk. The main agent then plans, writes, checks, and delegates
only the mandatory independent Reviewer; broad or high-risk work may add one
bounded role for a concrete uncertainty.

Render an ordinary delegation with `workflow_tool.py prompt` only after validating
its final TaskSpec, then add repository instructions and live runtime facts.

## 2. Plan and implement

Record scope, dependencies, milestones, ownership, checks, exclusions, risks,
integration order, and the main-agent full-validation command. A delegated plan
cannot expand scope. The main agent is the only portable writer; use
[`coordination-protocol.md`](coordination-protocol.md) for worktrees and review
only the integrated identity.

## 3. Validate, freeze, and review

Run all focused checks and full validation, inspecting diff/status and removing
only task-created disposable output. If full validation demonstrably subsumes a
focused check, record that coverage rather than invoking the same command twice.
Choose the Reviewer profile from
[`review-runtime.md`](review-runtime.md), freeze exactly `review_paths` outside the
repository, and record both emitted identities:

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

Pause writers. Bind the selected budget in the Reviewer TaskSpec, start one fresh
read-only Reviewer for the exact artifact, and use one foreground wait for its full
protected window. Do not poll, edit, cancel, interrupt, or roll over live context;
confirm read-only settings and artifact boundary. An incapable host is not ready.

ContractV2 accepts one integrated Reviewer; partial lanes cannot combine into
`CLEAN`. Validate identity, scope, access, coverage, checks, and provenance; route
findings or unavailable review to [`review-recovery.md`](review-recovery.md).
`REVIEW_UNAVAILABLE` and `REVIEW_BLOCKED` yield `NOT_ACCEPTED`.

## 4. Accept

After review, do only read-only acceptance work:

1. Inspect the full diff, status, staged boundary, generated/secret/debug changes,
   and required-check coverage.
2. Create complete `acceptance-evidence-v2` with result, proofs, checks, model
   policy/resolution, full validation, and outcome.
3. Run:

   ```bash
   python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" accept \
     --evidence /absolute/path/to/acceptance-evidence.json \
     --root /absolute/path/to/repository --expected-run-id RUN_ID
   ```

Exit `0` means consistent evidence, reopened artifact, matching base identities,
and a matching scoped workspace; `1` means scope mismatch and `2` invalid input,
binding, record, or artifact. The helper cannot observe Reviewer invocation,
sandbox, or preflight timing and leaves `helper_confirmed_acceptance=false`.

Do not run a mutating formatter, generator, or test after freezing. Any changed
scoped content or Git identity invalidates review and requires new validation and
a new snapshot.

Report `ACCEPTED_PORTABLE` with usable independent review, enforced read-only
sandbox, matching scope/artifact/workspace identities, passing checks, all criteria
`met`, and consistent evidence. This does not prove exclusive artifact access
unless enforced and recorded. Only an outside adapter can produce
`ACCEPTED_STRICT`; otherwise report `NOT_ACCEPTED` with the exact V2 reason.

Report changes, checks, review conclusion, acceptance, and remaining risks.
Mention a commit only if created. External changes require authorization; use
[`failure-and-reporting.md`](failure-and-reporting.md) for blocked handoffs.
