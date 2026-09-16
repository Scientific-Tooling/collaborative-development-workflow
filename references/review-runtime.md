# Portable Review and Frozen Snapshots (V2)

Read this only for portable Reviewer setup or snapshot guarantees. Use the
single executable policy source for details and commands:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic review
python3 "$CDW_SKILL_DIR/scripts/snapshot_tool.py" --help
```

The relevant ContractV2 paths are:

```text
modes.required_capabilities.portable
artifact_contracts.snapshot_manifest
```

Portable review is Linux-only. Complete live preflight first; keep the main agent
as the only writer; select the Reviewer budget before freezing; freeze exactly
`review_paths` outside the repository; and record both content and manifest
identities. Use one fresh identifiable Reviewer in an effectively read-only
sandbox and one uninterrupted foreground wait for the selected protected window.

Read-only blocks writes but not necessarily reads. Record
`artifact_only_read_enforcement` truthfully and do not claim exclusive artifact
access without a mount or read allowlist. Silence, timeout, empty output, wrapper
yield, and close acknowledgement are not review results. The parent validates
scope, identities, access, coverage, checks, model provenance, and workspace state;
`REVIEW_UNAVAILABLE` and `REVIEW_BLOCKED` cannot be accepted.

Strict rules are separate in [`review-runtime-strict.md`](review-runtime-strict.md).
