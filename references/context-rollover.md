# Context Rollover and Fresh-Task Handoff

Read this reference when the current context is approaching its safe remaining budget,
when a task must continue in a fresh invocation, or when a genuinely independent task
should start without inheriting unrelated conversation history.

This is a bounded handoff protocol. It does not claim that the runtime can force
compaction of the current top-level context or create a new top-level session. If the
runtime exposes neither capability, stop at a coherent checkpoint, preserve the
handoff artifact, and have the next session read it explicitly. Do not simulate a
runtime ledger or lock with a Markdown file.

## Choose the mode

There are four different operations. The first two are handoff modes; the last two are
review lifecycle operations and must not be disguised as an ordinary independent task.

- **Independent task:** create a new TaskSpec with a new task identity and only the
  request, repository instructions, baseline, and bounded scope needed for that task.
  Do not copy the old conversation or handoff unless a concrete dependency requires it.
- **Continuation:** keep the existing task identity and scope, and carry forward only a
  validated checkpoint, its artifact/content identity, open risks, and the exact next
  action. A fresh invocation is not a new task or permission to expand scope.
- **Replacement:** follow the bounded replacement path in `review-recovery.md`. It uses
  the same review snapshot/content identity and fixed budget, requires a confirmed stop
  or fail-closed predecessor, and is not a new `ContextHandoffV1` mode.
- **Fresh review round:** follow the fresh-round path in `review-recovery.md`. It needs
  explicit authorization, a new run/review-set and snapshot identity, a separately
  reserved budget, and an independent provider/model/channel; old review claims do not
  carry over.

Use this decision table before writing a handoff:

| Operation | Handoff mode | Identity/budget rule | Required authority |
| --- | --- | --- | --- |
| Independent task | `independent` | New TaskSpec/task identity; no old checkpoint | Parent scope and permissions |
| Continuation | `continuation` | Same task/scope; preserve validated checkpoint | Parent/runtime identity validation |
| Reviewer replacement | Neither; use recovery | Same snapshot and fixed review budget; new invocation only after predecessor stop | Recovery CAS and replacement slot |
| Fresh review round | Neither; use fresh-round procedure | New run/set, snapshot, budget, and channel | Explicit authorization |

If the task writes to a shared repository while another writer is live, do not start it
in the same mutable workspace. Use a task-specific isolated worktree or serialize the
writes. Read-only work does not authorize inspection of a moving review input.

## Rollover boundary

Start before the context is exhausted, leaving enough room to verify the checkpoint and
record the handoff. Prefer a natural boundary: a completed plan, a passed focused check,
an implementer checkpoint, an integrated result, or a confirmed runtime terminal event.
Do not describe an unfinished thought or an unverified child claim as a validated
checkpoint.

Before capturing a continuation:

1. Re-read the authoritative task/runtime row and record the current task, attempt,
   invocation, owner, lock, and active-work state.
2. Verify the repository baseline boundary, branch/HEAD, changed-path boundary, and
   focused-check outcomes.
3. Preserve any active child or reviewer. If a foreground wait is in progress, continue
   that same invocation and budget; do not poll, take over, unlock, or replace it merely
   because the parent context is large. If replacement is required, use the bounded
   recovery path first.

## Parent-owned handoff manifest

Use this closed shape for a handoff artifact. Lists use the declared order and the
scope/check structures are the versions defined in `task-contracts.md`.

~~~text
ContextHandoffV1 = {
  version: "context-handoff-v1",
  handoff_id: Token<128>,
  mode: continuation | independent,
  original_request: UserRequestV1,
  accepted_plan: PlanStepV1[],
  baseline: {
    branch: BoundedText<256>,
    head: Token<128>,
    status_digest: Token<128>
  },
  scope: ImpactScopeV1,
  acceptance_criteria: AcceptanceCriterionV1[],
  focused_checks: FocusedCheckV1[],
  checkpoint: CheckpointV1 | NONE,
  artifacts: ArtifactV1[],
  active_work: ActiveWorkV1[],
  open_risks: RiskV1[],
  decisions: DecisionV1[]
}
~~~

The following value types and cardinality limits apply; their canonical aliases are
defined in `task-contracts.md`. Unknown keys, duplicate IDs, invalid enum values, NUL
bytes, reserved sentinel tokens, and values over their limit are invalid:

~~~text
UserRequestV1 = BoundedText<4096>

PlanStepV1 = {
  step_id: Token<64>,
  status: planned | in_progress | done | blocked,
  summary: BoundedText<512>
}
accepted_plan: 0..32 PlanStepV1 values

AcceptanceCriterionV1 = {
  criterion_id: Token<64>,
  status: pending | met | blocked,
  summary: BoundedText<512>
}
acceptance_criteria: 0..32 AcceptanceCriterionV1 values
focused_checks: 0..32 existing FocusedCheckV1 values

