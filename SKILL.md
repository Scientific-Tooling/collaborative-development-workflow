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
- Record Git state and preserve existing work. Never switch branches, reset,
  clean, automatically stash, discard with `git checkout`, or broadly delete.
- Before editing or delegating, complete [`workflow.md`](references/workflow.md)'s
  live preflight and final scope; saved records are not live checks.
- Define typed impact/write scopes, exclusions, dependencies, and focused checks.
- Put `model-request-v2` in every delegation; follow `workflow.md` defaults and
  never silently substitute a model.
- Select the Reviewer execution budget before freezing and bind all values in its
  TaskSpec. Use Extended by default; wait once in the foreground for the full
  protected window. Do not poll, interrupt, cancel, or roll over context while it
  is live; an incapable host must report unavailable or blocked review.
- Keep one writer per workspace and write scope. Portable delegates are enforced
  read-only; strict writers require runtime-atomic binding.
- Validate child reports, identities, scope, artifact access, coverage, checks,
  and model provenance before using them.
- Review only a verified immutable snapshot covering every `review_paths` entry;
  pause writers and recheck identities and workspace afterward.
- Select `cdw_reviewer` and confirm read-only sandboxing with approvals disabled.
  `fork_turns="none"` removes chat history, not file access.
- Keep bounded metadata only. Reports may contain summaries, checks, and path/line
  findings, but not prompts, secrets, complete source, embeddings, or transcripts.
- Silence, empty output, timeout, continuation, and close acknowledgement are not
  results or permission to retry, replace, take over, or unlock.
- Independent final review is mandatory; self-review cannot replace it.
- Commit only after acceptance, an explicit request, a clean starting index, and
  no pre-existing overlap. External changes need separate authorization.

## Efficiency defaults

- Use the fast path for at most five explicit files in one component with
  deterministic criteria and no API/schema/generated/integration/security or
  concurrency/lifecycle risk; the main agent plans, writes, checks, and delegates
  only one independent Reviewer.
- For broad/high-risk changes, add only roles resolving concrete uncertainties;
  do not duplicate validation or split acceptance lanes.
- Batch discovery/checks, freeze one snapshot per round, and send a bounded packet.

## Contract/tools

[`contracts-v2.json`](references/contracts-v2.json) is authoritative;
[`task-contracts.md`](references/task-contracts.md) explains it. Never mix V1/V2
or use examples as evidence. Set `CDW_SKILL_DIR` from this Skill's absolute path;
`workflow.md` owns helper commands and limits.

## Router

Start every change with [`workflow.md`](references/workflow.md), then read only
the matching conditional references.

| Condition | Read or use |
| --- | --- |
| Create or validate V2 records | [`task-contracts.md`](references/task-contracts.md), [`contracts-v2.json`](references/contracts-v2.json), or `contract_tool.py` |
| Reviewer setup or snapshot guarantees | [`review-runtime.md`](references/review-runtime.md) |
| Strict requested | [`review-runtime-strict.md`](references/review-runtime-strict.md) before mutation; [`review-recovery-strict.md`](references/review-recovery-strict.md) for recovery |
| Portable findings or unavailable review | [`review-recovery.md`](references/review-recovery.md) |
| Delegated role prompt | [`agent-templates.md`](references/agent-templates.md) |
| Model, effort, fallback, or reviewer diversity | [`model-selection.md`](references/model-selection.md) |
| Multiple assignments, background work, or worktrees | [`coordination-protocol.md`](references/coordination-protocol.md) |
| Final report or blocked handoff | [`failure-and-reporting.md`](references/failure-and-reporting.md) |
| Context rollover or fresh independent task | [`context-rollover.md`](references/context-rollover.md) |
| Convert older records | [`migrating-v1-to-v2.md`](references/migrating-v1-to-v2.md) |

## Minimal lifecycle

1. Restate the outcome, inspect guidance and consumers, record Git state, define
   scope, run preflight, and resolve each delegation's model request.
2. Plan and implement with one writer, then run focused and full validation.
3. Choose and bind the Reviewer budget, freeze and verify the exact snapshot, pause
   writers, and obtain one uninterrupted independent review of that snapshot.
4. For findings, fix only confirmed in-scope issues, rerun validation, freeze a
   new snapshot, and obtain complete coverage again. Accept only after identity,
   evidence, and workspace checks pass.

At checkpoints, use [`context-rollover.md`](references/context-rollover.md) for a
bounded handoff; it never replaces runtime state, locks, review proof, acceptance,
or commit authorization.
