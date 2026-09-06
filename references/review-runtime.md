# Review Runtime, Waiting, and Classification

Read this reference for snapshot freezing, delegated-agent runtime binding, budgets,
timing, provenance, runtime events, report scanning, or compare-and-set (CAS). It
contains the runtime rules only; role schemas are in task-contracts.md.

## Fixed review budget

Choose the tier before freezing the snapshot and record it in the reviewer TaskSpec.

| Tier | Use | Total snapshot budget | Initial attempt |
| --- | --- | ---: | ---: |
| Standard | small integrated review | 7200s | 1800s |
| Extended | broad, high-risk, long-running, or review_set review | 10800s | 3600s |

The snapshot budget is shared by the original attempt, recovery, replacement decision,
replacement spawn/binding, and replacement review. Use these minima:

~~~text
review_recovery_grace_budget = 60s
review_replacement_decision_reserve_budget = 120s
review_spawn_reserve_budget = 120s
review_replacement_min_budget >= 1500s
review_replacement_limit = 1
~~~

Require:

~~~text
review_wait_budget >= review_initial_budget
  + review_recovery_grace_budget
  + review_replacement_decision_reserve_budget
  + review_spawn_reserve_budget
  + review_replacement_min_budget
~~~

The remaining time is shared headroom. Never shorten the initial slice below 1800s or
restart the clock for a replacement. A custom budget may be longer but must preserve
these minima and leave the replacement gate intact.

## Blocking wait and timing

Use one foreground/blocking wait per bounded invocation. A wrapper continuation is part
of the same wait. Do not poll status, inspect a moving workspace, read logs, or send a
liveness message. Empty output, timed_out, a wrapper yield, and No agents completed yet
are observations only.

At snapshot freeze, before pre_spawn, persist one verified monotonic
snapshot_budget_started_at and derive snapshot_deadline_at exactly once. For the
original attempt:

~~~text
attempt_deadline_at = min(snapshot_deadline_at,
                          snapshot_budget_started_at + review_initial_budget)
recovery_deadline_at = min(snapshot_deadline_at,
                           attempt_deadline_at + review_recovery_grace_budget)
~~~

A replacement uses the same snapshot_deadline_at as its fixed attempt/recovery deadline.
Record budget remaining at spawn and binding from that clock. Do not derive deadlines
from spawn confirmation or a later continuation.

Validate before arithmetic:

- every timing sample uses one verified monotonic clock and is finite;
- timestamps are ordered: snapshot start ≤ spawn request ≤ spawn confirmation/now;
- started_at is present only after a real running event;
- deadlines are persisted absolute samples and satisfy the formulas;
- durations are finite and non-negative, and all reserved minima fit the total budget.

If a clock, deadline, identity, or reserve is missing, reversed, non-finite, or below
the gate, mark timing UNVERIFIED, retain locks, and prohibit acceptance, replacement,
or takeover. Wall-clock timestamps are for display only.

## Immutable snapshot

Freeze the exact impact set before spawning a reviewer. The manifest includes every
reviewed path, base identity, resulting content identity, and per-file hash.

For a Git workspace, use a detached worktree or archive rooted at the recorded
tree/patch. For a non-Git skill or other loose files, copy the exact relative layout
(including references/), make files 0444 and directories 0555, and verify the modes.
SNAPSHOT_ID is the logical/content identity; ARTIFACT_PATH is a separate location.
A flattened, writable, live-path, or identity-mismatched artifact is invalid.

Pause all writers and main-agent edits for the review. After review, re-hash both the
artifact and authoritative workspace. Any change invalidates CLEAN and requires a new
snapshot.

## Spawn and identity binding

Before every spawn, the runtime atomically claims the TaskSpec row, owner, lock,
invocation_id, binding_token, budget, and exact reserved runtime target. The initial
sentinels are:

~~~text
agent_id = UNASSIGNED
agent_channel = UNASSIGNED
transport_invocation_association = UNASSIGNED
reserved_runtime_target = NONE
report_id = NONE
runtime_terminal_event_id = NONE
overlay = NONE
binding_failure_provenance = NONE
~~~

The reserved target is an opaque addressable tuple for the exact
run/task/attempt/invocation/token and includes its channel and one-to-one transport
association. An agent ID alone is never a stop or binding proof.

runtime_atomic binds identity, target, timing, and result envelope before model
execution; writers require it. If unavailable, transport_bound_provisional is allowed
only for read-only roles. The child may inspect only the supplied immutable artifact
before binding and must not author runtime fields.

When binding succeeds, one post_spawn CAS records the exact runtime agent ID, channel,
transport association, target, and spawn-confirmed time. The parent does not wait for
a separate acknowledgement. If post_spawn or binding fails after the child may have
started, one combined full-row CAS must record the target, finite binding_failed_at
(or explicit UNVERIFIED), binding_failure_provenance=SPAWN_UNCONFIRMED, and
overlay=CANCEL_REQUESTED before issuing one idempotent stop request. Do not retry,
take over, unlock, or spawn a replacement. If no target can be addressed, reconcile
the spawn once through the authoritative runtime; an unresolved possible invocation
becomes UNADDRESSABLE/CANCEL_REQUESTED/BLOCKED with the lock retained.

