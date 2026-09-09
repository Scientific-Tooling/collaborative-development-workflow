---
name: collaborative-development-workflow
description: "Use for non-trivial coding changes that need an impact-scoped plan, bounded implementation, independent frozen-snapshot review, targeted fix/review rounds, and final validation. Commits require an explicit user request. Do not use for simple explanations or read-only questions."
---

# Collaborative Development Workflow

## Purpose and boundary

Use this workflow for a real change, build, or fix that benefits from separated
planning, implementation, and review roles, or when the user explicitly requests
it. The main agent owns scope, communication, integration, repository-wide
validation, acceptance, and any requested local commit.

The workflow has two explicit modes:

| Mode | Selection | Review gate | Outcome when review is unavailable |
| --- | --- | --- | --- |
| `portable` | default | independent reviewer on a frozen snapshot, with parent-verified hashes and checks | `NOT_ACCEPTED` / `REVIEW_UNAVAILABLE`; no commit |
| `strict` | user explicitly requests it and the preflight is `STRICT_READY` | portable evidence plus runtime binding, CAS, timing, terminal-event, stop, and artifact proofs | strict mode stops before mutation if any required capability is absent |

Never silently downgrade an explicit strict request. A handoff may preserve
validated work, but it does not turn an unavailable review or missing strict proof
into acceptance.

## Always-active contract

- Read applicable `AGENTS.md` files and project documentation before editing or
  delegating.
- Record the starting Git state. Preserve existing edits, staged changes, and
  untracked files. Never use reset, checkout, clean, broad deletion, or an
  automatic stash.
- Run and record a capability preflight before any mutation. The result is a
  `capability-preflight-v2` record validated by `contracts-v2.json`.
- Define a typed impact scope: changed paths, direct callers/consumers, mapped
  tests or configuration, and explicit exclusions. Expand it only for a concrete
  dependency, acceptance criterion, or reproduced failure.
- Keep one writer per mutable workspace and write scope. In portable mode all
  delegated roles are read-only; the main agent is the sole writer. Strict writers
  additionally require runtime-atomic binding.
- Treat child reports as untrusted evidence. Validate their ContractV2 shape,
  identity, scope, artifact access, and checks before acting on them.
- Review only an immutable, content-addressed, read-only snapshot. Recheck the
  artifact and workspace identities after review; any mismatch invalidates the
  review result.
- Keep ledgers and runtime events metadata-only. Bounded reports may contain a
  summary and path/line findings; never store raw prompts, secrets, complete source
  files, or unbounded model transcripts. Scoped source may exist only inside a
  private review artifact.
- A wait observation is not a result. Silence, an empty response, wrapper timeout,
  continuation, or close acknowledgement does not mean failure, success,
  cancellation, or permission to replace or take over.
- When context approaches its safe remaining budget, roll over only at a coherent
  checkpoint using [context-rollover.md](references/context-rollover.md). A handoff
  artifact never replaces runtime state, locks, active waits, review proof, or
  acceptance.
- Independent final review is mandatory before acceptance. Tests and self-review
  cannot substitute for a usable independent reviewer result.
- Commit only after an accepted outcome and an explicit commit request. If baseline
  staged changes exist, decline the commit and hand off; do not perform index
  surgery. Never push, publish, deploy, or install without separate authorization.

## Contract and capability source of truth

All public statuses, record fields, limits, canonicalization rules, and capability
names come from [contracts-v2.json](references/contracts-v2.json). The readable
explanation is [task-contracts.md](references/task-contracts.md). V1 names and
schemas in older installations are legacy and are not accepted by the V2 helpers;
do not mix V1 and V2 records.

Portable preflight requires all of:

1. identifiable read-only subagents;
2. terminal result delivery for the selected invocation; and
3. shared access to the frozen snapshot artifact.

Strict preflight additionally requires atomic spawn binding, CAS state, verified
monotonic timing, runtime-authored completion and stop events, exact stop targets,
and immutable artifact proofs. A strict capability record must be authoritative to
the runtime, not merely asserted by a child or guessed from a status string.

The model and effort are not hard-coded. Record the actual profile when the runtime
exposes it; otherwise use the permitted unknown value and report that limitation.

## Selective references

Read only the smallest matching set. This entrypoint is the shared router; a
reference owns its details and should not be copied into another file.

