# Portable Review and Frozen Snapshots (V2)

Read this only for portable reviewer setup, snapshot guarantees, or troubleshooting.
Snapshot publication is Linux-only. The common preflight, normal commands,
reviewer invocation, and final acceptance gates are owned by
[`workflow.md`](workflow.md). Strict binding, timing, events, and recovery are in
[`review-runtime-strict.md`](review-runtime-strict.md) and
[`review-recovery-strict.md`](review-recovery-strict.md).

Relevant contract sources:

```text
contracts-v2.json:
  modes.required_capabilities.portable
  artifact_contracts.snapshot_manifest
```

## Reviewer boundary

Complete the live preflight in `workflow.md` before editing or delegating. The main
agent remains the only portable writer. `read_only_enforcement` proves a write
boundary only; `artifact_only_read_enforcement` separately states whether a
container mount or read allowlist limits readable paths. `none` or `unknown` may
support portable acceptance, but never a claim of exclusive artifact access or
confidentiality.

[`../assets/cdw-reviewer.toml`](../assets/cdw-reviewer.toml) is a custom-agent
template. With user authorization, copy it to `.codex/agents/cdw-reviewer.toml` in
the project or `$HOME/.codex/agents/cdw-reviewer.toml` for personal use. A Skill
cannot install or activate it. Select its configured name, `cdw_reviewer`, not its
filename. Confirm the effective `sandbox_mode="read-only"` and
`approval_policy="never"`; parent overrides can change them, and any writable
override or approved escalation invalidates review. Its request to avoid the live
workspace is guidance, not a read boundary. Keep secrets out of readable paths.

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

Use a shell variable so placeholders cannot become redirections. Record both
emitted identities. The helper derives manifest version, entry status and type,
artifact prefixes, baseline-only types, and bounds from
`artifact_contracts.snapshot_manifest`.

The helper enforces a top-level Git worktree and at most 128 normalized
`review_paths`. It records present and deleted entries; content hashes, modes, and
symlink targets; scoped Git-HEAD bytes under `baseline/`; and post-state bytes
under `files/`. A source rename is a deletion plus an addition. It never follows
symlinks or traversal paths. It bounds file, total, and Git output; pins source
traversal and Git queries to one repository descriptor; disables replacement
objects and lazy fetches; and rejects races or unavailable descriptor operations.

Sanitized Git identity records attached branches as full refs and reserves
`DETACHED` for a detached HEAD. It combines the stage inventory with a raw staged
diff, including intent-to-add, and records deleted and untracked inventories.
Exact scoped bytes, modes, and symlink targets bind tracked worktree state. The
helper does not call porcelain status or Git worktree conversion, so clean/process
filters do not run.

Artifacts are owner-only (`0500` directories and `0400` files). Creation verifies
a same-parent staging tree before publishing it with Linux
`renameat2(RENAME_NOREPLACE)`, and fails closed when atomic no-clobber publication
is unavailable.

## Review and acceptance handoff

Pause writers and follow the review and acceptance steps in
[`workflow.md`](workflow.md). Give one fresh
reviewer the bounded task and exact verified artifact. `fork_turns="none"` removes
inherited conversation but does not narrow filesystem reads. The parent validates
the result and creates the artifact-access and review-coverage records, recomputes
their digests, and links them with the TaskSpec and result in one review round.

Contract validation shows only that supplied records agree. It does not open the
snapshot, observe a reviewer, prove sandbox settings or preflight freshness, or
compare the workspace. `workflow_tool.py accept` performs the artifact, run, and
scoped-workspace checks, but still leaves the live confirmations to the parent.
Neither a standalone outcome nor a valid `acceptance-evidence-v2` record is proof
by itself.

No usable terminal result is `REVIEW_UNAVAILABLE`. A delivered result with invalid
identity, scope, artifact access, or coverage is `REVIEW_BLOCKED`. Neither accepts
the work. Portable evidence cannot prove strict conflict-safe updates,
authoritative stop, or runtime-authored events.

## Strict boundary

Strict mode is opt-in and must stop before mutation unless its authoritative
preflight is ready. Read [`review-runtime-strict.md`](review-runtime-strict.md)
before any strict spawn or lifecycle decision. Portable evidence cannot be upgraded
to strict proof.
