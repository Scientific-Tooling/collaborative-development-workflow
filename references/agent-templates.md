# Agent Prompt Guidance (V2)

Do not hand-write role templates. Validate the final TaskSpec, then render the
bounded prompt:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" prompt \
  --task-spec /absolute/path/to/task-spec.json
```

The JSON response contains the task, role-specific limits, permitted statuses, and
the required `role-result-v2` shape. The prompt rules are owned by
`workflow_tool.py`; inspect them with:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic roles
```

Supported ContractV2 roles are `role: planner`, `role: researcher`,
`role: implementer`, `role: verifier`, and `role: reviewer`. The contract remains
authoritative in [`contracts-v2.json`](contracts-v2.json). Add only task-specific
repository instructions and live runtime facts; never weaken the generated rules.

Portable delegates are read-only and portable implementers are rejected by the
helper. Children return one JSON object, preserve arrays when empty, and never
invent runtime-owned identity, timing, artifact, coverage, or model facts.
