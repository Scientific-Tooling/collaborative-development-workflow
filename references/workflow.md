# Workflow and Acceptance

Detailed planning, implementation, snapshot, review, acceptance, and commit procedures. Read this when the task uses the full collaborative workflow.

## Workflow

### 1. Analyze and establish scope

Before spawning an agent:

1. Restate the requested outcome and concrete acceptance criteria.
2. Inspect the applicable `AGENTS.md` instructions, the likely changed files, directly related callers/consumers, directly mapped tests/configuration, and documented validation commands. Do not inventory the whole repository.
3. Run read-only Git checks such as `git status --short`, identify the current branch and `HEAD`, and record changed/staged/untracked paths as the baseline.
4. Define the impact set and explicit exclusions. Identify only the dependencies, risks, ambiguous requirements, and smallest safe implementation boundary that are evidenced by that set. For new work, use targeted search to locate direct callers rather than tracing the entire repository.
5. Decide the narrowest focused checks that will prove completion, and reserve repository-wide validation for the final acceptance step.

Report this analysis to the user. Ask a question only when an ambiguity materially changes scope, safety, or the acceptance criteria. Otherwise proceed.

If the task has overlapping pre-existing edits in the files that must change, flag the overlap before implementation and preserve the user's changes. Do not silently overwrite or stage them.

### 2. Plan adaptively and resolve material decisions

Start from the main agent's repository analysis and select the least complex planning path that provides enough confidence:

- **Main-agent planning:** use for small, clear, or tightly coupled work. Produce the concise plan directly and do not delegate merely to satisfy a role boundary.
- **Delegated planning or critique:** use for large, ambiguous, high-risk, or cross-cutting work, or when an independent perspective is likely to change the plan. Give the planner a bounded read-only assignment and one absolute `planner_wait_budget` wait budget. It may produce the plan or critique a provisional main-agent plan. When its result controls the next planning decision, invoke it in the foreground and wait for its terminal result; otherwise continue only safe, non-overlapping analysis without polling it.

Every accepted plan, regardless of its author, should include:

- a concise implementation strategy;
- a `TaskSpec` for every delegated assignment, including role, dependencies, read/write scope, base snapshot and baseline content identity, focused checks, execution mode, isolation, budget, and resumability;
- the impact scope and explicit exclusions for each milestone;
- cohesive, dependency-aware milestones, each independently understandable and reviewable, with its write scope, prerequisites, acceptance checks, and risk level;
- an execution-mode recommendation, including which milestones, if any, are genuinely independent and safe to isolate;
- exact or likely files/modules to change;
- focused tests and validation commands, plus one repository-wide final validation command when the repository prescribes one;
- compatibility, regression, and rollback risks;
- questions that must be resolved before implementation;
- every material decision point, with viable options, trade-offs, a recommendation, and the consequence of choosing each option.

Milestones should represent meaningful behavior or integration boundaries, not arbitrary per-file fragments. Every delegated milestone must be small enough to implement, validate, and return as a checkpoint in one bounded agent invocation, without a rigid wall-clock threshold. Split oversized work at coherent intermediate boundaries so each milestone leaves an understandable, testable diff before dependent work starts.

A delegated planner must remain read-only and end its bounded assignment with:

```text
STATUS: PLAN_READY | NEEDS_INPUT | PARTIAL | NEEDS_USER_DECISION | BLOCKED | FAILED | CANCELLED
PLANNING_ROLE: PLAN | CRITIQUE
PLAN_OR_CRITIQUE: <concise result, or partial result>
MATERIAL_DECISIONS: <options, trade-offs, recommendation, or none>
BLOCKER: <blocker and resume condition, or none>
```

Treat delegated planning as input, not authority to expand scope. The main agent reviews every plan against the user's request and repository instructions. Neither planning path may silently resolve a material product or strategy choice on the user's behalf.

When either planning path returns or identifies a material decision and `planning_decision_gate` is `on-demand`:

1. Present the decision to the user in plain language, including the options, recommendation, main trade-offs, and the default consequence.
2. Pause the workflow and wait for the user's choice. Do not start implementation while a material decision is unanswered.
3. Record the user's choice in the accepted plan; send it back to a delegated planner only when another planning pass adds value.
4. Show the user the resulting concise plan summary and continue only after the decision is clear.

