# Coordination Protocol (V2)

Read this reference only for two or more assignments, background work, isolated
worktrees, or work that may outlive the current turn. The common lifecycle belongs
to [`workflow.md`](workflow.md); record fields and public statuses belong to
[`contracts-v2.json`](contracts-v2.json).

## Coordination invariants

- The main agent is the authority for scope, integration, repository-wide
  validation, acceptance, and releasing review locks.
- One task has one owner and one declared mutable workspace. Portable delegates are
  read-only; strict writers require runtime-atomic binding.
- Child completion is evidence, not an accepted dependency or authorization for an
  external mutation. Validate its exact role, status, scope, identity, checks, and
  findings before consuming it.
- Timeout, silence, missing progress, wrapper yield, and close acknowledgement do
  not permit retry, replacement, takeover, unlock, cleanup, or state inference.
- Ledgers and event journals contain bounded metadata only. Reports must not contain
  secrets, prompts, complete source, embeddings, or unbounded transcripts.

## Capability preflight

Run a `capability-preflight-v2` record before any edit or spawn. Portable requires
identifiable read-only subagents, terminal result delivery, and shared snapshot
access. Strict additionally requires the authoritative runtime capabilities in
[`review-runtime-strict.md`](review-runtime-strict.md), must be explicitly
requested, and must fail before mutation when its authoritative record is absent.
Never silently downgrade it.

## Portable parent state

Portable mode may keep task metadata in parent orchestration state when the runtime
does not expose a ledger. Mark persistence as unavailable; do not create an
untracked repository Markdown file to imitate a runtime store. A task-specific
temporary handoff may preserve bounded checkpoint metadata, but it is not a lock or
authoritative lifecycle state.

Record at minimum:

```text
task/run identity, role, objective, dependencies, mode
read/write/impact scopes and exclusions
baseline and result content identities
focused checks, snapshot/artifact identity
report disposition, review status, outcome, and reason
```

Use the helper to validate records. Portable artifact and coverage proofs are
derived only from verified snapshot operations and the bound reviewer result; do
not invent them. Never invent strict terminal IDs, CAS versions, or stop confirmations.

## Strict runtime ledger

When strict is available, the authoritative row additionally identifies owner,
lock, invocation, binding token, target, channel, association, attempt,
replacement slot, fixed deadlines, monotonic timing, runtime event IDs, artifact
access and review coverage proofs, checkpoint, quarantine reason, and row version.

Every mutation is a complete-row CAS and returns the authoritative winner on loss.
Events are append-only. Retain owner and lock until a runtime stop/terminal event
and the artifact/report have been captured or quarantined. Detailed strict binding
and recovery are in [`review-runtime-strict.md`](review-runtime-strict.md) and
[`review-recovery-strict.md`](review-recovery-strict.md).

## Scope, ownership, and worktrees

Before a task is claimed, record common baseline, read/write/impact scopes,
exclusions, dependencies, focused checks, mode, and integration order. Parallel
writers are safe only with disjoint write scopes, isolated workspaces, a common
baseline, and no unreviewed shared dependency. Serialize shared contracts,
migrations, generated files, and overlapping callers. Background work must be
genuinely independent and joined before a dependent result is consumed.

Create a task-specific isolated worktree at the recorded baseline when a strict or
parallel writer needs mutable space. Never give two writers the same mutable path.
An isolated worktree changes only the mutable path; it does not change the
execution or authorization boundary. The child task must follow the parent's
sandbox, approval, network, and repository instructions.
The main agent verifies each checkpoint and focused checks, integrates in recorded
order, creates a new integrated identity, and reviews that integrated result.
Remove an isolated worktree only after no dependent recovery or review needs it;
cleanup is never a response to silence or timeout.

## Review coordination

Freeze the exact review paths with `snapshot_tool.py` before starting a reviewer.
Use one integrated reviewer for acceptance. The public V2 contract has no review
lane assignment or aggregate-proof record; multiple partial reviews therefore
cannot be combined into `CLEAN`. Portable review mechanics are in
[`review-runtime.md`](review-runtime.md); strict lifecycle mechanics are in the
strict references above.

## External mutation boundary

No child or recovery path may push, publish, deploy, install, change a live service,
or create a remote resource. A local commit is allowed only after acceptance, an
explicit user request, a clean baseline index, and no pre-existing edit overlapping
an explicit task path. Follow the detailed commit gate in [`workflow.md`](workflow.md).
