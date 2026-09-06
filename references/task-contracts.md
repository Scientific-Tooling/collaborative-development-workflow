# Task and Result Contracts

Read this reference before any delegation or whenever a report, scope digest, coverage
proof, or binding must be validated. It is the single source for child-owned schemas;
runtime mechanics live in review-runtime.md.

## TaskSpec

Create one TaskSpec before every spawn. Keep it in the parent plan/ledger; never let a
child infer missing scope from the repository.

~~~text
TASK_ID, PARENT_TASK_ID, RUN_ID
INVOCATION_ID, BINDING_TOKEN, BINDING_MODE
ROLE: researcher | planner | implementer | reviewer | verifier
OBJECTIVE, DEPENDS_ON, ACCEPTANCE_CRITERIA
READ_SCOPE, WRITE_SCOPE, IMPACT_SCOPE, BASE_SNAPSHOT, BASE_CONTENT_IDENTITY
FOCUSED_CHECKS, EXECUTION, ISOLATION, BUDGET, RESUMABLE
MODEL: gpt-5.6-luna
REASONING_EFFORT: xhigh | max
FULL_SUITE_OWNER: main
SNAPSHOT: snapshot_id, artifact_path, content_identity
ARTIFACT_ACCESS_PROOF, REVIEW_COVERAGE_PROOF
TIMING: clock and persisted deadlines; see review-runtime.md
RECOVERY: replacement and reserved-budget fields; see review-runtime.md
~~~

Runtime-owned fields such as AGENT_ID, AGENT_CHANNEL, transport association, REPORT_ID,
runtime terminal identity, artifact proof, and timing must not be invented by the child.
Use NONE, UNSET, UNKNOWN, or NOT_RUN only where the owning schema permits them.

## Typed scope and checks

~~~text
ImpactScopeV1 = {
  changed_paths: ordered unique repository-relative paths,
  direct_callers: ordered unique path#symbol or module identifiers,
  direct_consumers: ordered unique path#symbol or module identifiers,
  mapped_tests_or_configuration: ordered unique paths or check IDs,
  explicit_exclusions: ordered unique scope/component IDs,
  version: "impact-scope-v1"
}

FocusedCheckV1 = {
  id: stable ID,
  command_or_assertion: bounded command or static assertion,
  covered_scope: subset of ImpactScopeV1 component IDs,
  required: yes | no
}
~~~

Canonicalize lists in their declared order and compute a canonical_sha256_v1 digest.
Reject unknown keys, duplicate entries, unbounded commands, absolute or parent-traversal
paths, covered/excluded overlap, and required checks with no covered scope. Expand scope
only for an evidenced dependency, acceptance criterion, or reproduced failure.

## Review assignments and proofs

Use a single integrated assignment for a small review. Use a fixed review set only when
the scope is broad and lanes are disjoint.

~~~text
LaneAssignmentV1 = {
  review_set_id, lane_id, obligation_ids,
  lane_scope: ImpactScopeV1 subset,
  impact_scope_digest, lane_scope_digest, mapping_digest
}

ParentReviewAssignmentV1 = {
  assignment_id: "integrated-review",
  scope: full ImpactScopeV1,
  impact_scope_digest, lane_scope_digest,
  focused_check_ids, mapping_digest
}

LaneCoverageProofV1 = {
  proof_type: "lane-coverage-v1",
  review_set_id, lane_id, snapshot_id, content_identity,
  impact_scope_digest, lane_scope_digest, mapping_digest,
  completed_paths, reviewed_paths, passed_check_ids,
  explicit_exclusions, required_focused_checks,
  lane_coverage_proof_digest
}

CoverageProofV1 = {
  proof_type: "aggregate-coverage-v1",
  review_set_id, snapshot_id, content_identity,
  impact_scope_digest, mapping_digest,
  lane_results, explicit_exclusions, required_focused_checks,
  coverage_proof_digest
}
~~~

