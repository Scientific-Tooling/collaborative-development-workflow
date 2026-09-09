# Workflow and Acceptance (V2)

Read this reference for the full plan → implement → review → accept → optional
commit sequence. Statuses and record fields come from
[`contracts-v2.json`](contracts-v2.json); runtime-only strict mechanics live in
`review-runtime.md` and `review-recovery.md`.

## 1. Analyze, establish scope, and preflight

Before editing or delegating:

1. Restate the requested outcome and observable acceptance criteria.
2. Read applicable `AGENTS.md` files, project docs, likely paths, direct callers and
   consumers, mapped tests/configuration, and prescribed validation commands.
3. Record Git status, branch, HEAD, index state, and the baseline boundary. Preserve
   all pre-existing staged, unstaged, and untracked work.
4. Define a canonical `ImpactScopeV2`, explicit exclusions, risks, dependencies, and
   focused `FocusedCheckV2` records. Do not silently truncate a scope.
5. Select `portable` by default, or `strict` only when the user explicitly requests
   it. Validate a `capability-preflight-v2` record before any mutation or spawn.

Portable is ready only when identifiable read-only subagents, terminal result
delivery, and shared snapshot access are observable. Strict additionally needs the
authoritative runtime capabilities in `review-runtime.md`. A missing strict
capability stops before mutation; it is never a portable fallback.

If required files overlap pre-existing edits, report the overlap before changing
them. If the baseline index has staged changes, record their metadata now; this will
later make the optional commit gate refuse index surgery.

## 2. Plan

Use main-agent planning for small, clear, tightly coupled work. For broad,
ambiguous, or high-risk work, use one bounded read-only planner or critic. The
accepted plan records:

- exact or likely paths and the V2 impact scope;
- dependencies, milestones, write ownership, mode, isolation, and integration order;
- focused checks, validation owner, exclusions, risks, and material decisions; and
- one prescribed full-validation command deferred to the final acceptance point.

A delegated planner returns a ContractV2 role result. Treat it as evidence, not as
authority to expand scope. Pause at the planning gate for an unanswered user
decision that changes safety, behavior, scope, cost, or authorization.

## 3. Implement bounded milestones

The main agent is the sole writer in portable mode. A strict implementer may write
only after runtime-atomic binding and only within its declared scope. Read-only
delegates may plan, research, verify, or review a supplied immutable artifact.

Each writer preserves unrelated changes, runs focused checks, stops at a coherent
checkpoint, and returns a V2 role result. It must not commit, push, deploy, install,
reset, clean, delete, or perform unrelated work. The parent verifies actual changed
paths, content identity, and checks before integrating any checkpoint.

For a large change, integrate one coherent milestone at a time and create a new
integrated identity before review. Do not let a child worktree or a moving workspace
become the review input.

## 4. Freeze and review

After implementation and focused checks:

1. perform a cheap pre-freeze self-check;
2. freeze exactly the V2 impact paths with `snapshot_tool.py` outside the repository;
3. run `snapshot_tool.py verify` and record its content/manifest identities;
4. pause all writers and main-agent edits; and
5. start one fresh, read-only reviewer context for the exact artifact, using
   `fork_context=false` when the runtime exposes that option.

Use one integrated reviewer for a small scope. Use a fixed review set only when the
scope is broad enough for disjoint obligation lanes, and choose the mapping before
freeze. A reviewer sees the request, accepted plan, baseline, exact scope,
exclusions, focused checks, artifact identity, and named risk-bearing consumers—no
unbounded conversation or live workspace.

In portable mode, the parent verifies the artifact and workspace identities before
and after review, validates the reviewer result, and checks complete scope coverage.
This is strong portable evidence, not a claim of runtime CAS or stop guarantees. In
strict mode, the runtime must also attest the proofs and events defined in the strict
references.

## 5. Findings and bounded rounds

For a validated `FINDINGS` result:

1. retain the old report and snapshot identity;
2. apply only confirmed, in-scope fixes;
3. rerun affected focused checks;
4. recanonicalize scope if the fix changes it;
5. create a new snapshot; and
6. rerun the review against the new snapshot with a fresh reviewer context. For a
   single integrated review, the new review may be narrowed to the affected
   obligations; for a fixed review set, rerun every lane and rebuild the aggregate
   coverage proof. No old lane result or `CLEAN` result transfers to the new identity.

The old `CLEAN` never transfers across a content identity. Ordinary
findings-driven revision rounds are not blocked-review restarts and do not need new
user authorization. Permit two fix/review rounds; if the third review still has
actionable findings, pause and request authorization before continuing.

`REVIEW_UNAVAILABLE` means no usable independent result arrived. `REVIEW_BLOCKED`
means a result exists but its identity, scope, artifact, or required proof is not
valid. In portable mode either condition produces `NOT_ACCEPTED` and a handoff, not
a commit. Strict replacement is a separate, bounded recovery operation and is
allowed only after the authoritative stop confirmation specified in
`review-recovery.md`.

## 6. Independent acceptance

At the final acceptance point the main agent:

1. inspects the complete task diff, status, staged boundary, and generated/secrets/
   debug changes while keeping substantive scope review bounded;
2. verifies each acceptance criterion, exclusion, snapshot identity, and coverage
   obligation;
3. runs all relevant focused checks;
4. runs the repository-prescribed full validation once, last; and
5. runs `snapshot_tool.py compare` to recheck the final workspace against the exact
   reviewed artifact.

Produce `ACCEPTED_PORTABLE` only when the independent portable review is usable,
scope/artifact/workspace identities match, required checks pass, and full validation
passes. Produce `ACCEPTED_STRICT` only when the same conditions plus all strict
runtime proofs pass. Otherwise produce `NOT_ACCEPTED` with the precise V2 reason.

A failed validation, missing proof, user decision, quarantine, blocked lane,
identity mismatch, or changed scope invalidates acceptance and requires a targeted
fix plus a new review identity.

## 7. Optional local commit

A local commit is an explicit user-authorized operation, not a default workflow
outcome. It is permitted only after acceptance and only when the baseline index was
clean and no pre-existing edit overlaps an explicit task path.

When those conditions hold:

1. Reconcile the baseline and current Git path states. Treat a pre-existing staged,
   unstaged, or untracked entry as overlapping when its path equals, is a descendant
   of, or is an ancestor of an explicit task path; a task directory therefore
   includes all of its descendants.
2. If any such overlap exists, do not stage or commit. Report the exact paths and
   hand off the accepted result or ask the user to separate and explicitly
   authorize the overlap. Content identity proves bytes, not authorship.
3. stage only the explicit task paths;
4. inspect staged paths and the staged diff;
5. verify staged content matches the accepted reviewed identity;
6. create one focused local commit; and
7. verify the commit summary and worktree.

If any pre-existing staged change exists, or any pre-existing unstaged/untracked
change overlaps a task path, decline the commit and hand off the
accepted-but-uncommitted result. This portable policy is deliberately conservative:
it avoids accidentally including unrelated work and avoids temporary-index surgery.
Push, publication, deployment, installation, and other external mutations require
separate explicit authorization.
