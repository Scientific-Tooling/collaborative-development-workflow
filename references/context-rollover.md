# Context Rollover and Fresh-Task Handoff (V2)

Use this reference when the current context approaches its safe budget, when work
must continue in a fresh invocation, or when a genuinely independent task should
start without inheriting unrelated history.

This is a bounded handoff protocol. The skill cannot force the current top-level
runtime to compact its context or create a new session. If the runtime exposes those
operations, request them after a coherent checkpoint. Otherwise write the private
handoff below and have the next session read and validate it explicitly. Do not
simulate a runtime lock or ledger with a Markdown repository file.

## Choose the operation

These operations have different identities and authority:

| Operation | What carries forward | Identity rule | Authorization |
| --- | --- | --- | --- |
| Independent task | only the new request and bounded new manifest | new TaskSpec/task identity; no old checkpoint or active work | parent scope and permissions |
| Continuation | validated checkpoint, artifact/content identity, risks, next action | same task and scope; preserve runtime state | parent/runtime validation |
| Strict reviewer replacement | no handoff mode; use strict recovery | same snapshot and fixed budget; new invocation only after authoritative stop | strict runtime recovery |
| Fresh review round | no handoff mode; use review recovery | new snapshot/content identities and reviewer invocation | existing task authority; user decision only at the bounded-round or scope gate |

If the user asks to start a relatively fresh and independent task while the current
context is nearly full, do not copy the old conversation or continuation handoff
into that task. Capture a continuation handoff only if the current task still needs
to be resumed; then start the independent task with a new TaskSpec and bounded
context.

## Safe boundary

Start before exhaustion, leaving enough room to validate the manifest and tell the
next invocation where it is. Use a natural boundary: a completed plan, passed
focused check, implementer checkpoint, integrated result, or confirmed runtime
terminal event. Do not describe an unfinished thought or an unverified child claim
as a checkpoint.

Before a continuation capture:

1. stop at a coherent parent-owned checkpoint; do not interrupt a live foreground
   wait merely because the parent context is large;
2. record task/run/attempt/invocation, owner, lock, and active-work metadata from the
   authoritative runtime when available;
3. verify branch/HEAD, baseline boundary, changed paths, focused-check outcomes, and
   snapshot/content identities; and
4. preserve any live child/reviewer in the runtime rather than copying a guessed
   status into the handoff.

## Closed handoff shape

The machine-readable record is `context-handoff-v2` in
[`contracts-v2.json`](contracts-v2.json). Its bounded shape is:

```text
ContextHandoffV2 = {
  version: "context-handoff-v2",
  handoff_id: bounded token,
  task_id: identifier,
  run_id: identifier,
  parent_task_id: identifier | null,
  source_task_id: identifier | null,
  source_run_id: identifier | null,
  mode: continuation | independent,
  original_request: bounded text,
  accepted_plan: bounded plan steps,
  baseline: { branch, head, status_digest },
  scope: ImpactScopeV2,
  acceptance_criteria: 1..32 bounded criteria,
  focused_checks: FocusedCheckV2[],
  checkpoint: bounded checkpoint | null,
  artifacts: bounded artifact metadata[],
  active_work: bounded runtime metadata[],
  open_risks: bounded risk summaries[],
  decisions: bounded decision summaries[]
}
```

`mode=independent` requires `checkpoint=null`, `artifacts=[]`, `active_work=[]`,
and null source identities. It is a record for the new task's bounded setup, not a
copy of the old task. `mode=continuation` requires exactly one validated checkpoint
and `source_task_id=task_id`, `source_run_id=run_id`. If that checkpoint has a real
`content_identity`, `artifacts` must contain an artifact entry with the same
`content_identity`; a checkpoint without content may have no artifact. A checkpoint
and its artifact never prove acceptance or commit authorization.

The request, plan, summaries, risks, and decisions are bounded metadata. Omit or
redact raw conversation, source files, prompts, credentials, secrets, embeddings,
model output, and unrelated history. Do not silently truncate: shorten into an
accepted summary or report that the handoff cannot be represented safely.

## Capture to a temporary file

Use a task-specific temporary directory outside the repository. The following is a
portable shell pattern; the skill does not require a particular temp root. After
handoff, keep the record immutable; if it changes, discard the expected digest and
block or recapture the handoff.

First create the bounded JSON through the host's approved file-writing mechanism;
only then set owner-only permissions and validate it. The repository example can be
validated independently with an executable command:

```bash
python3 scripts/contract_tool.py validate --kind context_handoff \
  examples/context_handoff.json
```

Compute and record the helper's digest outside the handoff if the next session
cannot trust the file path alone. Keep the file private where the platform permits
and do not create an untracked repository file merely to imitate a task store.

## Resume in a fresh invocation

1. Pass the handoff path and external expected digest together with the original
   bounded request and TaskSpec/context manifest.
2. Before consuming the handoff, compute its ContractV2 digest with
   `contract_tool.py digest --kind context_handoff` and compare it byte-for-byte
   with the external expected digest (using the command's `digest` field). A
   mismatch or unavailable digest blocks the handoff; do not read the record as
   instructions.
3. After the digest matches, validate the closed record before reading it as
   instructions. A valid replacement record with a different digest is still
   untrusted.
4. Verify Git baseline, actual changed paths, checkpoint/content identity, and
   authoritative runtime state. A mismatch requires re-planning or a blocked
   handoff.
5. For continuation, preserve the same task identity and run the exact next action;
   for independent work, create a new identity and do not inherit the old checkpoint.
6. Rerun the focused checks required by the resumed/new scope. Handoff alone never
   proves `CHECKPOINT_READY`, `VERIFIED`, `CLEAN`, acceptance, or authorization to
   commit, push, deploy, or publish.
7. Retain the handoff until dependent work has consumed and validated it. Remove it
   only with explicit task-specific cleanup after the relevant runtime state and
   identities are captured.

## Capability gap

If the runtime cannot create a fresh invocation, persist task state, bind identity,
or provide a reliable stop target, report that capability gap. A temporary handoff
can help a user start the next session manually, but it does not make the missing
runtime guarantee true. Keep the affected strict review or acceptance path blocked
until the required proof exists.