Use local conventions and the planning recommendation without pausing for low-impact implementation details that do not materially change the outcome. If the user says to use the recommendation, treat that as an explicit approval. If the user asks for autonomous execution, disable this gate for the current task.

The current subagent interface may not expose a literal `plan mode` parameter. Enforce plan mode through the prompt: read-only inspection and a plan-only response.

### 3. Select an implementation mode and execute milestones

After accepting the plan, select the least complex mode that fits the work:

- **Main-agent implementation:** use for small or tightly coupled changes. The main agent edits directly and still produces a checkpoint at each meaningful boundary; a small task may have one milestone. Do not spawn separate agents for per-file work or for a verifier when the main agent can perform the same focused check cheaply. A fallback or takeover is allowed only through the documented recovery path after no child writer was claimed or the previous writer’s stop/cancellation is confirmed, with a recovery `TaskSpec`, an atomic lock transfer or distinct workspace, and the same independently enforced scope, sandbox, approval, and network boundaries.
- **Checkpointed delegated implementation:** use for large or long-running work. Invoke an implementer for exactly one accepted milestone with one absolute `implementer_wait_budget` wait budget, wait in the foreground for its terminal checkpoint, inspect the checkpoint once it returns, then invoke the next milestone. Reuse the same implementer when available so context and responsibility remain stable. Milestones are orchestration-level call boundaries; internal progress text that the orchestrator cannot receive is not a checkpoint.
- **Isolated parallel implementation:** use only when slices are genuinely independent, have disjoint write scopes, and can be implemented and tested in isolated worktrees or equivalent workspaces. Record ownership, common baseline, integration order, and integration checks before starting. Shared contracts, migrations, generated artifacts, or overlapping callers usually make slices dependent; serialize them unless isolation is demonstrably safe.

For every mode, pass the accepted `TaskSpec` and context manifest to the writer. Require the writer to make the smallest complete change, preserve unrelated and pre-existing work, follow local conventions, add focused tests when appropriate, and avoid commits, pushes, resets, cleanup, and unrelated refactors. The writer may inspect only the write scope and declared impact scope; it must not perform an exploratory repository audit. Do not assign repository-wide test suites or heavyweight validation to the writer; the main agent runs those once during final acceptance.

When calling `multi_agent_v1__spawn_agent`, pass `model: "gpt-5.6-luna"` and `reasoning_effort: "max"` for the default policy. For complex, cross-cutting, high-risk, or long-running work, pass `model: "gpt-5.6-terra"` with `reasoning_effort: "max"`. The user has explicitly authorized this model policy; omit `model` only when intentionally inheriting the parent model. Do not set `service_tier` unless the user requests it.

One delegated invocation must implement no more than one milestone, and that milestone must fit the bounded invocation. If it proves oversized, stop at the last coherent state and return `PARTIAL` when usable work exists, or `BLOCKED` when no coherent work can be retained, with a proposed coherent split rather than continuing open-endedly. At the milestone boundary, the writer stops modifying files and returns this checkpoint:

Prepend the complete common result envelope above, including all invocation, baseline, result-identity, and `ATTENTION_REQUIRED` fields; the block below contains only milestone-specific fields.

```text
STATUS: CHECKPOINT_READY | NEEDS_INPUT | PARTIAL | BLOCKED | NEEDS_USER_DECISION | FAILED | CANCELLED
MILESTONE: <identifier and objective>
COMPLETED_SCOPE: <completed behavior, or none>
CHANGED_PATHS: <explicit paths, or none>
CHECKS: <commands and results, including failures or not run>
REMAINING_RISKS: <known uncertainty, regression risk, or none>
BLOCKER_OR_DECISION: <blocker and resume condition, or decision/options/recommendation, or none>
NEXT_MILESTONE: <proposed next milestone, or none>
```

