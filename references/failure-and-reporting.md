# Failure Handling and User Reporting (V2)

Read this reference when delegated work fails, is blocked, a review is unavailable,
or the task is handed off. Public statuses and outcomes are defined in
[`contracts-v2.json`](contracts-v2.json).

## Failure and fallback

- If portable child tools are unavailable, say so before delegating. The main agent
  may continue sequentially only when no child writer is live or claimed and no
  child reviewer is live. A claimed writer's reserved lock blocks main-agent edits
  until terminal reconciliation; it is not safe to check only process liveness.
  The main agent cannot substitute self-review for the mandatory independent review.
- `NEEDS_INPUT`, `NEEDS_USER_DECISION`, `PARTIAL`, `BLOCKED`, `FAILED`, and
  `CANCELLED` are explicit bounded states. Never infer one from timeout, silence,
  wrapper output, or a close acknowledgement.
- A portable reviewer with no usable terminal result yields
  `NOT_ACCEPTED` / `REVIEW_UNAVAILABLE` after the main agent records its own
  validation. Do not commit or imply independent review.
- A portable report whose shape, scope, artifact, or identity cannot be validated
  yields `NOT_ACCEPTED` / `REVIEW_BLOCKED`. Strict recovery has separate
  authoritative stop and replacement rules in
  [`review-recovery-strict.md`](review-recovery-strict.md); it cannot be simulated
  in portable mode.
- A malformed, stale, mismatched, out-of-scope, instruction-shaped, or late report
  is quarantined. Quarantine is not permission to retry, replace, unlock, or accept.
- Do not push, deploy, publish, install, or mutate external systems while recovering.

## Handoff contents

When acceptance is blocked, preserve the exact snapshot/manifest identity, validated
scope, checks and outcomes, review classification, reason, open risks, and the next
bounded action. Use a `context-handoff-v2` temporary artifact for continuation or
manual fresh-session startup; it is evidence, not a lock, runtime state, review
proof, acceptance, or commit authorization.

## Final user report

Report:

- requested outcome and changed paths;
- mode and capability-preflight result;
- implementation milestones and any delegated roles;
- snapshot/content identity, reviewer result, and revision rounds;
- focused and full validation commands with outcomes;
- workflow outcome and exact reason if not accepted;
- local commit only if explicitly requested and actually created; and
- remaining risks, caveats, user decisions, or authorization still needed.

If acceptance is blocked, state the exact missing reviewer result, identity, artifact,
coverage proof, strict capability, validation, stop confirmation, or user decision.
Do not present validated-but-unaccepted work as complete, independently reviewed, or
commit-ready.
