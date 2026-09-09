# Portable Review Runtime and Frozen Artifacts (V2)

Read this reference for the default portable snapshot and review path. ContractV2
owns machine shapes and closed fields. Strict-only binding, timing, event, and CAS
rules are in [`review-runtime-strict.md`](review-runtime-strict.md).

The relevant contract sources are:

```text
contracts-v2.json:
  modes.required_capabilities.portable
  artifact_contracts.snapshot_manifest
```

## Portable snapshot and review

Portable mode is the default. Before editing or delegating, validate a
`capability-preflight-v2` record. It is ready only when the parent can select an
identifiable read-only reviewer, receive a terminal result, and share one frozen
artifact path. The parent remains the only writer and validates identity, scope,
evidence, and final workspace state.

Freeze and verify the exact impact scope:

```bash
python3 scripts/snapshot_tool.py create REPOSITORY \
  --scope scope.json --output /tmp/cdw-review-<task>
python3 scripts/snapshot_tool.py verify /tmp/cdw-review-<task> --scope scope.json
```

The helper derives manifest version, entry status/type values, artifact prefixes,
baseline-only types, and bounded counts from
`contracts-v2.json: artifact_contracts.snapshot_manifest`. `snapshot_tool.py`
owns filesystem rules: Git identity, normalized scope, present/deleted entries,
hashes, modes, symlink targets, scoped `baseline/` Git-HEAD bytes, and post-state
`files/` bytes. It never follows symlinks or traversal paths, rejects races, and
fails closed when descriptor-relative POSIX operations are unavailable. Artifacts
are outside the repository and owner-only (`0500` directories, `0400` files);
verification rehashes the manifest and copied files. A rename is deletion plus
addition.

Give the reviewer the bounded request, accepted plan, scope, exclusions, checks,
artifact path, and identities. Request fresh context with `fork_context=false`
when supported, then pause writers. After the result:

```bash
python3 scripts/snapshot_tool.py compare REPOSITORY /tmp/cdw-review-<task> \
  --scope scope.json
```

`compare` exit `0` is an exact artifact/workspace match, `1` is an identity
mismatch, and `2` is invalid input or artifact. A missing usable terminal result is
`REVIEW_UNAVAILABLE`; a delivered result whose identity, scope, artifact access, or
coverage proof cannot be validated is `REVIEW_BLOCKED`. Neither outcome is
accepting. Portable evidence does not prove strict CAS, authoritative stop, or
runtime-authored event semantics.

## Strict mode boundary

Strict mode is opt-in and must stop before mutation unless its authoritative
preflight is ready. Read [`review-runtime-strict.md`](review-runtime-strict.md)
before any strict spawn or strict lifecycle decision; portable evidence cannot be
upgraded into strict proof.
