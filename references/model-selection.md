# Sub-agent Model Selection (V2)

Read when choosing a delegated model, effort, fallback, or reviewer diversity.
The executable policy and role defaults are:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic model
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind model_request
```

Every delegated TaskSpec carries `model-request-v2`; the runtime owns the resolved
`model_profile`. Apply precedence: explicit user requirement, repository policy,
bounded parent choice, then model-neutral defaults. Never hard-code a provider
model or silently substitute a disallowed fallback. A child may copy supplied
provenance but cannot infer or repair it.

`same_allowed` is the ordinary default. `different_preferred` supplements fresh
context and a frozen artifact when the author model is known; `different_required`
needs a known comparison model, `fallback=fail`, and a known different resolved
reviewer model. Diversity never replaces independent context, read-only isolation,
or complete snapshot coverage.
