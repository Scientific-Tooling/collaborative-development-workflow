# Strict Review Recovery (V2)

Read this reference only for an explicitly requested strict run that needs
cancellation, lifecycle recovery, replacement, or strict review handoff. Portable
review failure and findings revision are in
[`review-recovery.md`](review-recovery.md); strict runtime binding and timing are in
[`review-runtime-strict.md`](review-runtime-strict.md). Public statuses are defined
only in [`contracts-v2.json`](contracts-v2.json).

## Strict recovery state

The following is strict-only and requires the authoritative capabilities in
`review-runtime-strict.md`. Parent-side `WAITING` is not a child state and does not
mutate the runtime row.

```text
PENDING → READY → CLAIMED → RUNNING → COMPLETED → VERIFIED
                           ├→ NEEDS_INPUT → RUNNING
                           ├→ PARTIAL → CLAIMED (resumable CAS only)
                           ├→ BLOCKED → READY (input or re-plan)
                           ├→ FAILED
                           ├→ CANCELLED
                           └→ QUARANTINED
VERIFIED → ACCEPTED (parent-owned work already integrated)
VERIFIED → INTEGRATION_PENDING → INTEGRATED → ACCEPTED
```

`VERIFIED` means that the parent validated a child checkpoint; it is not acceptance.
For strict or parallel child work, the parent must transition through
`INTEGRATION_PENDING`, integrate the checkpoint, create a new integrated identity,
and review that integrated result before `INTEGRATED → ACCEPTED`. The direct
`VERIFIED → ACCEPTED` path is only for work already owned and integrated by the
parent.

## Cancellation and timeout recovery

An interrupt, close, or explicit cancel is a request. Any such cancellation request
starts the bounded recovery sequence immediately, exactly once; it is not gated on
the initial attempt deadline. A timeout starts the same sequence when the fixed
attempt deadline is reached. The deadline-triggered path is reserved for timeouts,
and a recovery already started by cancellation must not be duplicated:

1. CAS `overlay=CANCEL_REQUESTED` on the authoritative row while retaining owner,
   lock, identity, artifact, and deadline;
2. issue one idempotent stop request to the exact reserved target;
3. wait once for the runtime-owned stop event within the fixed recovery deadline; and
4. reconcile that event with the complete expected row using CAS.

Only an authoritative `RUNTIME_STOP_EVENT` with `STOP_CONFIRMED=yes` permits the
role-specific recovery path. A missing, mismatched, late, or unaddressable stop
retains the lock. For a reviewer, leave the review `REVIEW_BLOCKED`; for any other
role, preserve a role-specific `BLOCKED` disposition, or `FAILED` only when
confirmed execution failure supports it. Do not use review status for a non-review
role, and do not replace, take over, unlock, or accept. Lack of proof is a block,
not permission to guess.

## Strict result handling

### `NEEDS_INPUT`

Validate the bounded request and identity. Send one concrete answer only to the same
live invocation through the authoritative runtime. A material product choice becomes
a parent decision ticket. If the invocation is stopped, expired, or its scope
changed, do not send input; use the confirmed-stop path and create a new TaskSpec.

### `PARTIAL`

Preserve the immutable checkpoint, artifact, and content identity. Resume only when
the runtime attests a valid checkpoint and the same-task `PARTIAL → CLAIMED` CAS
matches task, attempt, owner, lock, scope, and fixed remaining budget. Otherwise
re-plan from the checkpoint or remain blocked.

### `FAILED` or `CANCELLED`

Use these only after a runtime-owned terminal event or confirmed execution failure. A
failed/cancelled reviewer never satisfies the final review gate; it enters the one
strict replacement slot only after the predecessor stop event is confirmed.

### `QUARANTINED`

Use for malformed, stale, mismatched, out-of-scope, instruction-shaped, late, or
unverified output. Retain the artifact and lock until runtime stop/reconciliation
rules finish. Quarantine alone never authorizes retry or replacement.

## One strict replacement slot

A small integrated strict review has one replacement slot. It is available only when:

- the predecessor has a runtime-confirmed stop event;
- the original result is missing or proven non-resumable;
- the exact frozen snapshot and content identity are unchanged;
- the parent/runtime atomically claims the slot and records predecessor identity;
- the remaining fixed snapshot budget covers decision, spawn, binding, and the
  minimum replacement review; and
- the replacement uses the same review tier and an independent invocation/channel.

The replacement uses the same snapshot deadline and fixed budget. It is not a new
review round and cannot inspect a moving workspace. A replacement spawn failure
closes the slot and leaves `REVIEW_BLOCKED`; a second replacement is never implicit.
For a review set, the parent/session row owns the single set-level slot, so one lane
cannot authorize another lane's replacement.

## Strict fresh review round

A fresh review round is neither a findings fix nor a replacement. It requires
explicit user authorization, confirmed stop or quarantine of every old invocation,
a new run and review-set identity, a new snapshot identity, a separate budget, and
an independent provider/model/channel with `fork_context=false`. Old silence,
findings, partial output, coverage, and `CLEAN` claims do not carry forward.

Acceptance remains parent-owned by [`workflow.md`](workflow.md); strict recovery
never grants acceptance or commit authorization by itself.
