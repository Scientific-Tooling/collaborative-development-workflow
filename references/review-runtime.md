# Review Runtime and Frozen Artifacts (V2)

Read this reference for snapshot freezing, portable evidence, strict binding,
timing, and runtime-owned lifecycle rules. ContractV2 owns machine shapes and
closed fields:

```text
contracts-v2.json:
  modes.required_capabilities.portable
  modes.required_capabilities.strict_additional
  records.runtime_completion_event
  records.runtime_terminal_event
  records.runtime_stop_event
  records.runtime_event_sequence
  artifact_contracts.snapshot_manifest
```

## Portable snapshot and review

Portable mode is the default. Before editing or spawning, validate a
`capability-preflight-v2` record. It is ready only when the parent can select an
identifiable read-only reviewer, receive a terminal result, and share one frozen
artifact path. The parent remains the only writer and validates identity, scope,
evidence, and final workspace state.

Freeze and verify the exact impact scope:

```bash
python3 scripts/snapshot_tool.py create REPOSITORY \
  --scope scope.json --output /tmp/cdw-review-<task>
python3 scripts/snapshot_tool.py verify /tmp/cdw-review-<task> --scope scope.json
```

The helper derives manifest version, entry status/type values, artifact prefixes,
baseline-only types, and bounded counts from
`contracts-v2.json: artifact_contracts.snapshot_manifest`. `snapshot_tool.py`
still owns filesystem rules: Git identity, normalized scope, present/deleted
entries, hashes, modes, symlink targets, scoped `baseline/` Git-HEAD bytes, and
post-state `files/` bytes. It never follows symlinks or traversal paths, rejects
races, and fails closed when descriptor-relative POSIX operations are unavailable.
Artifacts are outside the repository and owner-only (`0500` directories,
`0400` files); verification rehashes the manifest and copied files. A rename is
deletion plus addition.

Give the reviewer the bounded request, accepted plan, scope, exclusions, checks,
artifact path, and identities. Request fresh context with `fork_context=false`
when supported, then pause writers. After the result:

```bash
python3 scripts/snapshot_tool.py compare REPOSITORY /tmp/cdw-review-<task> \
  --scope scope.json
```

`compare` exit `0` is an exact artifact/workspace match, `1` is an identity
mismatch, and `2` is invalid input or artifact. A missing usable terminal result is
`REVIEW_UNAVAILABLE`; a delivered result whose identity, scope, artifact access, or
coverage proof cannot be validated is `REVIEW_BLOCKED`. Neither outcome is
accepting. Portable evidence does not prove strict CAS, authoritative stop, or
runtime-authored event semantics.

## Strict capability gate

Strict mode is opt-in and must stop before mutation unless an authoritative
record validates the portable set plus
`modes.required_capabilities.strict_additional`. The helper validates preflight
and missing capabilities but cannot upgrade an observed surface to
`STRICT_READY`; only the runtime adapter can. Never silently downgrade strict.

## Budget and monotonic timing

Choose the review tier before freezing. Standard uses a 7200-second snapshot
budget and 1800-second initial attempt; Extended uses 10800 and 3600. The fixed
budget covers the attempt, recovery grace, one replacement, and its review.
Reserve at least 60 seconds for recovery, 120 for the replacement decision, 120
for spawn/binding, and 1500 for replacement review.

Use one foreground blocking wait per invocation; a wrapper continuation is the
same wait. Never poll or inspect a moving workspace; silence, empty output,
timeout text, and close acknowledgement are not results.

At freeze, record one verified monotonic start and derive the deadline once:

```text
attempt_deadline_at = min(snapshot_deadline_at,
                          snapshot_budget_started_at + initial_budget)
recovery_deadline_at = min(snapshot_deadline_at,
                           attempt_deadline_at + recovery_grace)
```

A replacement shares the fixed snapshot deadline and never restarts the clock.
Samples use one finite, ordered monotonic clock; wall-clock values are display
metadata. Missing, reversed, non-finite, or under-budget timing is `UNVERIFIED`
and blocks recovery and acceptance.

## Strict binding and lifecycle

Before each strict spawn, the authoritative runtime atomically claims the TaskSpec,
owner, lock, invocation, token, exact target, budget, and reserved timing.
`runtime_atomic` binds identity, target, timing, and the result envelope before
model execution. An agent ID alone is not binding or stop proof, and the child may
not author, repair, or infer runtime-owned fields.

After spawn, one complete-row CAS records the agent, channel, transport
association, target, and spawn time. Later CAS operations match the complete
expected row and return the authoritative winner on loss. Keep owner and lock
until the terminal event and artifact/report are captured or quarantined.

Completion, terminal, and stop events are runtime-authored closed records, not child
status strings. Validate them against the three record paths above. A completion
event anchors successful result delivery; a terminal event records failure-like
termination; a stop event is the authoritative result of an idempotent stop request.
`runtime_event_sequence` additionally requires unique event IDs, one shared
non-sentinel identity set, and non-decreasing monotonic timestamps. The semantic
validator distinguishes a `spawn_failed` sentinel event from a started child; a
started child requires an exact target and confirmed stop. A failure-like event may
omit cancellation confirmation when no cancellation was requested; a terminal
event whose status is `cancelled` must carry that confirmation and it cannot be
later than its terminal timestamp.

If binding may have failed after a child started, record the exact target and
`SPAWN_UNCONFIRMED` in one CAS, issue one idempotent stop, and wait once for the
runtime stop event. If target or stop cannot be confirmed, retain the lock and mark
the review blocked. Do not guess that the process is dead or replace it. An on-time
stop may enter the recovery procedure in [`review-recovery.md`](review-recovery.md);
a late stop proves eventual stop only and never authorizes replacement, takeover,
unlock, a new budget, or acceptance.

## Strict report binding

Before accepting strict `role_result`, match run/task/attempt/invocation/token,
channel/association, and base/result identities to the frozen snapshot. Validate
artifact access, positive coverage, the completion/terminal event, and fixed
timing; consume the row only after complete-row CAS wins. Role success must finish
by its attempt deadline and replacement by the fixed snapshot deadline. `CLEAN`
needs exact scope, focused checks, and parent/runtime proof; tests or a report
alone never imply it.

Recovery, replacement, findings rounds, cancellation, and blocked-review handoff
belong exclusively to [`review-recovery.md`](review-recovery.md).
