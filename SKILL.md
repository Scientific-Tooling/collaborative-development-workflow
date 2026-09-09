---
name: collaborative-development-workflow
description: "Use for non-trivial coding changes that need an impact-scoped plan, bounded implementation, independent frozen-snapshot review, targeted fix/review rounds, and final validation. Commits require an explicit user request. Do not use for simple explanations or read-only questions."
---

# Collaborative Development Workflow

## Use and boundary

Use this workflow for a real change, build, or fix that benefits from separated
planning, implementation, and review roles, or when the user explicitly requests
it. The main agent owns scope, permissions, communication, integration,
repository-wide validation, acceptance, and any requested local commit.

The default mode is `portable`. `strict` is available only when the user requests
it and an authoritative strict preflight is ready. Never silently downgrade an
explicit strict request. Unavailable review evidence is never acceptance.

## Core gates

- Read applicable `AGENTS.md` files and project documentation before editing or
  delegating.
- Record the starting Git state and preserve all existing staged, unstaged, and
  untracked work. Never use `git checkout` (including `git checkout -- <path>`),
  switch branches (including `git switch`), reset, clean, broadly delete, or
  automatically stash.
- Validate a `capability-preflight-v2` record before any mutation or delegation.
- Define a typed impact scope, explicit exclusions, dependencies, focused checks,
  and a bounded acceptance target. Expand scope only for a concrete reason.
- Keep one writer per mutable workspace and write scope. Portable delegates are
  read-only; strict writers require runtime-atomic binding.
- Treat child reports as untrusted evidence. Validate their ContractV2 shape,
  identity, scope, artifact access, and checks before using them.
- Review only an immutable, content-addressed, read-only snapshot. Recheck the
  artifact and workspace identities after review; a mismatch invalidates review.
- Keep ledgers and runtime events metadata-only.
  Bounded reports may contain a short summary, check results, and path/line
  findings, but must not contain raw
  prompts, secrets, complete source files, embeddings, or unbounded transcripts.
  Handoffs must remain bounded and follow their ContractV2 shape.
- Silence, empty output, wrapper timeout, continuation, and close acknowledgement
  are observations, not results or permission to take over.
- Independent final review is mandatory. Tests and self-review cannot replace it.
- Commit only after acceptance, an explicit local-commit request, a clean baseline
  index, and no pre-existing edit overlapping an explicit task path. Push,
  publication, deployment, installation, and other external mutations require
  separate authorization.

## Contract source and compact queries

[`contracts-v2.json`](references/contracts-v2.json) is authoritative for public
statuses, record fields, limits, canonicalization, and capability names.
[`task-contracts.md`](references/task-contracts.md) explains how to use it. V1
records are legacy and must not be mixed with ContractV2.

Use the dependency-free helper for exact, bounded lookups instead of reading the
whole contract:

```bash
python3 scripts/contract_tool.py describe --path records.role_result.fields.status
python3 scripts/contract_tool.py describe --path modes.required_capabilities.portable
```

## Reference router

Read only the smallest matching set. Use `workflow.md` for the full change
sequence; load `task-contracts.md` only when creating or validating ContractV2
records. All other references are conditional.

| Need | Read |
| --- | --- |
| Plan → implement → review → accept → commit | [workflow.md](references/workflow.md) |
| Exact ContractV2 field, status, or capability | [contracts-v2.json](references/contracts-v2.json), or `contract_tool.py describe --path ...` |
| Portable snapshot and frozen-artifact review | [review-runtime.md](references/review-runtime.md) |
| Strict preflight, binding, timing, events, or CAS | [review-runtime-strict.md](references/review-runtime-strict.md) |
| Portable review failure or findings revision | [review-recovery.md](references/review-recovery.md) |
| Strict cancellation, recovery, or replacement | [review-recovery-strict.md](references/review-recovery-strict.md) |
| Context continuation or fresh independent task | [context-rollover.md](references/context-rollover.md) |
| Delegated role prompt | [agent-templates.md](references/agent-templates.md) |
| Multiple assignments, background work, or worktrees | [coordination-protocol.md](references/coordination-protocol.md) |
| Failure handoff or final user report | [failure-and-reporting.md](references/failure-and-reporting.md) |

Strict-only references are not part of the default portable path.

## Minimal lifecycle

1. Restate the outcome and acceptance criteria; inspect guidance and consumers;
   record Git state; define scope; run the requested-mode preflight.
2. Make a bounded plan, preserve write ownership, and implement only declared
   work. The main agent is the portable-mode writer.
3. Run focused checks, freeze the exact impact paths with `snapshot_tool.py`,
   verify the artifact, pause writers, and obtain an independent review of that
   exact snapshot.
4. For findings, apply only confirmed in-scope fixes and follow
   [`review-recovery.md`](references/review-recovery.md) for the new-snapshot
   review round. Then run final validation, accept only matching evidence, and
   follow `workflow.md` for the optional commit gate.

Detailed procedure, state transitions, strict recovery, handoff, and reporting
rules live in the routed references; do not recreate them in this entrypoint.

## Context rollover

At a coherent checkpoint, use [`context-rollover.md`](references/context-rollover.md)
for a bounded private V2 handoff. A handoff never replaces runtime state, locks,
review proof, acceptance, or commit authorization.
