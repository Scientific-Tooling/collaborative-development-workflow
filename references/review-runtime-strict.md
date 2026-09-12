# Strict Review Runtime and Lifecycle (V2)

Read this reference only when the user explicitly requests `strict`. It depends on
the portable snapshot procedure in [`review-runtime.md`](review-runtime.md) and on
the closed records in [`contracts-v2.json`](contracts-v2.json).

## Strict capability gate

Strict mode must stop before mutation unless an authoritative record validates the
portable set plus `modes.required_capabilities.strict_additional`. The helper
validates fail-closed preflight shapes but deliberately rejects `STRICT_READY` and
`ACCEPTED_STRICT`; only an external authoritative runtime adapter can attest and
validate those states. This repository specifies the adapter obligations but does
not ship an adapter or conformance claim. Never silently downgrade strict.

## Budget and monotonic timing

Choose the Reviewer profile from [`review-runtime.md`](review-runtime.md) before
freezing. `wall_clock_seconds` is the protected review window and the initial
attempt receives the full selected window: do not reserve replacement capacity by
shortening the first Reviewer to an arbitrary 30- or 60-minute slice. Recovery grace
is control-plane time after that window, not a reason to interrupt the Reviewer
early. A replacement is opportunistic only when the predecessor terminates early
and the fixed window still contains the required decision, spawn/binding, and
minimum review time.

Record the delegated attempt bound in the TaskSpec `execution-budget-v2` object
(`wall_clock_seconds`, `max_turns`, and `max_output_bytes`). The larger lifecycle
tier remains parent/runtime policy and must not be inferred from the task object.
Contract validation caps those three task values at 86,400 seconds, 128 turns, and
4,194,304 bytes. A runtime may enforce smaller limits.
Record all three selected values in the TaskSpec `execution-budget-v2` object
(`wall_clock_seconds`, `max_turns`, and `max_output_bytes`). The profile remains
parent/runtime policy and must not be inferred from an omitted task field. The
runtime must not apply a smaller hidden cap.

Use one foreground blocking wait per invocation; a wrapper continuation is the same
wait. Never poll or inspect a moving workspace. Do not issue an automatic
close/interrupt/cancel while the Reviewer is inside the protected window. Silence,
empty output, timeout text, and close acknowledgement are not results.

At freeze, record one verified monotonic start and derive the deadline once:

```text
attempt_deadline_at = snapshot_budget_started_at + wall_clock_seconds
snapshot_deadline_at = attempt_deadline_at
recovery_deadline_at = attempt_deadline_at + recovery_grace
```

Samples use one finite, ordered monotonic clock; wall-clock values are display
metadata. Missing, reversed, non-finite, or under-budget timing is `UNVERIFIED` and
blocks recovery and acceptance.

At `attempt_deadline_at`, a missing result may enter the runtime-owned stop path
once, using the exact target and `recovery_deadline_at`; it must not be interpreted
as a successful review. A portable-style wrapper timeout before that deadline is a
runtime capability failure, not permission to stop or replace the Reviewer.

## Strict binding and lifecycle

Before each strict spawn, the authoritative runtime atomically claims the TaskSpec,
owner, lock, invocation, token, exact target, model request, budget, and reserved
timing. `runtime_atomic` binds identity, target, model selection, timing, and the
result envelope before model execution. An explicit selection that cannot be bound
fails before execution; it is never replaced silently. An agent ID alone is not
binding or stop proof, and the child may not author, repair, or infer runtime-owned
fields.

After spawn, one complete-row compare-and-set (CAS) update records the agent,
channel, transport association, target, and spawn time. Later CAS operations match
the complete expected row and return the authoritative winner on loss. Keep owner
and lock until the terminal event and artifact/report are captured or quarantined.

Completion, terminal, and stop events are runtime-authored closed records, not child
status strings. Validate them against `records.runtime_completion_event`,
`records.runtime_terminal_event`, `records.runtime_stop_event`, and
`records.runtime_event_sequence`. A completion event anchors successful result
delivery; a terminal event records failure-like termination; a stop event is the
authoritative result of an idempotent stop request. The event sequence additionally
requires unique event IDs, one shared non-sentinel identity set, and non-decreasing
monotonic timestamps.

The semantic validator distinguishes a `spawn_failed` sentinel event from a started
child. A started child requires an exact target and confirmed stop. A failure-like
event may omit cancellation confirmation when no cancellation was requested; a
terminal event whose status is `cancelled` must carry that confirmation and it cannot
be later than its terminal timestamp.

If binding may have failed after a child started, record the exact target and
`SPAWN_UNCONFIRMED` in one CAS, issue one idempotent stop, and wait once for the
runtime stop event. If target or stop cannot be confirmed, retain the lock and mark
the review blocked. Do not guess that the process is dead or replace it. An on-time
stop may enter [`review-recovery-strict.md`](review-recovery-strict.md); a late stop
proves eventual stop only and never authorizes replacement, takeover, unlock, a new
budget, or acceptance.

## Strict report binding

Before accepting a strict `role_result`, match run/task/attempt/invocation/token,
channel/association, and base/result identities to the frozen snapshot. Validate
artifact access, positive coverage, the completion/terminal event, and fixed timing;
consume the row only after complete-row CAS wins. Role success must finish by its
attempt deadline and replacement by the fixed snapshot deadline. `CLEAN` needs
exact scope, focused checks, and parent/runtime proof; tests or a report alone
never imply it.

Strict lifecycle recovery, replacement, cancellation, and blocked-review handoff
belong exclusively to [`review-recovery-strict.md`](review-recovery-strict.md).
Findings-driven revision rounds, including strict findings, follow
[`review-recovery.md`](review-recovery.md).