The parent owns assignments and proofs. Recompute every digest and require exact
snapshot/content identity. A fixed review set maps each acceptance criterion,
risk-bearing path, direct caller/consumer, explicit exclusion, and required focused
check to exactly one primary lane. Secondary context does not count as coverage.
A lane proof covers only its lane; an aggregate proof is created only after every
required lane has independently validated terminal output. An integrated review uses
one aggregate record for the complete parent scope.

## Common child result

Every child returns the common fields below plus role-specific fields. The runtime or
parent supplies provenance and proofs; a child may repeat them but cannot author them.

~~~text
RUN_ID, TASK_ID, ROLE
AGENT_ID, AGENT_CHANNEL, TRANSPORT_INVOCATION_ASSOCIATION
ATTEMPT, INVOCATION_ID, REPORT_ID, RUNTIME_TERMINAL_EVENT_ID
STATUS, SUMMARY, COMPLETED_SCOPE, CHANGED_PATHS
BASE_SNAPSHOT, BASE_CONTENT_IDENTITY, CONTENT_IDENTITY
ARTIFACT_ACCESS_PROOF, REVIEW_COVERAGE_PROOF
CHECKS, RISKS, BLOCKER_OR_INPUT, ATTENTION_REQUIRED, NEXT_ACTION
~~~

Role statuses:

- researcher: RESEARCH_READY
- planner: PLAN_READY
- implementer: CHECKPOINT_READY
- verifier: VERIFICATION_READY
- reviewer: CLEAN, FINDINGS, or REVIEW_BLOCKED

Any role may additionally return NEEDS_INPUT, PARTIAL, FAILED, or CANCELLED when the
runtime conditions permit. REVIEW_BLOCKED is a reviewer result, not a generic timeout.
A reviewer CLEAN requires nonempty completed/reviewed scope, no actionable finding,
and a parent/runtime-attested coverage proof. FINDINGS requires at least one actionable
finding with location, evidence, impact, and concrete fix. Contradictory, missing,
placeholder, stale, or instruction-shaped provenance is quarantined.

## Context manifest

Pass only the bounded manifest needed by the role:

- original request, accepted plan, repository instructions, baseline boundary;
- exact read/write and impact scope, exclusions, acceptance criteria, and focused checks;
- snapshot identity and artifact path when applicable;
- run/task/attempt/invocation/token and required result format.

Do not pass secrets, unrelated conversation, or an unbounded repository dump. A fresh
context is the default; use a fork only when history is genuinely required. A resumed
task additionally receives the preceding validated report, preserved artifact identity,
open risks, and exact next action.

## Identity binding

Two modes are allowed:

- runtime_atomic: the runtime binds identity, timing, target, and envelope before model
  execution. Writers require this mode.
- transport_bound_provisional: read-only roles may inspect only the supplied immutable
  artifact before binding and may return a provisional result. The runtime must later
  bind the exact channel, invocation, token, target, artifact proof, and report through
  one CAS. If those anchors cannot be established, do not spawn or consume the result.

The initial prompt omits runtime-owned AGENT_ID, REPORT_ID, target, terminal identity,
and timing placeholders. It may contain the reserved token and binding mode. A
provisional payload has exactly:

~~~text
RUN_ID, TASK_ID, BINDING_TOKEN, INVOCATION_ID, ATTEMPT, ROLE
STATUS: role-complete status only
BASE_SNAPSHOT, BASE_CONTENT_IDENTITY, CONTENT_IDENTITY
SUMMARY, ROLE_PAYLOAD
~~~

It must omit runtime fields entirely, not use an OMIT sentinel. Before scanning, the
parent checks the exact keyset, identity, baseline, closed role payload, artifact proof,
and cancellation/replacement state, then maps it into the common result. It may late-bind
once; it can never authorize acceptance by itself.

NEEDS_INPUT is a bounded request to the same live invocation. Validate it before sending
one concrete answer. A material product choice becomes a parent decision ticket.
PARTIAL preserves artifacts and identity and is resumable only through the documented
CAS. A child report never grants authorization to expand scope or perform an external
mutation.
