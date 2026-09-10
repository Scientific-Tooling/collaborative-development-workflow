# Workflow and Acceptance (V2)

Read this reference for the full plan → implement → review → accept → optional
commit sequence. Statuses and record fields come from
[`contracts-v2.json`](contracts-v2.json); portable artifact mechanics live in
`review-runtime.md`, while strict-only runtime and recovery mechanics live in
`review-runtime-strict.md` and `review-recovery-strict.md`.

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
authoritative runtime capabilities in `review-runtime-strict.md`. A missing strict
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
- one prescribed full-validation command and its main-agent owner.

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

## 4. Validate, freeze, and review

After implementation:

1. run every required focused check and the repository-prescribed full validation;
2. inspect the resulting diff/status and remove only task-created disposable output;
3. freeze exactly `ImpactScopeV2.review_paths` with `snapshot_tool.py` outside the repository;
4. record the emitted content and manifest identities, then run `verify` with both
   values as expected-identity arguments;
5. pause all writers and main-agent edits; and
6. start one fresh, read-only reviewer context for the exact artifact, using
   `fork_context=false` when the runtime exposes that option.

ContractV2 acceptance currently supports one integrated reviewer. Review-lane
aggregation is not a public record shape and must not be used to claim acceptance.
A reviewer sees the request, accepted plan, baseline, exact scope,
exclusions, focused checks, artifact identity, and named risk-bearing consumers—no
unbounded conversation or live workspace.

In portable mode, the parent verifies the artifact and workspace identities before
and after review, validates the reviewer result, and checks complete scope coverage.
This is strong portable evidence, not a claim of runtime CAS or stop guarantees. In
strict mode, the runtime must also attest the proofs and events defined in the strict
references.

## 5. Findings and bounded rounds

For a validated `FINDINGS` result, follow [`review-recovery.md`](review-recovery.md),
which owns the portable revision procedure. Every content-changing fix requires a
new snapshot and review identity and complete integrated review coverage; no old
coverage or `CLEAN` result transfers. Ordinary
findings-driven rounds do not need new user authorization; the bounded round limit
and user-decision gates are defined in that reference.

`REVIEW_UNAVAILABLE` means no usable independent result arrived. `REVIEW_BLOCKED`
means a result exists but its identity, scope, artifact, or required proof is not
valid. In portable mode either condition produces `NOT_ACCEPTED` and a handoff, not
a commit. Strict replacement is a separate, bounded recovery operation and is
allowed only after the authoritative stop confirmation specified in
[`review-recovery-strict.md`](review-recovery-strict.md).

## 6. Independent acceptance

After the reviewer returns, the main agent performs only read-only acceptance work:

1. inspects the complete task diff, status, staged boundary, and generated/secrets/
   debug changes while keeping substantive scope review bounded;
2. validates the reviewer result, structured proof records, required-check coverage,
   and previously recorded full-validation result;
3. runs `snapshot_tool.py compare` with both expected identities to recheck the
   final workspace against the exact reviewed artifact; and
4. validates the complete `acceptance-evidence-v2` bundle.

Do not run a mutating formatter, generator, or test after the snapshot is frozen.
If any post-freeze operation changes scoped content or Git identity, invalidate the
review, rerun validation, and freeze a replacement snapshot.

Produce `ACCEPTED_PORTABLE` only when the independent portable review is usable,
scope/artifact/workspace identities match, required checks pass, and full validation
passes, and every final acceptance criterion is `met`. Required focused checks must
collectively cover all changed paths, not merely reuse a passing check ID. The
bundled validator cannot produce `ACCEPTED_STRICT`; an external
authoritative runtime adapter must validate the same evidence plus all strict
runtime proofs. Otherwise produce `NOT_ACCEPTED` with the precise V2 reason.

A failed validation, missing proof, user decision, quarantine, incomplete coverage,
identity mismatch, or changed scope invalidates acceptance and requires a targeted
fix plus a new review identity.

## 7. Optional local commit

A local commit is an explicit user-authorized operation, not a default workflow
outcome. It is permitted only after acceptance and only when the baseline index was
clean and no pre-existing edit overlaps an explicit task path.

Record intent separately from lifecycle: `NOT_REQUESTED`, `PENDING`, `CREATED`, or
`BLOCKED`. A non-accepted workflow may preserve explicit user intent as `BLOCKED`
with a reason; it must not rewrite that intent to false.

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
