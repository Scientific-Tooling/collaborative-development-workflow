# Strict Review Runtime and Lifecycle (V2)

Read only for an explicit `strict` request. The executable policy is:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic strict
```

Portable snapshot mechanics remain in [`review-runtime.md`](review-runtime.md);
record shapes remain in [`contracts-v2.json`](contracts-v2.json). The strict
contract paths are:

```text
modes.required_capabilities.strict_additional
records.runtime_completion_event
records.runtime_terminal_event
records.runtime_stop_event
records.runtime_event_sequence
```

Strict must stop before mutation without an outside authoritative preflight. The
bundled helper cannot attest `STRICT_READY` or `ACCEPTED_STRICT`, and portable
evidence cannot become strict proof. The runtime must bind identity, target, model,
budget, timing, owner/lock, and result atomically; use complete-row CAS and
runtime-authored events; and retain the lock until terminal/stop evidence and the
artifact are captured or quarantined. Never infer stop, success, or replacement
from silence or an agent status string.

Use [`review-recovery-strict.md`](review-recovery-strict.md) for cancellation,
quarantine, and replacement. Findings-driven revision remains in
[`review-recovery.md`](review-recovery.md).