Use `CHECKPOINT_READY` only when the milestone is coherent and reviewable and every required focused check passed. If a required check failed or was not run, use `PARTIAL`, `BLOCKED`, or `NEEDS_USER_DECISION` as appropriate; the main agent must not mark that writer task accepted until the check is rerun successfully after the intended integration. Use `BLOCKED` when safe progress cannot continue, and `NEEDS_USER_DECISION` when a material choice must return to the planning gate. The writer must pause after any status and must not begin the next milestone. The main agent verifies the reported paths and checks against the actual artifacts before accepting the checkpoint, then stops the milestone rather than using remaining budget for unrelated exploration.

For isolated parallel work, each writer returns its own checkpoint. The main agent remains the integration owner, combines slices in the recorded order, resolves integration issues without hiding them from review, and creates a new integrated checkpoint before final review.

### 4. Review frozen checkpoints and the final diff

#### Materialize the review snapshot

Before spawning a reviewer, materialize the exact impact set into a content-addressed, read-only snapshot. For a Git-backed workspace, use a detached worktree or archive rooted at the recorded tree/patch identity; for a non-Git file such as a global skill, copy the exact bytes into a task-specific snapshot artifact, preserve each source file's relative directory layout (including `references/`), set every snapshot file to `0444` and every snapshot directory to `0555`, and recheck those modes before spawn. The snapshot manifest must list every reviewed path, its hash, the base identity, and the resulting content identity. `SNAPSHOT_ID` is the stable logical/content identity and `ARTIFACT_PATH` is a separate location; never put the artifact path in the ID field. A flattened copy that breaks a relative reference, a writable directory, or an ID/path mismatch is not the declared artifact.

Pass `SNAPSHOT_ID`, `artifact_path`/URI, and `CONTENT_IDENTITY` to the reviewer. The reviewer reads the artifact, never a live mutable path. A hash taken before and after reading a live file is not an immutable snapshot. If the platform cannot materialize or verify a read-only artifact, use `REVIEW_BLOCKED`; do not silently downgrade to a live-path review. After review, re-hash the artifact and the authoritative workspace; any mismatch invalidates `CLEAN` and requires a new snapshot.

At every `CHECKPOINT_READY`, the main agent performs only cheap scope-integrity checks: inspect the changed paths and actual diff, check for malformed patches, and confirm the reported focused checks. Rerun a focused check only when it was omitted or failed, the relevant files changed after it ran, its result is untrusted, or a concrete risk needs reproduction. Do not inspect or test unrelated code at a checkpoint. These checks do not replace independent review.

Before delegating a final review, finish the implementation and expected validation for that snapshot. Perform a pre-freeze self-check of likely edge cases and acceptance criteria so the reviewer is not started while the main agent still expects to make ordinary fixes. For a small, tightly coupled task, use one integrated final review unless a checkpoint carries a distinct risk that justifies an earlier review. Invoke the reviewer in the foreground and wait for its terminal result; do not poll a moving review or inspect the frozen input repeatedly while it runs.

Freeze the review input before delegating review. Keep every writer paused and prohibit changes to the authoritative workspace for the duration of review, even when the reviewer uses the immutable artifact. Record the base snapshot, base content identity, result/frozen content identity, `SNAPSHOT_ID`, artifact identity, and artifact path/URI for the exact input. Verify both artifact and authoritative-workspace identities after review and before accepting `CLEAN`. If either contents changed, invalidate `CLEAN`, refreeze and identify the new input, and re-review it. Never ask a reviewer to inspect a diff that is changing in the same mutable workspace.

Request independent checkpoint review when the milestone carries meaningful correctness, compatibility, security, privacy, data, concurrency, performance, accessibility, or cross-cutting integration risk. Low-risk checkpoints may proceed after the main-agent checks, but the complete integrated diff must always receive an independent final review before acceptance.

Parallel review is safe only when the reviewer receives an immutable snapshot or isolated worktree and the next milestone is genuinely independent of the reviewed milestone with a disjoint write scope. Otherwise keep the writer paused until review findings are resolved. Findings against an older snapshot must be reconciled against the current integrated state and re-reviewed when intervening changes affect their evidence.

Give the reviewer the original request, acceptance criteria, accepted plan, baseline boundary, base snapshot, base content identity, result/frozen content identity, immutable `SNAPSHOT_ID` and artifact path/URI, impact scope and exclusions, and frozen diff. Instruct it to remain read-only and review the changed paths and named direct callers/consumers from the artifact:

