# Coordination Protocol (V2)

Read only for two or more assignments, background work, isolated worktrees, or
work that outlives the current turn. Use the executable policy:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic coordination
```

The parent owns scope, integration, full validation, acceptance, and lock release.
Give each task one owner, baseline, dependency set, typed scopes/exclusions,
focused checks, model request, and mutable workspace. A child result is evidence,
not authorization. Portable delegates are read-only; strict writers require
runtime binding.

For parallel writers, use disjoint write scopes, isolated worktrees, a common
baseline, and no unreviewed shared dependency. Serialize shared contracts,
migrations, generated files, and overlapping callers. Integrate checkpoints in
recorded order, create a new integrated identity, and use one integrated Reviewer;
partial review lanes cannot combine into `CLEAN`. Silence or timeout never
authorizes retry, takeover, unlock, cleanup, or state inference.

Use [`workflow.md`](workflow.md) for the common preflight and
[`review-runtime-strict.md`](review-runtime-strict.md) for strict lifecycle.