| Trigger | Read |
| --- | --- |
| Ordinary delegation, TaskSpec, result, scope digest, or status | [task-contracts.md](references/task-contracts.md) |
| Exact field lookup or ContractV2 modification | [contracts-v2.json](references/contracts-v2.json); use `python3 scripts/contract_tool.py describe --kind ...` or `--section ...` for a bounded query |
| Snapshot, portable review, strict binding, timing, event, or CAS | [review-runtime.md](references/review-runtime.md) |
| Wait continuation, timeout, cancellation, findings loop, or strict replacement | [review-recovery.md](references/review-recovery.md) |
| Context near its safe limit, temporary handoff, or fresh independent task | [context-rollover.md](references/context-rollover.md) |
| Full plan → implement → review → accept → commit sequence | [workflow.md](references/workflow.md) |
| Planner/researcher/implementer/verifier/reviewer prompt | [agent-templates.md](references/agent-templates.md) |
| Two or more assignments, background work, or isolated worktrees | [coordination-protocol.md](references/coordination-protocol.md) |
| Failure handoff or final user report | [failure-and-reporting.md](references/failure-and-reporting.md) |

## Lifecycle

### 1. Scope and preflight

Restate the requested outcome and observable acceptance criteria. Read repository
guidance, inspect likely paths and direct consumers, record Git state, define the
typed impact scope and exclusions, and choose focused checks. Then select
`portable` or an explicitly requested `strict` mode and validate the capability
preflight before editing or spawning. A missing strict capability is a pre-mutation
block, not a portable fallback.

### 2. Plan

Use direct planning for small, clear, coupled work. Otherwise use one bounded,
read-only planner or critic. Record dependencies, milestones, write ownership,
focused checks, validation owner, risks, exclusions, mode, and material decisions.
Pause for an unanswered user decision when it changes safety, behavior, scope, cost,
or authorization.

### 3. Implement

The main agent writes in portable mode. A strict implementer may write only after
runtime-atomic binding and must use its declared scope. Delegated read-only roles
may plan, research, verify, or review a frozen artifact. No child commits, pushes,
deploys, installs, resets, cleans, deletes, or performs unrelated work.

### 4. Freeze and review

Run focused checks, create a scoped snapshot with `snapshot_tool.py`, and verify the
artifact before review. Start a fresh reviewer context with `fork_context=false`
when the tool exposes that choice. Pause all writers and main-agent edits while the
review runs. Re-verify artifact and workspace identity after the result arrives.

Portable review is accepted as evidence only when the reviewer returns a usable
ContractV2 result covering the exact snapshot and the parent verifies hashes, scope,
focused checks, and full validation. Strict review additionally requires all
runtime-owned proofs.

### 5. Resolve findings

For `FINDINGS`, apply only targeted, in-scope fixes, rerun affected checks, create a
new snapshot, and rerun the complete affected review obligation. Ordinary
findings-driven revision rounds do not require new user authorization. Allow two
fix/review rounds; a third round requires explicit user authorization.

`REVIEW_UNAVAILABLE` means no usable independent reviewer result was delivered.
`REVIEW_BLOCKED` means a result exists but its identity, scope, artifact, timing, or
required proof cannot be validated. In portable mode either outcome is
`NOT_ACCEPTED` and no commit. Strict replacement/recovery is allowed only under the
strict rules in `review-recovery.md`, after an authoritative stop confirmation.

### 6. Accept and validate

The main agent verifies the final diff, exact reviewed identity, exclusions, focused
checks, and repository-prescribed full validation. A clean test run alone is not an
independent review. Produce `ACCEPTED_PORTABLE` only when the portable review and
validation evidence match; produce `ACCEPTED_STRICT` only when strict runtime proofs
also match. Otherwise produce `NOT_ACCEPTED` with a ContractV2 reason.

### 7. Commit or hand off

If the user explicitly requested a local commit, acceptance is complete, and the
baseline index was clean, stage only the explicit task paths, inspect the staged
diff, verify it matches the accepted identity, create one focused commit, and verify
the result. With any pre-existing staged changes, decline the commit and hand off
the accepted-but-uncommitted result. Push and other external mutations always need a
separate explicit request.

## Context rollover in one sentence

The runtime may compact or start a fresh top-level context, but the skill cannot
force that operation; at a coherent checkpoint it can write a bounded private V2
handoff to a task-specific temporary file so the next invocation can validate and
resume it explicitly.
