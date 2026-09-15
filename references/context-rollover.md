# Context Rollover and Fresh-Task Handoff (V2)

Use this protocol when the current context is nearly full, work must continue in a
fresh invocation, or an independent task should start without unrelated history.
The Skill cannot force context compaction or create a session. If the runtime
cannot do that, write the private handoff below and have the next session validate
it. Do not imitate a runtime lock or ledger with a repository Markdown file.

## Choose the operation

| Operation | Carries forward | Identity and authority |
| --- | --- | --- |
| Independent task | New request and bounded manifest only | New task identity; parent scope and permissions |
| Continuation | Validated checkpoint, identities, risks, next action | Same task and scope; runtime validates and preserves state |
| Strict reviewer replacement | No handoff; use strict recovery | Same snapshot and budget; start only after an authoritative stop |
| Fresh review round | No handoff; use review recovery | New snapshot, content, and invocation identities; existing task authority and its user-decision gates |

If independent work starts while the current task remains unfinished, capture a
continuation handoff for the old task separately. Do not copy that handoff or the
old conversation into the new task.

## Stop at a safe boundary

Leave enough context to validate and locate the handoff. Stop after a completed
plan, passed focused check, implementer checkpoint, integrated result, or confirmed
runtime terminal event. Never present an unfinished thought or unverified child
claim as a checkpoint.

Before a continuation capture:

1. Reach a parent-owned checkpoint without interrupting a live foreground wait.
2. Record task, run, attempt, invocation, owner, lock, and active-work facts from
   the authoritative runtime when available.
3. Verify branch and HEAD, baseline boundary, changed paths, focused checks, and
   snapshot and content identities.
4. Leave live children and reviewers in the runtime; do not guess their state in
   the handoff.

## Create the record

Inspect the authoritative shape rather than copying its fields:

```bash
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe \
  --kind context_handoff
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" validate \
  --kind context_handoff \
  "$CDW_SKILL_DIR/examples/context_handoff.json"
```

Independent mode requires no checkpoint, artifacts, active work, or source
identities. It describes the new bounded task; it is not a summary of the old
one. Continuation mode requires exactly one validated checkpoint and matching
task and run source identities. If that checkpoint has a real `content_identity`,
include one artifact entry with the same identity. Neither a checkpoint nor its
artifact proves acceptance or grants commit authority.

Keep only bounded request, plan, summary, risk, decision, and artifact metadata.
Do not include raw conversation, source files, prompts, credentials, secrets,
embeddings, model output, or unrelated history. If the record does not fit its
limits, write a shorter accepted summary or report that it cannot be represented;
never silently truncate it.

Create the JSON through an approved file-writing method in a task-specific
temporary directory outside the repository. Set owner-only permissions, validate
it, and keep it unchanged. If it changes, discard the expected digest and block
or recapture it. Keep the digest outside the handoff when the next session cannot
trust the path alone.

## Resume

1. Pass the handoff path, its external expected digest, the original bounded
   request, and the TaskSpec or context manifest.
2. Before treating the record as instructions, compute its digest and compare the
   returned `digest` byte-for-byte with the external value:

   ```bash
   python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" digest \
     --kind context_handoff /absolute/path/to/handoff.json
   ```

   A missing or mismatched digest blocks the handoff. A valid replacement with a
   different digest is still untrusted.
3. After the digest matches, validate the closed record.
4. Verify the Git baseline, actual changed paths, checkpoint and content identity,
   and authoritative runtime state. Re-plan or block on any mismatch.
5. Continue with the same task identity and exact next action, or create a new
   identity for independent work without inheriting the old checkpoint.
6. Rerun the focused checks required by the scope. A handoff never proves
   `CHECKPOINT_READY`, `VERIFIED`, `CLEAN`, acceptance, or permission to commit,
   push, deploy, or publish.
7. Keep the handoff until dependent work has validated and consumed it. Remove it
   only through explicit task-specific cleanup after preserving needed runtime
   state and identities.

## Missing runtime support

If the runtime cannot create a fresh invocation, persist state, bind identity, or
provide a reliable stop target, report that gap. A temporary handoff can help the
user resume manually, but it cannot supply the missing guarantee. Keep the
affected strict review or acceptance path blocked.
