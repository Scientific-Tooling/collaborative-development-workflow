# Workflow and Acceptance (V2)

Read this first for every change. The detailed, executable policy is in one
place:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic workflow
```

Set `CDW_SKILL_DIR` to the absolute directory containing this Skill, not the target
repository. Use `contract_tool.py` for record shape and `snapshot_tool.py` for
snapshot mechanics; do not reproduce their fields or guarantees in prompts.

## Required order

1. Inspect applicable `AGENTS.md`, project guidance, consumers, tests, and checks;
   record Git state and preserve pre-existing changes.
2. Finalize the outcome, criteria, typed impact/write scopes, exclusions,
   dependencies, and focused/full checks. Run live preflight before editing or
   delegating; saved records are not live checks.
3. Use `workflow_tool.py init` for starter records outside the repository, validate
   the final TaskSpec, and use `workflow_tool.py prompt` for any delegation.
4. Implement with one writer, validate, freeze the exact `review_paths` outside the
   repository, and verify both emitted snapshot identities.
5. Pause writers and obtain one fresh, enforced-read-only Reviewer with its selected
   protected budget and one foreground wait. Re-review a new snapshot after every
   confirmed in-scope fix.
6. After review, use `workflow_tool.py generate` to assemble
   `acceptance-evidence-v2` from observations, then run `accept` with the live run
   ID. The generated report is user-facing; the evidence attachment holds internals.

## Acceptance boundary

The generator binds proofs and digests but cannot observe reviewer invocation,
sandbox enforcement, preflight freshness, or post-freeze immutability. The parent
must complete those live checks, inspect the complete diff/status, and confirm
criteria, scope, checks, identities, and review coverage. Any scoped mutation after
freeze requires new validation and a new snapshot. `REVIEW_UNAVAILABLE` and
`REVIEW_BLOCKED` are `NOT_ACCEPTED`; strict acceptance requires an outside
authoritative runtime. Commit only after acceptance and explicit user request.

For conditional details, use the matching reference from [`SKILL.md`](../SKILL.md)
and `workflow_tool.py guide --topic TOPIC`.
