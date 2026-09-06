# Workflow and Acceptance

Read this reference for the full plan → implement → review → accept → commit sequence.
Use task-contracts.md for schemas, review-runtime.md for runtime mechanics, and
review-recovery.md for blocked/replacement paths.

## 1. Analyze and establish scope

Before delegation or editing:

1. Restate the requested outcome and observable acceptance criteria.
2. Read applicable AGENTS.md files, project docs, likely paths, direct callers/consumers,
   mapped tests/configuration, and prescribed validation commands.
3. Record Git status, branch, HEAD, and the baseline boundary. Preserve all pre-existing
   staged, unstaged, and untracked work.
4. Define canonical impact scope, exclusions, risks, dependencies, and focused checks.
5. Report the analysis. Ask only for a material safety, behavior, compatibility, scope,
   cost, or authorization decision.

If required files overlap pre-existing edits, flag the overlap before changing them.

## 2. Plan

Use main-agent planning for small, clear, tightly coupled work. For broad, ambiguous,
high-risk, or cross-cutting work, use one bounded read-only planner or critic.

The accepted plan records:

- strategy and exact/likely paths;
- one TaskSpec per delegated assignment;
- dependency-aware milestones and each write/impact scope;
- exclusions, focused checks, validation owner, and risks;
- execution mode, isolation, common baseline, and integration order;
- material decisions with options, recommendation, and consequences;
- one prescribed full-validation command deferred to final acceptance.

A delegated planner is read-only and returns PLAN_READY, NEEDS_INPUT, PARTIAL, BLOCKED,
FAILED, or CANCELLED. Treat its report as evidence, not authority to expand scope. Pause
at the planning gate for an unanswered material user choice. Before any delegation read
task-contracts.md; before multiple/background/isolated assignments read
coordination-protocol.md.

## 3. Implement bounded milestones

Choose the least complex safe mode:

- Main agent for small/coupled work.
- One bounded implementer invocation per milestone for large/long-running work.
- Isolated parallel writers only for genuinely independent, disjoint slices with a
  common baseline and recorded integration order.

Pass the accepted TaskSpec and bounded context manifest. A writer edits only its declared
scope, preserves unrelated work, runs focused checks, and does not commit, push, deploy,
reset, clean, delete, or perform unrelated refactors. It must stop at a coherent
checkpoint and return CHECKPOINT_READY, PARTIAL, BLOCKED, NEEDS_USER_DECISION, FAILED, or
CANCELLED. The parent verifies the actual paths, identity, and checks before integrating.
Never assign repository-wide suites or a full production build to a child.

## 4. Freeze and review

Finish expected implementation and focused checks before freezing. Perform a cheap
pre-freeze self-check, then materialize the exact impact set as a read-only,
content-addressed artifact.

For Git, use a detached worktree or archive at the recorded tree/patch identity. For a
non-Git skill or loose files, copy the exact relative layout, including references/,
to a task-specific artifact; make files 0444 and directories 0555 and verify them.
The manifest lists every reviewed path, base identity, result identity, and per-file
hash. Keep SNAPSHOT_ID separate from ARTIFACT_PATH.

Pause all writers and main-agent edits while review runs. Use one integrated reviewer
for a small scope. Choose one fixed review_set before freeze only when broad scope needs
disjoint obligation lanes. A lane reviews only its assigned obligations; the parent owns
the aggregate proof. All review inputs and runtime budgets are defined in
review-runtime.md.

Give the reviewer the original request, accepted plan, baseline, exact scope/exclusions,
focused checks, immutable snapshot/artifact, and risk-bearing direct callers. Require
read-only inspection of the frozen diff and named impact paths. Wait in the foreground
as one logical wait; do not poll or inspect a moving input.

## 5. Fix and re-review

A validated CLEAN must match the exact frozen content identity. For FINDINGS:

1. stop the review and preserve its report/artifact;
2. apply only confirmed, in-scope fixes;
3. rerun affected focused checks;
4. recanonicalize scope if the fix expands it;
5. freeze a new snapshot and rerun every affected obligation, or every lane in the
   fixed review set, with the pinned reviewer profile by default.

Keep writers paused until the report and artifact are captured. Never carry CLEAN across
edits. A missing/no-report reviewer follows review-recovery.md and cannot silently
become a review set or a fresh budget. A fresh review round needs explicit authorization,
new run/set and snapshot identities, a separate budget, and an independent channel.

## 6. Independent acceptance

After the final frozen review is CLEAN:

1. inspect the complete task diff, status, staged boundary, and generated/secrets/debug
   changes; keep substantive inspection within the impact scope;
2. verify every acceptance criterion, exclusion, artifact identity, and coverage proof;
3. run relevant focused checks;
4. run the repository's prescribed full validation once, last, as the main agent;
5. recompute final workspace identity and compare it with the reviewed identity.

If any check fails, scope changes, proof is missing, timing is unverified, a lane is
blocked, or the identity differs, do not accept. Fix, refreeze, and repeat independent
review before full validation/commit as applicable. A test pass cannot substitute for
the required independent CLEAN.

## 7. Local commit

When commit is authorized and the workspace is Git-backed:

1. stage only explicit task paths, preserving baseline staged/untracked work;
2. inspect the staged file list and diff;
3. verify staged content matches the accepted reviewed identity;
4. create one focused local commit;
5. verify the commit summary and worktree.

If task and pre-existing edits overlap inseparably, stop and ask for direction. Never
push or deploy without explicit authorization.
