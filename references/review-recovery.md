# Review Recovery, Replacement, and Acceptance

Read this reference when a delegated wait yields no terminal report, an agent needs
input, work is partial/failed/cancelled, a reviewer must be replaced, or a review is
blocked. Runtime identity and deadline validation are in review-runtime.md.

## State vocabulary

WAITING is parent-side only. It means the parent is waiting on the same invocation and
does not change the child ledger row.

~~~text
PENDING → READY → CLAIMED → RUNNING → COMPLETED → VERIFIED
                           ├→ NEEDS_INPUT → RUNNING
                           ├→ PARTIAL → CLAIMED (only with resumable CAS)
                           ├→ BLOCKED → READY (after input or re-plan)
                           ├→ FAILED
                           ├→ CANCELLED
                           └→ QUARANTINED
VERIFIED → ACCEPTED
VERIFIED → INTEGRATION_PENDING → INTEGRATED → ACCEPTED
~~~

Child STATUS and ledger state are different vocabularies. NEEDS_INPUT and PARTIAL are
explicit reports, not timeout interpretations. FAILED requires confirmed execution or
task failure. CANCELLED requires runtime-confirmed stop. A reviewer without a usable
independent report is REVIEW_BLOCKED, not CLEAN or FAILED by inference.

## One logical wait

Wait in the foreground with the invocation's remaining deadline. If the platform
requires continuation, resume the same wait and consume the same monotonic budget. Do
not poll status, inspect moving artifacts, read logs, send liveness probes, retry, take
over, unlock, or mutate while waiting.

The following are observations only:

- empty output, timed_out, wrapper yield, or No agents completed yet;
- silence or a close acknowledgement;
- a previous_status response without a runtime terminal event.

When the deadline has not passed, continue the same wait. When it has passed, use the
single bounded recovery path below. A passive progress display may read local timing
metadata but must not wake the parent or call the agent API.

## Cancellation and recovery

An interrupt, close, or cancel is a request, not proof. On the one bounded recovery
window:

1. atomically set overlay=CANCEL_REQUESTED on the authoritative row, retaining owner,
   lock, identity, and artifact;
2. issue at most one idempotent stop request to the exact reserved target;
3. wait once for a runtime-owned terminal stop event within recovery_deadline;
4. validate the event and reconcile it with the current row using a full-row CAS.

If stop confirmation is on time, the role-specific recovery path may proceed. If it is
late, materialize STOP_CONFIRMED_ONLY as specified in review-runtime.md. If it is
missing, unverified, or unaddressable, retain the lock and record REVIEW_BLOCKED for a
reviewer (or the role-specific blocked/failed state for another role). Do not replace,
take over, unlock, or accept.

A binding failure is the earlier exception: first record
SPAWN_UNCONFIRMED plus CANCEL_REQUESTED and the exact target/timing in one CAS, then
send one stop request. It never skips the lock-retention rule. An ambiguous spawn is
not a spawn failure.

Every recovery/CAS loss consumes the transaction's authoritative winner. Never use the
stale row that lost the CAS. A late child report is quarantined and cannot reopen a
task.

## Outcome handling

### NEEDS_INPUT

Validate the bounded request and identity. If the same invocation is live and accepting
input, send one concrete answer and atomically transition back to RUNNING. If it asks
for a material user choice, create a parent decision ticket and pause at the planning
gate. If the invocation is stopped, expired, or its scope changed, do not send input;
wait for confirmed stop and create or hand off to a new TaskSpec.

### PARTIAL

Preserve the artifacts, checkpoint, and content identity. Resume only if the runtime
attests a valid checkpoint and the same-task PARTIAL → CLAIMED CAS matches task,
attempt, owner, lock, scope, and fixed remaining budget. Otherwise re-plan from the
checkpoint or mark blocked. A reviewer partial is replaceable only after proving it is
non-resumable.

### FAILED or CANCELLED

Use only after a runtime-owned terminal event or confirmed execution failure. Keep the
attempt artifact and report disposition. A failed/cancelled reviewer does not satisfy
the final review gate; it enters reviewer recovery or REVIEW_BLOCKED.

### QUARANTINED

Use for malformed, stale, mismatched, instruction-shaped, late, or unverified output.
Retain the artifact and lock until the runtime stop/reconciliation rules are complete.
Quarantine never authorizes retry or replacement by itself.

## Reviewer replacement

A small integrated review has one replacement slot. A replacement is allowed only when:

- the predecessor has a runtime-confirmed stop or a permanently unaddressable,
  fail-closed state;
- the original report is proven non-resumable or missing after the bounded recovery;
- the original immutable snapshot and content identity remain unchanged;
- the parent atomically claims the slot and records predecessor task/attempt/
  invocation/runtime-terminal identity;
- the remaining fixed snapshot budget still covers decision reserve, spawn reserve,
  binding, and at least review_replacement_min_budget effective review;
- the replacement uses the same review tier/profile and a new invocation identity.

The replacement is not a new clock, budget, review set, or permission to inspect a live
workspace. Its task row and owner/lock handoff are one full-row CAS before spawn. A
replacement spawn failure closes the slot and leaves REVIEW_BLOCKED. A second
replacement is never implicit.

For a review_set, the parent/session record owns one set-level replacement slot. Claim it
with REVIEW_SET_ID, lane ID, snapshot/content identity, scope/mapping digests,
predecessor stop identity, fixed replacement cutoff, owner/lock, and remaining budget.
Only the winning lane may spawn a replacement; another lane's per-row counter cannot
claim the set slot. Missing lanes keep the aggregate REVIEW_BLOCKED.

## Findings loop

For FINDINGS:

1. stop the review and retain its exact snapshot/report;
2. validate each finding and apply only confirmed, in-scope fixes;
3. rerun affected focused checks and produce a new checkpoint;
4. recanonicalize scope if the fix changes it;
5. freeze a new snapshot and rerun the complete affected integrated review or every lane
   of the fixed review set, using the pinned reviewer profile by default.

Do not review a moving workspace or carry CLEAN across identities. If a finding requires
a product decision, conflicts with user-owned edits, or cannot be reproduced, pause and
ask the user. Do not loop indefinitely on an unchanged finding.

## Fresh review round

A new round is not a replacement. It requires explicit authorization, confirmed stop or
permanent quarantine of every old invocation, a new RUN_ID/REVIEW_SET_ID, a new
SNAPSHOT_ID/artifact, a separately reserved budget, and an independent provider/model/
channel with fork_context=false. Old silence, findings, partial output, coverage, and
CLEAN claims do not count. If these conditions are unavailable, remain REVIEW_BLOCKED.

## Acceptance gate

Before acceptance, the main agent must have:

- a matching authoritative workspace and reviewed artifact identity;
- validated runtime artifact-access and exact-scope coverage proofs;
- final integrated CLEAN, or lane CLEAN results plus a gap-free aggregate proof;
- on-time, verified timing and no unresolved cancellation, quarantine, blocked lane, or
  user decision;
- passing focused checks and the repository-prescribed full validation.

A clean review cannot override failed validation. A missing proof or unusable report means
stop before acceptance and commit.
