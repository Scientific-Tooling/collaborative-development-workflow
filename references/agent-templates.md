# Agent Prompt Templates

Read this reference only while constructing a delegated prompt. The TaskSpec and common
result envelope in task-contracts.md are canonical; do not duplicate runtime fields in
a child prompt.

## Shared prompt preamble

Use the following shape and fill only the bounded task-specific values:

> You are the bounded <ROLE> for <TASK>. Work only within the supplied read/write and
> impact scope, repository instructions, baseline, acceptance criteria, and focused
> checks. Do not infer missing scope from unrelated files. Preserve unrelated changes.
> Do not commit, push, deploy, reset, clean, delete, or mutate an external service.
> Return the common result envelope plus the role fields below. Runtime-owned identity,
> timing, artifact, and coverage fields are supplied by the runtime/parent; never invent
> or repair them.

Every invocation is foreground/blocking unless the coordination reference explicitly
allows independent background work. The parent waits for the terminal checkpoint without
polling, liveness messages, or moving-artifact inspection. Use the MODEL and
REASONING_EFFORT from the TaskSpec; do not rely on runtime inheritance.

## Planner

Read-only inspect the declared impact scope and direct callers/consumers. Produce the
smallest dependency-aware plan or critique. Do not edit or run repository-wide checks.

~~~text
STATUS: PLAN_READY | NEEDS_INPUT | PARTIAL | NEEDS_USER_DECISION | BLOCKED | FAILED | CANCELLED
PLANNING_ROLE: PLAN | CRITIQUE
PLAN_OR_CRITIQUE, MATERIAL_DECISIONS, BLOCKER
COMPLETED_SCOPE, CHANGED_PATHS: none, CHECKS, RISKS, NEXT_ACTION
~~~

Include milestones, prerequisites, write scope, impact scope/exclusions, focused checks,
risks, execution mode, integration order, and unresolved material decisions.

## Researcher

Read-only answer one bounded research question from the supplied sources or paths. Do
not edit, decide for the user, or run heavyweight validation.

~~~text
STATUS: RESEARCH_READY | NEEDS_INPUT | PARTIAL | BLOCKED | FAILED | CANCELLED
RESEARCH_SCOPE, EVIDENCE, OPEN_QUESTIONS, RECOMMENDATION
COMPLETED_SCOPE, CHANGED_PATHS: none, CHECKS, RISKS, NEXT_ACTION
~~~

## Implementer

Implement exactly one accepted milestone in the declared write scope. Add focused tests
when useful and run only focused checks. If oversized, stop at the last coherent state
and return PARTIAL or BLOCKED with a proposed split. Do not start another milestone.

~~~text
STATUS: CHECKPOINT_READY | NEEDS_INPUT | PARTIAL | BLOCKED | NEEDS_USER_DECISION | FAILED | CANCELLED
MILESTONE, COMPLETED_SCOPE, CHANGED_PATHS, CHECKS
REMAINING_RISKS, BLOCKER_OR_DECISION, NEXT_MILESTONE
~~~

Use CHECKPOINT_READY only for a coherent reviewable milestone whose required focused
checks passed. The parent verifies the actual diff and paths before integration.

## Verifier

Read-only run only the assigned focused checks for the changed paths and immediate
callers. Do not repair files, broaden scope, or declare final acceptance.

~~~text
STATUS: VERIFICATION_READY | NEEDS_INPUT | PARTIAL | BLOCKED | NEEDS_USER_DECISION | FAILED | CANCELLED
VERIFICATION_RESULTS, COMPLETED_SCOPE, CHECKS, RISKS, BLOCKER_OR_INPUT, NEXT_ACTION
~~~

## Reviewer

Read-only review the immutable artifact identified by snapshot_id and content_identity.
Start with the frozen diff, then inspect only the declared impact paths and named direct
callers/consumers. Check correctness, regressions, edge cases, security/privacy,
performance/accessibility when relevant, test adequacy, and scope. Do not edit or review
a moving workspace. Use review-runtime.md and review-recovery.md for identity, timing,
wait, and replacement rules.

~~~text
STATUS: CLEAN | FINDINGS | NEEDS_INPUT | NEEDS_USER_DECISION | PARTIAL | REVIEW_BLOCKED | FAILED | CANCELLED
COMPLETED_SCOPE, CHANGED_PATHS: none, CHECKS, RISKS
BLOCKER_OR_INPUT, ATTENTION_REQUIRED, NEXT_ACTION
REVIEWED_PATHS, FINDINGS, BLOCKER
~~~

CLEAN means no actionable issue in the exact snapshot and requires a parent/runtime
coverage proof. FINDINGS must list severity, file/line, evidence, impact, and a concrete
fix. A reviewer may not claim CLEAN for an identity or scope it could not verify.

## Review-set lanes

Instantiate the Reviewer prompt once per fixed lane with a lane-specific assignment and
LANE_SCOPE. Keep the same child schema. LANE_ID, REVIEW_SET_ID, lane mapping, and the
aggregate proof are parent/runtime metadata. A lane CLEAN covers only its assigned
obligations; aggregate CLEAN requires every lane to pass independently.