- correctness and completeness against the request;
- deviations from the accepted plan or repository rules;
- regressions, edge cases, error handling, and compatibility;
- security, privacy, performance, and accessibility risks when relevant;
- test adequacy and reproducibility;
- accidental changes outside scope.

Keep the reviewer assignment bounded and independent. Prefer `fork_context: false` unless conversation history is required, enumerate the exact changed paths and risk-bearing callers, and ask only for focused checks needed to substantiate a concrete finding. Do not ask it to audit unrelated modules or run/repeat a repository-wide test/build suite. The reviewer should return the required terminal status for the supplied snapshot, even when a check was intentionally not run.

If one integrated reviewer is too broad for a reliable bounded invocation, use a
`review_set` instead of enlarging the prompt. The parent freezes one snapshot, maps
every acceptance criterion and risk-bearing path to exactly one primary lane, and
starts 3 (or another fixed, documented count of) read-only lane reviewers in
parallel. Split by acceptance obligations rather than arbitrary files; typical
lanes are identity/scope/artifact access, state/CAS/timing/recovery, and result/
acceptance/regression checks. Each lane receives only its immutable artifact, its
assigned obligations, direct callers needed for those obligations, exclusions, and
the same short terminal schema. A lane's `CLEAN` covers only its assigned slice and
must carry a parent/runtime-attested `LaneCoverageProofV1` for that slice; the parent
must not treat any single lane as the whole review or require the aggregate proof
before validating the lane. The parent creates one typed aggregate `CoverageProofV1`
only after the lane results are independently validated; it maps every declared impact-scope component, direct
caller/consumer, exclusion, and focused check to a completed lane with a validated
artifact proof. The parent first canonicalizes `ImpactScopeV1`, `FocusedCheckV1`, and
the fixed `LaneAssignmentV1` set; it records `impact_scope_digest`, each
`lane_scope_digest`, and the complete `mapping_digest`. Every lane result must already
have its validated `LaneCoverageProofV1` and artifact-access proof. The aggregate
coverage proof repeats
those digests, the exact snapshot/content identity, one validated lane result per
assignment, and positive accounting for every covered and excluded item; the parent
recomputes `coverage_proof_digest` before aggregate acceptance. Only this gap-free
aggregate proof may authorize final `CLEAN`. Review-set metadata (`set_id`, `lane_id`,
lane assignment, mapping/coverage digests, and aggregate status) lives in the
parent/session record and is not an extra `FullTaskRow` field. The parent/session
record also owns one atomic `review_set_replacement_slot_compare_and_set` for the
frozen snapshot, keyed by `REVIEW_SET_ID`, predecessor stop-event identity,
`impact_scope_digest`, `mapping_digest`, the fixed replacement-window deadline,
and remaining snapshot budget. A lane may be replaced only after this set-level CAS
returns the authoritative `CLAIMED` outcome; the per-lane `replacement_count` is not
a set-level quota and cannot authorize a second missing lane. A lost CAS must consume
the transaction's authoritative set record exactly once. If the slot is already
consumed, every other missing lane remains `REVIEW_BLOCKED` even when its individual
task row still appears eligible.

Wait for the lane set as one logical blocking wait, collecting terminal results
without status polling or liveness probes. A lane that cannot finish its bounded
slice returns `REVIEW_BLOCKED`; an empty result or timeout is never a pass. After
confirmed cancellation, at most one missing lane may win that atomic
parent/session replacement-slot CAS within the one permitted replacement window,
preserving the same snapshot identity and remaining budget. Every other missing
lane makes the aggregate `REVIEW_BLOCKED`; do not fill its coverage proof with the
main agent's self-review or another lane's overlapping claim. When a lane reports
`FINDINGS`, fix the finding, refreeze, and rerun the complete fixed lane set because
every lane result is bound to the old content identity.

For a missing lane, the parent first calls
`review_set_replacement_slot_compare_and_set` against the parent/session record with
the exact `REVIEW_SET_ID`, lane ID, canonical frozen snapshot/content identity,
`impact_scope_digest`, `lane_scope_digest`, `mapping_digest`, predecessor runtime
stop-event ID, fixed replacement-window deadline, remaining budget, owner, and the
consumed-slot sentinel. The complete metadata record has an explicit expected/new
projection and a typed result: `COMMITTED` with the winning lane claim or `CAS_LOST`
with the authoritative set record. The caller consumes that record exactly once.
Only the `COMMITTED` lane may spawn one replacement under the ordinary invocation-row
rules; all other missing lanes are terminal aggregate `REVIEW_BLOCKED`, even if their
per-lane task rows have a free `replacement_count`.

