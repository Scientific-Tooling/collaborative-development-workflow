# Review Recovery and Revision (V2)

Read for portable review failure or findings-driven revision. Use the executable
policy source:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic recovery
```

After the protected Reviewer window, no usable result is
`NOT_ACCEPTED` / `REVIEW_UNAVAILABLE`; malformed substantive output gets at most
one format-only correction from the same reviewer. Identity, scope, artifact,
coverage, stale, or late problems are `REVIEW_BLOCKED`. Neither disposition
authorizes retry, replacement, takeover, unlock, or commit.

For `FINDINGS`, keep the old evidence, fix only confirmed in-scope issues, rerun
affected and full checks, freeze a new snapshot, and obtain fresh complete review
coverage. Keep objective, criteria, checks, scope, and model request unchanged.
The public contract allows at most three total rounds; a third actionable finding
or any product/scope/policy decision pauses for user authorization.

Strict stop and replacement recovery is separate in
[`review-recovery-strict.md`](review-recovery-strict.md).
