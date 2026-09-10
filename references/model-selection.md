# Sub-agent Model Selection (V2)

Read this reference when selecting a model or reasoning effort for a delegated
task. [`contracts-v2.json`](contracts-v2.json) owns the closed machine shapes and
[`task-contracts.md`](task-contracts.md) owns their general use.

## Compatibility and ownership

Every newly created delegated `TaskSpecV2` must contain a `model-request-v2`
record. The field is optional in the ContractV2 schema only so records produced by
the 2.0.0 release remain valid; omission by a current workflow producer is an
error. `TaskSpec.model_profile` retains any profile already exposed at dispatch,
while `TaskSpec.model_request` records selection intent. `RoleResult.model_profile`
records the resolved profile and selection outcome.

The parent selects and validates the request. The runtime resolves it and owns any
authoritative model provenance. A child may repeat supplied provenance for
correlation, but it must not infer or repair a model identity, effort, or selection
outcome.

Concrete model IDs belong to the user's request, repository configuration, or
runtime/harness configuration. Do not hard-code a current provider model in this
skill's normative instructions.

## Selection precedence

Resolve selection policy before creating the TaskSpec, in this order:

1. an explicit user requirement;
2. an applicable repository role policy;
3. a bounded parent choice for this task; and
4. the model-neutral role defaults below.

Once recorded, the TaskSpec governs that invocation. Do not silently replace its
request because a different model is cheaper, faster, newer, or already active.

## Model request

The closed request is:

```text
ModelRequestV2 = {
  version: "model-request-v2",
  strategy: "explicit" | "inherit" | "runtime_default",
  requested_model: text | null,
  requested_effort: text | null,
  fallback: "fail" | "allow_runtime_default",
  reviewer_independence:
    "same_allowed" | "different_preferred" | "different_required",
  comparison_model: text | null
}
```

- `explicit` requires a non-sentinel `requested_model`. A null
  `requested_effort` leaves effort to runtime configuration.
- `inherit` is an intentional request for the parent's effective profile; it is
  never inferred from missing fields. Requested model and effort are null, and
  any exposed parent profile is recorded in `TaskSpec.model_profile` for matching.
- `runtime_default` intentionally delegates selection to the runtime. Requested
  model and effort are null, and `fallback` is `fail` because no second fallback
  target exists.
- `fail` blocks a successful result when an explicit selection is not honored or
  the selection outcome cannot be attested. `allow_runtime_default` permits an
  explicit or inherited request to fall back, but the result must disclose it.
- Non-reviewer tasks use `same_allowed` with `comparison_model=null`.

Strict explicit selection always uses `fallback=fail`. If the authoritative
runtime cannot bind the requested selection, stop before model execution; do not
downgrade the request or claim strict readiness.

## Resolved provenance

A successful delegated review with `model_request` records all of these in
`RoleResult.model_profile`:

```text
{
  model: text,
  effort: text,
  selection_outcome: "honored" | "fallback" | "unknown"
}
```

Recorded model and effort values are exact, nonempty tokens with no surrounding
whitespace. Use a canonical sentinel such as `unknown` when provenance is not
exposed; padded values are malformed, not another spelling of unknown provenance.

Use `unknown` for a model or effort that the runtime does not expose. This is a
disclosure, not proof that a named requirement or model-diversity requirement was
met. The selection outcome has these meanings:

- `honored`: the requested strategy was used;
- `fallback`: the runtime default was used under an allowed fallback; and
- `unknown`: the runtime did not expose whether selection was honored.

For an explicit request with `selection_outcome=honored`, the resolved model and
any requested effort must match exactly. `fallback` is valid only when the request
allows it. A known inherited dispatch profile must likewise match an honored
result. `unknown` cannot satisfy `fallback=fail`.

## Model-neutral role defaults

These defaults describe capability intent; the parent or runtime maps them to an
available configured profile.

| Role | Default strategy | Capability intent |
| --- | --- | --- |
| planner | `inherit` | balanced general reasoning appropriate to scope and risk |
| researcher | `inherit` | source analysis appropriate to the bounded question |
| implementer | `inherit` | coding ability and effort proportional to risk; strict mode only for delegated writes |
| verifier | `runtime_default` | efficient deterministic checking, strengthened when diagnosis is required |
| reviewer | `runtime_default` | strongest suitable reasoning and code-review profile available under policy |

All non-reviewer defaults use `same_allowed`. Ordinary reviews also use
`same_allowed`. For high-risk changes, use `different_preferred` when the runtime
can expose the primary authoring model. Use `different_required` only when the
user, repository, or high-assurance policy requires it.

## Reviewer model independence

Model diversity supplements the mandatory fresh, read-only reviewer context and
immutable artifact; it never replaces either one. `comparison_model` identifies
the primary authoring model used for the integrated change.

- `same_allowed` requires `comparison_model=null`.
- `different_preferred` requires a known comparison model. A same or unknown
  resolved reviewer model remains visible but does not by itself block portable
  acceptance.
- `different_required` requires a known comparison model, `fallback=fail`, and a
  known resolved reviewer model that differs from it. A different effort on the
  same model does not count as a different model.

An explicit request for a different reviewer cannot name the comparison model.
`inherit` cannot satisfy `different_required`.

## Failure and review rounds

If a required selection cannot be honored before spawn, do not launch a substitute
silently. Report the bounded blocked or user-decision state appropriate to the
role. If a delivered successful reviewer result violates the request, classify its
evidence as `REVIEW_BLOCKED`; it cannot support acceptance or a commit.

The model request is immutable throughout findings-driven review rounds. A changed
selection policy starts a newly authorized task/evidence bundle. The runtime may
resolve a different concrete model between rounds only when that still honors the
unchanged request.
