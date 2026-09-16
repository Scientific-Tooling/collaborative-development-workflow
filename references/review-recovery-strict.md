# Strict Review Recovery (V2)

Read only for an explicit strict run needing cancellation, timeout recovery,
quarantine, or replacement. Use:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic strict
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic recovery
```

Only the authoritative runtime may CAS a lifecycle row, issue the one idempotent
stop, confirm a runtime stop event, or open the single replacement slot. Retain
owner/lock on missing or mismatched stop proof; classify a Reviewer as
`REVIEW_BLOCKED`. Never infer that a child stopped, retry, replace, take over,
unlock, or accept from silence, timeout text, or a child status.

`NEEDS_INPUT`, `PARTIAL`, `FAILED`, `CANCELLED`, and `QUARANTINED` require the
runtime-owned identity and event rules in the strict guide. A replacement keeps
the same frozen snapshot and fixed deadline, is not a new review round, and is
never a second replacement. Acceptance and commit remain parent-owned.

Portable findings revision is in [`review-recovery.md`](review-recovery.md).
