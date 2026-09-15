# Migrating older records to current ContractV2

ContractV2 is fail-closed. Do not mix V1 objects, 2.0.0 record shapes, old field
names, or digests from earlier shapes and canonicalization domains with current
records. The helpers intentionally do not provide a permissive compatibility mode
or automatic converter.

## Required changes

1. Rebuild each object from the current closed shape reported by
   `python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind KIND`; do not
   copy unknown V1 fields.
2. Use lowercase schema field names, a structured `model_profile`, and an
   `execution-budget-v2` object. Add a validated `model-request-v2` to each
   delegated TaskSpec and record the resolved profile in its result; see
   [`model-selection.md`](model-selection.md).
3. Separate operating `mode` (`portable` or `strict`) from `binding_mode`
   (`transport_bound_provisional` or `runtime_atomic`). Portable delegated tasks
   have an empty write scope.
4. Add `impact_scope.review_paths`. Keep `changed_paths` limited to paths intended
   for integration. Ensure `review_paths` covers them and every path-shaped caller,
   consumer, test, or configuration reference, plus supporting material needed by
   the reviewer.
5. Record both `snapshot_id` (equal to the manifest identity) and
   `content_identity`. Pass both back to the bundled snapshot helper's `verify`
   and `compare` commands.
6. Replace opaque proof claims with `artifact-access-proof-v2`,
   `review-coverage-proof-v2`, and `review-round-v2` records. Validate acceptance
   through one `acceptance-evidence-v2` bundle, not a standalone workflow outcome.
7. Represent user commit intent separately from `commit_status`. An unsuccessful
   workflow may preserve requested intent as `BLOCKED`; a created commit requires
   `CREATED` and a real commit ID.
8. Use runtime completion, terminal, and stop event records only when the runtime
   actually supplies their identities and monotonic timestamps.
9. Add the current `run_id`, truthful `read_only_enforcement`, and truthful
   `artifact_only_read_enforcement` to the capability preflight.
   `custom_agent_sandbox` is valid only when that custom agent is installed,
   selected, and confirmed effective; otherwise use `unverified`. A read-only
   sandbox does not prove artifact-only reads; use `none` for a known shared
   readable filesystem or `unknown` when the read boundary was not checked.
10. Recanonicalize set-like paths and lists, then recompute every record digest.

The bundled contract validator can confirm that supplied portable records are
consistent. Run the bundled workflow helper's `accept` command with the required
`--expected-run-id` for the live artifact and review-scope checks, and separately
confirm the reviewer sandbox and invocation. Strict
readiness and strict acceptance require an external authoritative runtime adapter;
changing an `authority` string is not proof.

Use the complete [portable acceptance example](../examples/acceptance_evidence.json)
only as a fictional shape reference. It is not reusable runtime evidence. Validate
each migrated record before relying on it.
