# Task and Result Contracts (ContractV2)

[`contracts-v2.json`](contracts-v2.json) is the only authority for fields, enums,
limits, path rules, and digest domains. Query it instead of copying a schema into
an instruction file:

```bash
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind KIND
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --path DOTTED_PATH
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" validate --kind KIND FILE
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" digest --kind KIND FILE
```

The helper is fail-closed: it rejects V1/mixed records, unknown fields, malformed
JSON, invalid bounds, and inconsistent identities. It validates supplied records;
it does not observe a live runtime, start a Reviewer, or prove acceptance. Examples
are fictional shapes, never runtime evidence.

## Operational boundaries

Use `workflow_tool.py guide --topic roles` for generated role prompts and
`guide --topic workflow` for lifecycle/evidence generation. In every delegation:

- create and validate one `task_spec` with a `model-request-v2`;
- keep scope, write ownership, runtime identities, and acceptance parent-owned;
- let the generator bind snapshot/proof/digest fields from observations; and
- keep reports bounded metadata—no prompts, secrets, complete source, embeddings,
  or unbounded transcripts.

Only a strict runtime may authorize a strict writer. Portable delegated roles are
read-only; only an implementer may have a non-empty write scope. A Reviewer must
cover every declared review path, and a clean test result is not independent review.

For model selection, runtime events, canonicalization, status rules, or migration,
use the helper topic and the conditional reference routed by [`SKILL.md`](../SKILL.md).
