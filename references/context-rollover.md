# Context Rollover and Fresh-Task Handoff (V2)

Read when context is nearly full, work continues in a fresh invocation, or an
independent task must start without unrelated history:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" guide --topic rollover
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind context_handoff
```

Stop only at a parent-owned completed checkpoint and never during a protected
Reviewer wait. An independent task gets a new identity and no old checkpoint,
artifact, or active work. A continuation carries one validated checkpoint with
matching task/run/content identities. A fresh review round gets new snapshot,
content, and invocation identities under recovery rules.

Keep the handoff bounded metadata only—no conversation, source, prompts, secrets,
embeddings, or transcript. Validate its digest, shape, Git baseline, scope,
checkpoint, identities, and runtime state before resuming. A handoff never proves
`CLEAN`, acceptance, or commit authority. If runtime support is missing, report the
gap and keep the affected path blocked.