If the review set is `REVIEW_BLOCKED` because its permitted lane/reviewer invocations
produced no usable report, and the user or parent policy supplies a new review budget,
the parent may start a fresh review round through an independent execution channel.
First close the old set as blocked after every affected invocation has a confirmed
runtime stop or an explicit permanently unaddressable quarantine; never keep its
locks or replacement slot implicitly open. Then create a new `RUN_ID`,
`REVIEW_SET_ID`, snapshot artifact/`SNAPSHOT_ID` (even when its content identity is
unchanged), invocation identities, and parent/session review record. Prefer a
different runtime provider/model when available, require `fork_context=false`, and
give the new reviewer(s) only the new immutable snapshot, TaskSpec, scope, and
acceptance criteria. Do not pass the old agents' silence, partial messages, findings,
coverage proof, or CLEAN claim as evidence. The new round must independently satisfy
the complete reviewer schema, runtime artifact proof, and typed gap-free coverage
proof; only its own validated `CLEAN` may authorize acceptance. This is a new review
round with a separately reserved budget, not an extension of the old snapshot budget
or a second hidden replacement. If no independent channel or new budget is available,
retain `REVIEW_BLOCKED` and stop before acceptance/commit.

Require findings in severity order with file/line references, evidence, impact, and a concrete fix suggestion. Distinguish actionable defects from optional improvements. The sole canonical child-owned reviewer schema is the complete field set in the `### Reviewer` prompt below; do not use a shortened variant. It includes `COMPLETED_SCOPE`, `CHANGED_PATHS`, `CHECKS`, `RISKS`, `BLOCKER_OR_INPUT`, `ATTENTION_REQUIRED`, `NEXT_ACTION`, `REVIEWED_PATHS`, `FINDINGS`, and `BLOCKER`. The runtime or parent adds and verifies the provenance, timing, runtime-attested `ARTIFACT_ACCESS_PROOF`, and parent/runtime-attested `REVIEW_COVERAGE_PROOF` fields.

Use `CLEAN` only when no actionable issue remains in that exact identified snapshot and the parent/runtime has attached a positive coverage proof for the declared impact scope, direct callers/consumers, exclusions, and required focused checks. Use `FINDINGS` when fixes are required and `REVIEW_BLOCKED` when the review cannot be completed reliably. A clean review does not override failing tests or a failed acceptance check.

Reviewer status and payload are semantically closed: `CLEAN` must have `CHANGED_PATHS: none`, nonempty `COMPLETED_SCOPE` and `REVIEWED_PATHS`, a parent/runtime coverage proof that positively accounts for every declared scope component and focused check, and no actionable `FINDINGS`, blocker, material decision, attention request, or unresolved actionable `RISKS`/`NEXT_ACTION`; `FINDINGS` must also have `CHANGED_PATHS: none`, no blocker, attention request, or material decision, and must contain at least one actionable finding with a location, evidence, impact, and concrete fix. A contradictory or under-specified status is quarantined even when its field syntax is otherwise valid.

The mandatory final review covers the whole integrated diff, including milestones already reviewed, but its code inspection remains bounded by the impact scope. In integrated mode this is one reviewer result; in review-set mode it is the aggregate of every required lane on the same frozen snapshot. Do not begin independent acceptance until this frozen final snapshot is `CLEAN` and focused checks pass; repository-wide validation is intentionally deferred to section 6.

### 5. Loop fixes and reviews

Keep the writer and reviewer available for follow-up when possible. The writer for a mutable workspace remains paused while that workspace is under review; only the isolated parallel conditions in section 4 permit other implementation progress. Do not create a new agent for every round unless an agent becomes unavailable or an independent perspective is genuinely needed.

For each review round:

