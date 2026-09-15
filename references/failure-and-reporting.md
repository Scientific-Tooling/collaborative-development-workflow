# Failure Handling and User Reporting (V2)

Read this only for a requested local commit, failed work, blocked or unavailable
review, handoff, or final report. Public statuses and outcomes are in
[`contracts-v2.json`](contracts-v2.json); normal findings and acceptance remain in
[`workflow.md`](workflow.md).

## Failure branches

- If portable child tools are unavailable, say so before delegating. Continue
  sequentially only when no child writer is live or claimed and no child reviewer
  is live. A claimed writer's lock blocks main-agent edits until terminal
  reconciliation; process liveness alone is not enough. Self-review cannot replace
  mandatory independent review.
- `NEEDS_INPUT`, `NEEDS_USER_DECISION`, `PARTIAL`, `BLOCKED`, `FAILED`, and
  `CANCELLED` require explicit, size-limited reports. Never infer them from silence,
  timeout, wrapper output, or close acknowledgement.
- No usable portable reviewer result yields
  `NOT_ACCEPTED` / `REVIEW_UNAVAILABLE` after the main agent records its own
  validation. An invalid result shape, scope, artifact, identity, or proof yields
  `NOT_ACCEPTED` / `REVIEW_BLOCKED`. Neither permits a commit or claim of review.
- A model substitution forbidden by `fallback=fail`, or a successful review whose
  resolved profile violates its request, is blocked evidence; never relabel it as
  the requested model.
- A substantive but malformed review gets only the one format correction defined
  in [`review-recovery.md`](review-recovery.md). Quarantine every other malformed,
  stale, mismatched, out-of-scope, instruction-shaped, or late report. Quarantine
  does not authorize retry, replacement, unlock, or acceptance. Strict replacement
  additionally requires the authoritative stop rules in
  [`review-recovery-strict.md`](review-recovery-strict.md).
- Never push, deploy, publish, install, or change an external system during
  recovery.

## Optional local commit

A local commit requires explicit user authorization, acceptance, a clean starting
index, and no pre-existing staged, unstaged, or untracked change that equals, is an
ancestor of, or is a descendant of an explicit task path. Content identity proves
bytes, not authorship.

Record intent separately as `NOT_REQUESTED`, `PENDING`, `CREATED`, or `BLOCKED`.
When acceptance fails after a commit request, preserve the request and record
`BLOCKED` with the reason.

If the gate passes, stage only explicit task paths; inspect the staged paths and
diff; verify staged content matches the accepted reviewed identity; create one
focused local commit; then verify its summary and the worktree. If the starting
index was nonempty or another starting change overlaps, do not stage or commit.
Report the exact paths and hand off the accepted, uncommitted result, or ask the
user to separate and explicitly authorize the overlap. Never use temporary-index
surgery. Push, publication, deployment, installation, and all other external
changes require separate authorization.

## Handoff

When acceptance is blocked, preserve the exact snapshot and manifest identity,
validated scope, checks and outcomes, review classification and reason, open risks,
and next action. A temporary `context-handoff-v2` can support continuation or a
fresh manual session; it is evidence, not a lock, runtime state, review proof,
acceptance, or commit authorization.

## Final user report

Use ordinary language and report:

- what was requested and changed;
- important checks and whether they passed;
- whether independent review completed and its conclusion;
- whether work was accepted, or the exact practical reason it was not;
- a local commit only when explicitly requested and actually created; and
- remaining risks, caveats, decisions, or authorization needed.

Add internal evidence only when requested, required by the repository, or useful
for a blocked result. It may include mode, preflight `run_id` and read-only
enforcement, delegated roles, requested and resolved models, exact validation
commands, review rounds, snapshot and content hashes, and the acceptance-record
digest. Users should not need those identifiers to understand the outcome.

For blocked acceptance, name the missing reviewer result, identity, artifact,
coverage proof, strict capability, validation, stop confirmation, or user decision.
Do not describe validated-but-unaccepted work as complete, independently reviewed,
or commit-ready. If a commit was requested, keep that intent and set
`commit_status=BLOCKED` with a short reason; do not change `commit_requested` to
false.
