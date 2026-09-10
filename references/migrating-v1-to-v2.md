# Migrating V1 records to ContractV2

ContractV2 is fail-closed. Do not mix V1 objects, old field names, or digests from
earlier canonicalization domains with current records. The helpers intentionally
do not provide a permissive compatibility mode or automatic converter.

## Required changes

1. Rebuild each object from the current closed shape reported by
   `contract_tool.py describe --kind KIND`; do not copy unknown V1 fields.
2. Use lowercase schema field names, a structured `model_profile`, and an
   `execution-budget-v2` object. Current producers also add a validated
   `model-request-v2` to each delegated TaskSpec and record the resolved profile
   in its result. The field remains schema-optional only so 2.0.0 TaskSpecs keep
   validating; see [`model-selection.md`](model-selection.md).
3. Separate operating `mode` (`portable` or `strict`) from `binding_mode`
   (`transport_bound_provisional` or `runtime_atomic`). Portable delegated tasks
   have an empty write scope.
4. Add `impact_scope.review_paths`. Keep `changed_paths` limited to paths intended
   for integration, while `review_paths` also contains callers, consumers, tests,
   configuration, and supporting material needed by the reviewer.
5. Record both `snapshot_id` (equal to the manifest identity) and
   `content_identity`. Pass both back to `snapshot_tool.py verify` and `compare`.
6. Replace opaque proof claims with `artifact-access-proof-v2`,
   `review-coverage-proof-v2`, and `review-round-v2` records. Validate acceptance
   through one `acceptance-evidence-v2` bundle, not a standalone workflow outcome.
7. Represent user commit intent separately from `commit_status`. An unsuccessful
   workflow may preserve requested intent as `BLOCKED`; a created commit requires
   `CREATED` and a real commit ID.
8. Use runtime completion, terminal, and stop event records only when the runtime
   actually supplies their identities and monotonic timestamps.
9. Recanonicalize set-like paths and lists, then recompute every record digest.

The bundled validator can confirm portable evidence. Strict readiness and strict
acceptance require an external authoritative runtime adapter; changing an
`authority` string is not attestation.

Use the complete [portable acceptance example](../examples/acceptance_evidence.json)
as a shape reference and validate each migrated record before relying on it.