1. Verify that the reviewed snapshot still has its recorded content identity. If it changed, invalidate `CLEAN`, retain only findings that still apply, and refreeze before review; otherwise record findings against that exact snapshot. Reconcile stale findings before changing a newer integrated state.
2. Do not edit while the reviewer is running or cancellation is merely requested. After the reviewer reaches a terminal state, release the review lock only after a validated `CLEAN`/`FINDINGS` result is committed and its artifact is captured, or after a confirmed stop/cancellation and capture or explicit rejection of the attempt artifact. Keep the review lock for `FAILED`, `CANCELLED`, `QUARANTINED`, `BLOCKED`, `REVIEW_BLOCKED`, unconfirmed, or budget-overrun attempts; no release, replacement, or takeover is allowed until the applicable stop and artifact conditions are satisfied. Then give the same writer a bounded fix milestone containing only confirmed, in-scope findings and relevant validation failures. For parallel work, the integration owner selects a non-overlapping workspace and safe integration point.
3. Require the writer to rerun only the focused checks affected by the fix, emit a new checkpoint, and pause again.
4. Inspect the actual updated artifacts, run cheap checks such as `git diff --check` when applicable, and freeze a refreshed snapshot. Keep repository-wide validation deferred.
5. Ask the integrated reviewer, or every lane in the fixed review set, to review the refreshed snapshot, explicitly identifying resolved findings and unchanged scope. Stop only when the integrated reviewer returns `STATUS: CLEAN`, or every required lane returns lane-scoped `STATUS: CLEAN` and the parent commits the aggregate coverage proof, with focused checks passing for that snapshot.

Do not let agents negotiate silently. If a finding requires a product decision, changes scope, conflicts with user-owned edits, or cannot be reproduced, pause and ask the user. If the round limit is reached or the same finding repeats without a meaningful change, report the evidence instead of looping indefinitely.

### 6. Perform independent acceptance

After the mandatory final snapshot is clean, the main agent must independently:

1. Inspect the complete final task diff and changed-path inventory. In Git repositories use `git diff`, `git diff --stat`, `git diff --check`, and `git status --short`; keep substantive inspection bounded to the impact scope and do not audit unrelated files.
2. Verify that the final change satisfies every acceptance criterion and does not remove or overwrite baseline user changes.
3. Run the narrowest relevant tests and build/lint/type checks for the impact scope first. Do not repeat checks already passed unless the relevant files changed or a concrete failure requires it.
4. After focused checks pass, run the repository's complete prescribed validation as the main agent, if one is prescribed, once and as the last validation action. Do not delegate it, run it at checkpoints, or repeat it merely because a review was clean. If it fails, diagnose with the failing target or another focused check, complete all fixes, and rerun the full validation only when acceptance requires a final post-fix result. A full-suite pass is validation evidence, not a substitute for the mandatory independent `CLEAN` review.
5. Check generated files, secrets, debug output, accidental dependency changes, and scope creep, limiting source inspection to the impact scope unless the change itself provides evidence of a wider issue.
6. After all validation, recompute the final content identity and compare it with the reviewed identity. If it differs, invalidate `CLEAN`, inspect the changes, refreeze, and repeat the mandatory final review before proceeding.
7. Resolve any remaining failure through a bounded fix milestone and refreshed final-snapshot review before committing. Keep full validation deferred until that fix loop is complete.

If acceptance fails, do not claim completion merely because review was clean. If the reviewer has no usable terminal report after the permitted recovery path, record `REVIEW_BLOCKED`, report the exact snapshot identity and wait-budget/cancellation evidence, and do not commit. Optional full-suite execution may be reported as diagnostic evidence, but it does not change that acceptance state.

### 7. Create the local commit

When acceptance passes, the workspace is a Git repository, and `commit` is enabled:

1. Stage only explicit task paths, taking care not to include baseline staged or untracked work.
2. Inspect the staged diff and staged file list.
3. Verify that the staged task diff corresponds exactly to the accepted content identity and contains no baseline work. If it does not, do not commit; correct the scope, refreeze, and re-review.
4. Create one focused local commit with a concise imperative message.
5. Verify the commit summary and remaining worktree state.

If task changes overlap inseparably with pre-existing edits, do not stage or commit blindly. Explain the exact overlap and ask for direction. Never push unless explicitly requested.
