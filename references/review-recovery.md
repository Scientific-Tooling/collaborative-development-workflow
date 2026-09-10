# Review Recovery and Revision (V2)

Read this reference for portable review failure and all findings-driven revision
rounds. Strict cancellation, lifecycle recovery, and replacement are in
[`review-recovery-strict.md`](review-recovery-strict.md). Public statuses are
defined only in [`contracts-v2.json`](contracts-v2.json).

## Portable review failure

Portable mode has no runtime-asserted replacement or takeover semantics. If the
reviewer does not produce a usable terminal result, the parent may finish its own
focused and full validation, preserve the snapshot and diagnostics, and hand off:

```text
outcome = NOT_ACCEPTED
reason = REVIEW_UNAVAILABLE
commit = forbidden
```

If a result arrives but its snapshot, scope, artifact, or report shape cannot be
validated, classify it as `REVIEW_BLOCKED` and use the same non-accepting handoff.
Do not call a wrapper timeout, empty result, silence, or close acknowledgement a
failure, success, cancellation, or stop proof.

## Findings-driven revision

`FINDINGS` is an ordinary revision round, not a blocked-review restart and not a new
authorization boundary. For each validated finding:

1. retain the exact old report and snapshot identity;
2. apply only a confirmed, in-scope fix;
3. rerun the affected focused checks;
4. rerun the prescribed full validation;
5. confirm that the task objective, acceptance definitions, focused checks, and
   impact scope remain unchanged;
6. freeze and verify a new snapshot with new manifest/content identities; and
7. rerun one integrated review against every declared review path in a fresh
   independent context. No old coverage or `CLEAN` result transfers to the new
   identity. Review-lane aggregation is not supported by the public V2 contracts.

If a finding requires a scope, objective, criterion-definition, or focused-check
change, stop this acceptance sequence and start a newly authorized task/evidence
bundle. Never shrink or silently rewrite the original review task to obtain
acceptance.

Permit at most three total review rounds: the initial review plus two fix/review
rounds. If the third review still has actionable findings,
pause and request explicit user authorization. A finding that requires a product
decision, conflicts with user changes, or cannot be reproduced also pauses at the
user-decision gate.

The final acceptance gate remains parent-owned by [`workflow.md`](workflow.md).
