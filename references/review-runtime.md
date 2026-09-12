# Portable Review and Frozen Snapshots (V2)

Read this only for portable reviewer setup, snapshot guarantees, or
troubleshooting. Snapshot publication is Linux-only; common preflight, normal
commands, invocation, and acceptance gates live in [`workflow.md`](workflow.md).
Strict lifecycle rules live in [`review-runtime-strict.md`](review-runtime-strict.md)
and [`review-recovery-strict.md`](review-recovery-strict.md).

Relevant contract sources:

```text
contracts-v2.json:
  modes.required_capabilities.portable
  artifact_contracts.snapshot_manifest
```

## Reviewer boundary and budget

Complete the live preflight before editing or delegating. Portable mode requires an
identifiable enforced-read-only reviewer, terminal result delivery, one frozen
artifact path, and a protected Reviewer wait. The main agent remains the only
writer. `read_only_enforcement` protects writes; `artifact_only_read_enforcement`
records whether a mount or allowlist also limits readable paths. Do not claim
exclusive artifact access without that enforcement.

Choose the Reviewer profile before freezing and copy all three values into the
TaskSpec's `execution-budget-v2` object:

| Profile | Use when | `wall_clock_seconds` | `max_turns` | `max_output_bytes` |
| --- | --- | ---: | ---: | ---: |
| Standard | genuinely small, low-risk, tightly coupled review | `7200` | `64` | `262144` |
| Extended | default; broad, cross-cutting, high-risk, or uncertain review | `10800` | `128` | `524288` |

These are ceilings, not work quotas. The Reviewer may return when exact coverage is
complete, but the runtime must not replace a selected profile with a smaller cap.
After start, use one foreground blocking wait for the same invocation. Do not set a
shorter wrapper timeout, poll, inspect moving files, edit, roll over context, or
send `close`/`cancel`/`interrupt` before a terminal result or protected deadline.
Only authoritative binding failure, a safety incident, or explicit user cancellation
is an earlier exception. A host unable to honor this contract is not ready.

At the deadline, preserve the artifact and diagnostics and report
`REVIEW_UNAVAILABLE` if no usable result exists. Never infer a result from silence,
empty output, timeout text, or a close acknowledgement. Strict mode may begin its
runtime-owned stop/recovery path only after the deadline or an actual binding
failure, and only after confirmed stop may it consider replacement.

## Create and verify the snapshot

After validation, freeze exactly the declared review scope outside the repository:

```bash
TASK_ID=task-1
ARTIFACT="/tmp/cdw-review-${TASK_ID}"
python3 "$CDW_SKILL_DIR/scripts/snapshot_tool.py" create /absolute/path/to/repository \
  --scope /absolute/path/to/task-scope.json --output "$ARTIFACT"
python3 "$CDW_SKILL_DIR/scripts/snapshot_tool.py" verify "$ARTIFACT" \
  --scope /absolute/path/to/task-scope.json \
  --expected-content-identity CONTENT_IDENTITY \
  --expected-manifest-identity MANIFEST_IDENTITY
```

Record both emitted identities. The helper enforces a top-level Git worktree and at
most 128 normalized `review_paths`; records present/deleted entries, exact bytes,
modes, symlink targets, scoped Git-HEAD bytes, and post-state bytes. A rename is a
deletion plus an addition. It never follows symlinks or traversal paths, bounds
file/total/Git output, pins queries to one repository descriptor, disables
replacement objects and lazy fetches, and rejects races or unavailable descriptor
operations.

Git identity uses full attached refs and reserves `DETACHED` for detached HEAD.
Stage inventory plus raw staged diff preserves intent-to-add, deleted, and untracked
inventories without porcelain status or worktree conversion. Artifacts are outside
the repository and owner-only (`0500` directories, `0400` files); publication uses
Linux `renameat2(RENAME_NOREPLACE)` and fails closed when unavailable.

## Review handoff

Give one fresh reviewer the bounded request, accepted plan, scope, exclusions,
checks, artifact path, identities, and validated model request. Pause writers first;
`fork_turns="none"` removes inherited conversation but does not narrow filesystem
reads. The reviewer should batch reads and focused checks, then return a bounded
`role-result-v2` when exact coverage is complete.

The parent validates result identity, scope, artifact access, coverage, checks, and
model provenance; it recomputes artifact-access and review-coverage proof digests
and links them with the TaskSpec/result in one review round. Contract validation only
checks supplied records; it does not observe live preflight, sandbox, reviewer, or
workspace state. `REVIEW_UNAVAILABLE` and `REVIEW_BLOCKED` are never accepting.

## Strict boundary

Strict mode is opt-in and must stop before mutation unless its authoritative
preflight is ready. Read [`review-runtime-strict.md`](review-runtime-strict.md)
before any strict spawn or lifecycle decision; portable evidence cannot become
strict proof.