CheckpointV1 = {
  checkpoint_id: Token<128>,
  status: PLAN_READY | CHECKPOINT_READY | VERIFICATION_READY |
          PARTIAL | INTEGRATION_PENDING | INTEGRATED,
  completed_milestone: BoundedText<512>,
  changed_paths: 0..128 ordered unique RepositoryPathV1 values,
  content_identity: Token<128> | NONE,
  next_action: BoundedText<512>
}

ArtifactV1 = {
  kind: Token<64>,
  artifact_id: Token<128>,
  artifact_path: BoundedText<512>,
  content_identity: Token<128>
}
artifacts: 0..32 ArtifactV1 values

ActiveWorkV1 = {
  run_id: Token<128>,
  task_id: Token<128>,
  attempt: integer 1..2147483647,
  invocation_id: Token<128>,
  role: researcher | planner | implementer | reviewer | verifier,
  state: PENDING | READY | CLAIMED | RUNNING | COMPLETED | VERIFIED |
         INTEGRATION_PENDING | INTEGRATED | ACCEPTED | NEEDS_INPUT | PARTIAL |
         BLOCKED | FAILED | CANCELLED | QUARANTINED,
  lock_scope: 0..128 ordered unique RepositoryPathV1 values,
  runtime_terminal_event_id: Token<128> | NONE
}
active_work: 0..32 ActiveWorkV1 values

RiskV1 = {
  risk_id: Token<64>,
  status: open | mitigated | accepted | blocked,
  summary: BoundedText<512>
}
open_risks: 0..16 RiskV1 values

DecisionV1 = {
  decision_id: Token<64>,
  status: pending | accepted | rejected,
  summary: BoundedText<512>
}
decisions: 0..16 DecisionV1 values
~~~

Every path in `changed_paths` and `lock_scope` follows the `ImpactScopeV1` repository-
relative, no-parent-traversal rules and is at most 512 bytes. Baseline fields are
identifiers or bounded metadata, not a copy of command output.

Mode-specific cardinality is explicit:

- `mode=independent` requires `checkpoint=NONE`, `artifacts=[]`, and
  `active_work=[]`. Existing runtime work stays in the ledger and is not copied into an
  unrelated task's handoff.
- `mode=continuation` requires exactly one `CheckpointV1`. `artifacts` may be empty for
  a plan or other checkpoint with `content_identity=NONE`; when the checkpoint names a
  content identity, `artifacts` must contain the matching artifact entry. `active_work`
  may contain only bounded runtime metadata for work relevant to the continuation.

The user request and accepted plan are bounded summaries, not a place for raw
conversation. Before serialization, omit or redact repository source, system/model
prompts, credentials, secrets, embeddings, model output, and unrelated conversation. Do
not silently truncate a value to fit a limit; shorten it into an accepted bounded summary
or report a capability gap. A validated report is referenced by its runtime/report
identity and structured outcome; do not copy its prose wholesale.

The runtime/session ledger remains authoritative for locks, cancellation, ownership,
deadlines, invocation binding, and terminal events. If no runtime store exists, a
task-specific temporary file outside the repository may preserve the same metadata, but
the persistence limitation must be explicit. A handoff file is evidence, not a lock,
authorization, review proof, or acceptance result.

## Capture and resume

1. Write the exact manifest to a task-specific temporary location outside the repository
   when a temporary artifact is appropriate. Keep it private where the platform allows,
   compute and record its content hash outside the file, and do not mutate it after
   handoff. Do not create an untracked repository file merely to imitate a task store.
2. Give the next invocation the manifest path/hash plus the original request and the
   bounded TaskSpec/context manifest. A path alone is not a substitute for explicit
   scope, acceptance criteria, or runtime binding.
3. In a fresh continuation, validate the manifest's schema, hash, task mode, Git
   baseline, actual changed paths, checkpoint/content identity, and active runtime
   state before editing or consuming a result. A mismatch requires re-planning or a
   blocked handoff; do not continue from stale text.
4. In an independent task, use a new TaskSpec and task identity. When the runtime
   supports it, request a non-forked fresh context; pass only the new request and its
   bounded manifest. Record dependencies explicitly instead of relying on inherited
   conversation.
5. Re-run the focused checks required by the new or resumed scope. Handoff alone never
   proves `CHECKPOINT_READY`, `VERIFIED`, `CLEAN`, acceptance, or authorization to
   commit/push/deploy.
6. Retain the handoff until all dependent work has consumed and validated it. Remove a
   temporary artifact only with an explicit, task-specific cleanup after runtime state,
   reports, and identities are captured; never clean broadly in response to silence or
   timeout.

## Capability failure

If the runtime cannot create a fresh invocation, bind its identity, persist task state,
or provide a reliable stop target, report the capability gap. A temporary manifest may
still help a user start a new session manually, but it does not make the missing runtime
guarantee true. Keep the affected task or review path blocked until the required identity,
stop, artifact, and acceptance proofs are available.
