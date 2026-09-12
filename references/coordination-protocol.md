# Coordination Protocol (V2)

Read this only for two or more assignments, background work, isolated worktrees, or
work that may outlive the current turn. The common lifecycle, preflight, acceptance,
and commit gate remain in [`workflow.md`](workflow.md); record fields and public
statuses remain in [`contracts-v2.json`](contracts-v2.json).

## Invariants

- The main agent owns scope, integration, full validation, acceptance, and release
  of review locks.
- One task has one owner and declared mutable workspace. Portable delegates require
  an enforced read-only sandbox; strict writers require strict runtime binding.
- Before consuming a child result, validate its role, status, scope, identity,
  checks, findings, and requested and resolved model evidence. A child result is
  evidence, not an accepted dependency or authorization for an external change.
- Timeout, silence, wrapper yield, missing progress, and close acknowledgement do
  not permit retry, replacement, takeover, unlock, cleanup, or state inference.
- Ledgers and event journals contain bounded metadata only. Reports cannot contain
  secrets, prompts, complete source, embeddings, or unbounded transcripts.

Complete the single preflight procedure in `workflow.md` before any edit or spawn.
For strict mode, also load the strict runtime references; never invent terminal
IDs, conflict-safe update versions, or stop confirmations.

For broad or unclear work, one discovery agent may inspect only a recorded
preliminary read scope and exclusions. Use an enforced read-only sandbox, focused
question, fixed output limit, preliminary TaskSpec and preflight, and separate run
ID. Validate them before spawning. Its suggestions do not authorize scope, and its
provisional scope and run ID cannot become acceptance evidence.
Run a `capability-preflight-v2` record before any edit or spawn. Portable requires
identifiable read-only subagents, terminal result delivery, shared snapshot access,
and a protected Reviewer wait. Strict additionally requires the authoritative
runtime capabilities in
[`review-runtime-strict.md`](review-runtime-strict.md), must be explicitly
requested, and must fail before mutation when its authoritative record is absent.
Never silently downgrade it.

## Portable parent state

When no runtime ledger exists, keep metadata in parent orchestration state and mark
persistence unavailable. Do not create an untracked repository Markdown file to
imitate a runtime store. A temporary, task-specific handoff may preserve bounded
checkpoint metadata, but is not a lock or authoritative state.

Record at least:

```text
task/run identity, role, objective, dependencies, mode
read/write/impact scopes and exclusions
model request and resolved model profile
baseline and result content identities
focused checks and snapshot/artifact identity
report disposition, review status, outcome, and reason
```

Validate records with the helper. Derive portable artifact and coverage proofs only
from verified snapshot operations and the bound reviewer result.

## Strict ledger

An authoritative strict row also identifies owner, lock, invocation, binding token,
target, channel, association, attempt, replacement slot, fixed deadlines, monotonic
timing, runtime event IDs, access and coverage proofs, checkpoint, quarantine
reason, and row version.

Every mutation is a complete-row compare-and-set update: it succeeds only when the
whole current row matches the expected value and otherwise returns the authoritative
winner. Events are append-only. Retain owner and lock until a runtime stop or
terminal event and the artifact/report have been captured or quarantined. Follow
the detailed strict runtime and recovery references.

## Scope, ownership, and worktrees

Before claiming work, record the common baseline, read/write/impact scopes,
exclusions, dependencies, focused checks, mode, integration order, and each
assignment's model request. Model choice never changes write ownership or
isolation.

Parallel writers require disjoint write scopes, isolated workspaces, a common
baseline, and no unreviewed shared dependency. Serialize shared contracts,
migrations, generated files, and overlapping callers. Background work must be
independent and joined before a dependent result is used.

When a strict or parallel writer needs mutable space, create a task-specific
worktree at the recorded baseline. Never give two writers the same mutable path.
A worktree changes only the mutable path, not sandbox, approval, network, or
authorization rules. The main agent verifies every checkpoint and focused check,
integrates in recorded order, creates a new integrated identity, and reviews that
integrated result. Remove a worktree only after no dependent recovery or review
needs it; silence or timeout never authorizes cleanup.

Use one integrated reviewer for acceptance. ContractV2 has no review-lane or
aggregate-proof record, so partial reviews cannot combine into `CLEAN`. Snapshot
and review mechanics remain in [`review-runtime.md`](review-runtime.md).
Choose and record the Reviewer budget before freezing; use the Extended profile by
default when no shorter limit is explicitly requested. Freeze the exact review
paths with `snapshot_tool.py` before starting a reviewer. Start one integrated
Reviewer with the selected `execution-budget-v2`, then wait once in the foreground
for that same invocation. Do not poll, probe, edit, roll over context, or issue an
automatic stop before the protected deadline. A wrapper yield is not a terminal
result and must resume the same wait. If the host cannot honor the selected window,
record the capability gap and leave acceptance blocked rather than shortening the
review silently.

The public V2 contract has no review lane assignment or aggregate-proof record;
multiple partial reviews therefore cannot be combined into `CLEAN`. Portable
snapshot and wait mechanics are in [`review-runtime.md`](review-runtime.md); strict
lifecycle mechanics are in the strict references above.

## External mutation boundary

No child or recovery path may push, publish, deploy, install, change a live service,
or create a remote resource. Follow the common commit and external-change gates in
`workflow.md`.
