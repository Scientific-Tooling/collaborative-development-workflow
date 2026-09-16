# Failure Handling and User Reporting (V2)

Read for blocked/unavailable review, handoff, final reporting, or a requested local
commit:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic report
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic recovery
```

Public statuses and record shapes remain in [`contracts-v2.json`](contracts-v2.json).
Do not infer a status from silence, timeout, wrapper output, or close acknowledgement.
No child or recovery path may push, publish, deploy, install, or change an external
system.

The user report should say what changed, important checks, independent-review
conclusion, acceptance or its practical reason, remaining risks/decisions, and a
commit only when explicitly requested and created. Keep IDs, hashes, model
provenance, exact commands, rounds, and full records in the optional evidence
attachment. Never describe validated-but-unaccepted work as complete or commit-ready.
