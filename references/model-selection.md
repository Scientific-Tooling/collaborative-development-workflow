# Sub-agent Model Selection (V2)

Use this when choosing a model or reasoning effort for a delegated task.
[`contracts-v2.json`](contracts-v2.json) owns the record shapes, and
[`task-contracts.md`](task-contracts.md) explains their general use.

## Ownership and precedence

Every delegated TaskSpec includes a `model-request-v2`. Its `model_request`
records intent, its dispatch `model_profile` records any profile already exposed,
and the result profile records what the runtime resolved.

The parent selects and validates the request. The runtime owns authoritative
provenance. A child may repeat supplied values for correlation, but must not infer
or repair model, effort, or selection outcome. Concrete model IDs come from the
user, repository, or runtime configuration; do not hard-code a current provider
model in this Skill.

Apply these sources in order:

1. explicit user requirement;
2. repository role policy;
3. bounded parent choice for this task;
4. the model-neutral defaults below.

Once recorded, the request controls that invocation. Do not replace it because a
different model is cheaper, faster, newer, or already active.

## Request and resolution

Inspect and validate the request instead of copying its fields:

```bash
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind model_request
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" validate \
  --kind model_request /absolute/path/to/model-request.json
```

- `explicit` requires a named model. A null effort leaves that choice to runtime
  configuration.
- `inherit` deliberately requests the parent's effective profile. It is never
  inferred from missing fields; record any known parent profile for matching.
- `runtime_default` deliberately lets the runtime choose. It uses `fallback=fail`
  because there is no second fallback target.
- `fail` blocks success when a requested selection is not honored or cannot be
  attested. `allow_runtime_default` permits a fallback but requires the result to
  disclose it.
- Non-reviewer tasks use `same_allowed` with no comparison model.

Strict explicit selection always uses `fallback=fail`. If the runtime cannot bind
it, stop before model execution; do not substitute another model or claim strict
readiness.

A successful delegated review records exact, nonempty `model`, `effort`, and
`selection_outcome` values with no surrounding whitespace. Use the canonical
`unknown` value when model or effort is not exposed. Outcomes mean:

- `honored`: the requested strategy was used;
- `fallback`: the runtime default was used under an allowed fallback;
- `unknown`: the runtime did not reveal whether it honored the request.

For an honored explicit request, model and any requested effort must match
exactly. An honored inherited request must match a known dispatch profile.
`fallback` is valid only when allowed, and `unknown` cannot satisfy
`fallback=fail`.

## Model-neutral defaults

The parent or runtime maps these capability goals to an available profile:

| Role | Strategy | Capability goal |
| --- | --- | --- |
| planner | `inherit` | balanced reasoning suited to scope and risk |
| researcher | `inherit` | source analysis suited to the question |
| implementer | `inherit` | coding and effort proportional to risk; delegated writes only in strict mode |
| verifier | `runtime_default` | efficient checking, with stronger diagnosis when needed |
| reviewer | `runtime_default` | strongest suitable review reasoning available under policy |

All non-reviewer defaults use `same_allowed`, as do ordinary reviews. For a
high-risk change, use `different_preferred` when the primary authoring model is
known. Use `different_required` only when the user, repository, or assurance
policy requires it.

## Reviewer independence

Model diversity supplements the required fresh, read-only reviewer context and
immutable artifact; it never replaces them. `comparison_model` names the primary
authoring model.

- `same_allowed` has no comparison model.
- `different_preferred` requires a known comparison model. The same or an unknown
  reviewer model remains visible but does not alone block portable acceptance.
- `different_required` requires a known comparison model, `fallback=fail`, and a
  known resolved reviewer model that differs from it. A different effort on the
  same model does not count.

An explicit different reviewer cannot name the comparison model, and `inherit`
cannot satisfy `different_required`.

## Failure and review rounds

If a required selection cannot be honored before spawn, do not silently launch a
substitute. Report the bounded blocked or user-decision state allowed for the
role. If a delivered successful reviewer result violates the request, classify
its evidence as `REVIEW_BLOCKED`; it cannot support acceptance or commit.

Keep the model request unchanged through findings-driven review rounds. A policy
change starts a newly authorized task and evidence bundle. The runtime may resolve
a different concrete model in a later round only when it still honors the
unchanged request.
