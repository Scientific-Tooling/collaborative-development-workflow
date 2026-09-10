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

Run required validation first, then freeze and verify the exact review scope. Use a
shell variable so angle-bracket placeholders cannot be parsed as redirection.

```bash
TASK_ID=task-1
ARTIFACT="/tmp/cdw-review-${TASK_ID}"
python3 scripts/snapshot_tool.py create REPOSITORY \
  --scope scope.json --output "$ARTIFACT"
python3 scripts/snapshot_tool.py verify "$ARTIFACT" --scope scope.json \
  --expected-content-identity CONTENT_IDENTITY \
  --expected-manifest-identity MANIFEST_IDENTITY
```

The helper derives manifest version, entry status/type values, artifact prefixes,
baseline-only types, and bounded counts from
`contracts-v2.json: artifact_contracts.snapshot_manifest`. `snapshot_tool.py`
owns filesystem rules: sanitized Git identity, top-level worktree enforcement,
normalized `review_paths`, present/deleted entries,
hashes, modes, symlink targets, scoped `baseline/` Git-HEAD bytes, and post-state
`files/` bytes. It never follows symlinks or traversal paths, bounds file, total,
and Git output, pins source traversal and Git queries to one repository descriptor,
disables replacement objects and lazy fetches, and never calls porcelain status or
Git worktree content conversion. Its Git identity encodes attached branches as full
refs (reserving `DETACHED` for detached HEAD), combines the stage inventory with a
non-converting raw staged diff that distinguishes intent-to-add, and records
deleted/untracked path inventories. Exact scoped bytes, modes, and symlink targets
bind tracked worktree state. Repository clean/process filters are therefore not
executed. Queries are bounded, races are rejected, and the helper fails closed when
the required descriptor surface is unavailable.
Artifacts are outside the repository and owner-only (`0500` directories, `0400`
files); creation publishes a verified same-parent staging tree with Linux
`renameat2(RENAME_NOREPLACE)`. It fails closed on platforms without that atomic
no-clobber primitive. A source rename is deletion plus addition.

Give the reviewer the bounded request, accepted plan, scope, exclusions, checks,
artifact path, and identities. Pause writers before requesting fresh context with
`fork_context=false` when supported. After the result:

```bash
python3 scripts/snapshot_tool.py compare REPOSITORY "$ARTIFACT" \
  --scope scope.json \
  --expected-content-identity CONTENT_IDENTITY \
  --expected-manifest-identity MANIFEST_IDENTITY
```

`compare` exit `0` is an exact scoped artifact/workspace match, `1` is an identity
mismatch, and `2` is invalid input or artifact. A missing usable terminal result is
`REVIEW_UNAVAILABLE`; a delivered result whose identity, scope, artifact access, or
coverage proof cannot be validated is `REVIEW_BLOCKED`. Neither outcome is
accepting. Portable evidence does not prove strict CAS, authoritative stop, or
runtime-authored event semantics.

The parent materializes structured artifact-access and review-coverage proofs,
recomputes their digests, and binds them with the TaskSpec/result in a review-round
record. A standalone outcome is not proof; portable acceptance requires the full
`acceptance-evidence-v2` record.

## Strict mode boundary

Strict mode is opt-in and must stop before mutation unless its authoritative
preflight is ready. Read [`review-runtime-strict.md`](review-runtime-strict.md)
before any strict spawn or strict lifecycle decision; portable evidence cannot be
upgraded into strict proof.
