---
name: collaborative-development-workflow
description: "Use on Linux for non-trivial coding changes that need scoped implementation, validation, and independent review of a frozen snapshot. Commits require an explicit user request. Do not use for simple explanations or read-only questions."
---

# Collaborative Development Workflow

Use for non-trivial Linux changes needing scoped implementation, validation, and
independent review of a frozen snapshot. The main agent owns scope, integration,
acceptance, and permissions. Do not use for explanations or read-only questions.

## Hard gates

- Read applicable `AGENTS.md` and repository guidance before editing or delegating.
- Record Git state and preserve existing work. Never switch branches, reset, clean,
  broadly delete, or discard pre-existing changes.
- Start with [`workflow.md`](references/workflow.md): complete live preflight and
  final scope before any edit or spawn. Saved records/examples are not live proof.
- Every delegation has a validated `model-request-v2`; never silently substitute a
  model. Keep one writer per workspace and declared write scope.
- Use `portable` by default: identifiable enforced-read-only Reviewer, exact
  verified snapshot, and one uninterrupted protected wait. `strict` is explicit-only,
  needs an outside authoritative runtime, and never downgrades.
- Validate child identity, scope, artifact access, coverage, checks, and model
  provenance. Review every `review_paths` entry from the frozen artifact; pause
  writers and recheck the workspace afterward.
- Silence, empty output, timeout, wrapper yield, and close acknowledgement are not
  results or permission to retry, replace, take over, unlock, or accept.
- Generate evidence from observed records with `workflow_tool.py`; keep hashes, IDs,
  and record details in its optional attachment. The parent still does live checks.
- Commit only after acceptance, an explicit user request, a clean starting index,
  and no pre-existing overlap. External changes need separate authorization.

## Single source of operational guidance

Do not copy policy, templates, budgets, or record fields into prompts or notes.
Use the helper for the topic you need:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --list
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic TOPIC
```

`workflow_tool.py` owns lifecycle and role guidance; `contract_tool.py` owns the
closed schema; `snapshot_tool.py` owns snapshot mechanics. The machine source of
truth is [`contracts-v2.json`](references/contracts-v2.json). Read only the
conditional reference named below when needed.

## Router

Always read [`workflow.md`](references/workflow.md) first.

| Need | Read or use |
| --- | --- |
| V2 record shape, validation, or digest | [`task-contracts.md`](references/task-contracts.md), `contract_tool.py` |
| Portable snapshot or Reviewer | [`review-runtime.md`](references/review-runtime.md), `guide --topic review` |
| Explicit strict request | [`review-runtime-strict.md`](references/review-runtime-strict.md), `guide --topic strict` |
| Findings or unavailable review | [`review-recovery.md`](references/review-recovery.md), [`review-recovery-strict.md`](references/review-recovery-strict.md) |
| Delegation | [`agent-templates.md`](references/agent-templates.md), `workflow_tool.py prompt` |
| Model or diversity | [`model-selection.md`](references/model-selection.md), `guide --topic model` |
| Multiple assignments or worktrees | [`coordination-protocol.md`](references/coordination-protocol.md), `guide --topic coordination` |
| Context rollover | [`context-rollover.md`](references/context-rollover.md), `guide --topic rollover` |
| Report, blocked handoff, or commit | [`failure-and-reporting.md`](references/failure-and-reporting.md), `guide --topic report` |
| V1 migration | [`migrating-v1-to-v2.md`](references/migrating-v1-to-v2.md), `guide --topic migration` |

## Minimal lifecycle

1. Restate the outcome, inspect guidance/consumers, record Git state, define typed
   scope/checks, run preflight, and resolve delegation models.
2. Plan and implement with one writer; keep noisy command output in files and
   inspect bounded excerpts instead of pasting complete diffs or logs.
3. At each parent-owned completed checkpoint, inspect `/status` and use `/compact`
   when context is high (when available); never compact during a protected
   Reviewer wait. Use a side task or fresh subagent for unrelated work.
4. Choose the Reviewer budget, freeze and verify the exact snapshot, pause writers,
   and wait once for one fresh independent review.
5. For findings, fix only confirmed in-scope issues and repeat validation, snapshot,
   and complete review coverage. Accept only after evidence and live checks.

At a safe checkpoint, use [`context-rollover.md`](references/context-rollover.md).
A handoff never replaces runtime state, locks, review proof, acceptance, or commit
authorization.
