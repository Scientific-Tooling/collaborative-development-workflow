---
name: collaborative-development-workflow
description: "Use on Linux for non-trivial coding changes that need scoped implementation, validation, and independent review of a frozen snapshot. Commits require an explicit user request. Do not use for simple explanations or read-only questions."
---

# Collaborative Development Workflow

## Boundary

Use for changes needing separate planning, implementation, and review. The main
agent owns scope, integration, validation, acceptance, and permissions.

Default `portable` mode publishes snapshots only on Linux and requires an
identifiable, enforced-read-only reviewer. `strict` requires an explicit request
and authoritative outside preflight; bundled helpers cannot attest it. Never
downgrade strict or treat missing review as acceptance.

Read-only may still expose files. Record the actual
`artifact_only_read_enforcement`; without a mount or allowlist, do not claim
exclusive artifact access or confidentiality.

## Non-negotiable gates

- Read applicable `AGENTS.md` files and project guidance before editing or
  delegating.
- Record Git state and preserve all existing work. Never switch branches, reset,
  clean, automatically stash, discard with `git checkout`, or broadly delete.
- Before editing or delegating, complete [`workflow.md`](references/workflow.md)'s
  live preflight and final scope. Saved records are not live checks.
- Put `model-request-v2` in every delegation; use `workflow.md` defaults and never
  silently substitute.
- Keep one writer per workspace and write scope. The main agent is the portable
  writer; portable delegates are enforced read-only; strict writers require strict
  runtime binding.
- Validate every child report's shape, identity, scope, artifact access, checks,
  and model provenance before use.
- After focused and full checks, review only a verified immutable snapshot covering
  all `review_paths`. Pause writers, then recheck both identities and the scoped
  workspace; any mismatch invalidates review.
- Select `cdw_reviewer` and confirm read-only sandboxing with approvals disabled.
  `fork_turns="none"` removes chat history, not file access.
- Keep bounded metadata only. Reports may contain summaries, checks, and path/line
  findings, but not prompts, secrets, complete source, embeddings, or transcripts.
- Silence, empty output, timeout, continuation, and close acknowledgement are not
  results or permission to retry, replace, take over, or unlock.
- Independent final review is mandatory. Tests and self-review cannot replace it.
  Contract validation checks supplied data; it does not prove a live preflight,
  spawn, sandbox, artifact read, or review. Complete `workflow.md`'s live checks.
- Commit only after acceptance, an explicit request, a clean starting index, and no
  pre-existing overlap. External changes need separate authorization.

## Contract and tools

[`contracts-v2.json`](references/contracts-v2.json) is authoritative;
[`task-contracts.md`](references/task-contracts.md) explains it. Never mix V1/V2 or
use examples as evidence.

Set `CDW_SKILL_DIR` from this Skill's absolute path, not the target repository.
`workflow.md` owns helper commands and limits; helper success is not acceptance.

## Router

Start every change with [`workflow.md`](references/workflow.md), then read only the
matching conditional references.

| Condition | Read or use |
| --- | --- |
| Create or validate V2 records | [`task-contracts.md`](references/task-contracts.md), [`contracts-v2.json`](references/contracts-v2.json), or `contract_tool.py` |
| Reviewer setup, snapshot guarantees, or troubleshooting | [`review-runtime.md`](references/review-runtime.md) |
| Strict requested | [`review-runtime-strict.md`](references/review-runtime-strict.md) before mutation; [`review-recovery-strict.md`](references/review-recovery-strict.md) for recovery |
| Portable findings, malformed review, or unavailable review | [`review-recovery.md`](references/review-recovery.md) |
| Customize or fall back from prompt helper | [`agent-templates.md`](references/agent-templates.md) |
| Override model, effort, fallback, strict selection, or diversity | [`model-selection.md`](references/model-selection.md) |
| Multiple assignments, background work, or worktrees | [`coordination-protocol.md`](references/coordination-protocol.md) |
| Commit requested or blocked handoff | [`failure-and-reporting.md`](references/failure-and-reporting.md) |
| Context rollover or fresh independent task | [`context-rollover.md`](references/context-rollover.md) |
| Convert older records | [`migrating-v1-to-v2.md`](references/migrating-v1-to-v2.md) |

A `context-handoff-v2` aids continuation but never replaces state, locks, review
proof, acceptance, or commit authorization.