A spawn failure is terminal only when the runtime proves that no child was created. It
uses a runtime terminal event with CHILD_STARTED=no, STATUS=spawn_failed, no stop
confirmation, and the unused reserved target. An ambiguous spawn is treated as live
and follows the binding-failure path.

## Runtime event schemas

Runtime events are closed, runtime-authored records. Child prose or a platform status
string cannot prove stop, cancellation, resumability, timing, or terminal identity.

~~~text
RUNTIME_INTERRUPTION_EVENT
EVENT_ID, RUN_ID, TASK_ID, INVOCATION_ID, ATTEMPT
AGENT_ID, AGENT_CHANNEL, TRANSPORT_INVOCATION_ASSOCIATION
RUNTIME_TARGET, BINDING_TOKEN, CLOCK_SOURCE
STATUS: interrupted
STOP_CONFIRMED: yes
RESUMABLE: yes | no
CHECKPOINT_ID, CHECKPOINT_CONTENT_IDENTITY
ARTIFACT_ACCESS_PROOF
INTERRUPTION_REASON
CANCEL_CONFIRMED_AT, TERMINAL_AT
~~~

RESUMABLE=yes requires an immutable validated checkpoint and access proof. With
RESUMABLE=no, checkpoint fields are NONE. The event must match the current exact
invocation and either the bound identities or the reserved unbound target.

~~~text
RUNTIME_TERMINAL_EVENT
EVENT_ID, RUNTIME_TERMINAL_EVENT_ID
RUN_ID, TASK_ID, INVOCATION_ID, ATTEMPT
AGENT_ID, AGENT_CHANNEL, TRANSPORT_INVOCATION_ASSOCIATION
RUNTIME_TARGET, BINDING_TOKEN, CLOCK_SOURCE
STATUS: failed | cancelled | errored | shutdown | spawn_failed
CHILD_STARTED: yes | no
STOP_CONFIRMED: yes | not_applicable
CANCEL_CONFIRMED_AT, TERMINAL_AT, TERMINAL_REASON
~~~

CHILD_STARTED=no is legal only for spawn_failed, with unassigned identities and no
stop confirmation. CHILD_STARTED=yes requires the exact target/channel/association,
STOP_CONFIRMED=yes, and a finite cancel confirmation. Runtime terminal identity is
independent of REPORT_ID and is required for failure, cancellation, shutdown, and
spawn-failure materialization.

Validate the event against the current row, run/task/attempt/invocation/token,
channel/association, target, clock, and fixed deadlines. A missing field, child-authored
field, mismatched identity, uncertain stop, or malformed reason is quarantined.

## Timing and stop classification

A runtime stop before or at the applicable fixed recovery deadline may enter normal
recovery. A stop after it may attest that the process eventually stopped, but it is
only STOP_CONFIRMED_ONLY:

~~~text
state = BLOCKED
overlay = CANCEL_REQUESTED
terminal_reason = STOP_CONFIRMED_ONLY
report_disposition = none
owner and lock retained
~~~

STOP_CONFIRMED_ONLY is an event disposition, not a child status and not permission to
recover, replace, take over, unlock, accept, or create a new budget. It is materialized
by one complete-row CAS; do not perform a second generic BLOCKED mutation.

A runtime stop may race with a parent cancellation CAS. If the exact event proves it
completed before the cancellation won, replay that same event once against the
authoritative row and preserve the winning overlay. Do not issue a second stop. Any
other ordering or identity mismatch is quarantined. A close acknowledgement or
previous_status is never terminal proof.

## Report binding and validation

Before a report-binding CAS:

1. parse the exact closed role schema;
2. match run/task/attempt/invocation/token and bound channel/association;
3. validate base and result content identities against the snapshot/workspace;
4. validate artifact access and, for a reviewer, positive coverage proof;
5. validate timing and terminal status;
6. compare the complete expected row version and consume the authoritative winner on CAS loss.

A provisional report first passes the adapter in task-contracts.md and may late-bind only
once. The parent supplies snapshot identity, artifact proof, and coverage proof; the
child cannot add them. A cancellation, replacement, or terminal winner makes the
provisional report stale.

A role-complete result must finish by its attempt deadline; a replacement result must
finish by the fixed snapshot deadline. Reviewer CLEAN additionally requires complete
scope accounting, direct caller/consumer coverage, explicit exclusions, required
focused checks, and the matching LaneCoverageProofV1 or aggregate CoverageProofV1.
CLEAN is never inferred from passing tests or from a child claim alone.

Every CAS uses the full expected row, increments its version, and returns the
authoritative winning row on loss. Never mutate from a stale pre-CAS read. Coordination
records contain metadata only; never log prompts, source text, embeddings, secrets, or
model output.
