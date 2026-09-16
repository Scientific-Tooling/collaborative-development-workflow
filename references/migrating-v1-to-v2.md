# Migrate older records to ContractV2

Read only for migration. Use the current executable checklist and query shapes
from the authority:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic migration
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind KIND
```

Rebuild closed records; do not copy V1 fields, old digests, or old canonicalization
domains. Validate each record, separate `mode` from `binding_mode`, add current
scope/model/budget/identity/proof fields, recanonicalize paths and lists, and
recompute digests. Strict readiness and acceptance still require an outside
authoritative runtime. Examples show shapes only and are not reusable evidence.
