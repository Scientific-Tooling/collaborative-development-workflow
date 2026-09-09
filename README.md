# Collaborative Development Workflow

A Codex skill for running scoped, multi-agent software changes with explicit
planning, single-writer boundaries, immutable review snapshots, fail-closed
review recovery, focused validation, and local commits.

The skill is designed for coding tasks where delegated agents can improve
confidence, but where the parent agent must retain responsibility for scope,
integration, repository-wide validation, acceptance, and the final commit.

## What it provides

- Impact-scoped planning and task contracts
- Bounded planner, implementer, verifier, and reviewer prompts
- One-writer workspace discipline and isolated-worktree guidance
- Content-addressed, read-only review snapshots
- Runtime-bound reviewer reports and coverage proofs
- Bounded context rollover and fresh-task handoffs
- Explicit handling for timeouts, silence, cancellation, partial work, and
  replacement reviewers
- Fail-closed acceptance when independent review evidence is unavailable
- Progressive disclosure through focused reference documents

## Install for Codex

Clone the skill directly into the Codex skills directory:

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/Scientific-Tooling/collaborative-development-workflow.git \\
  ~/.codex/skills/collaborative-development-workflow
```

If the directory already exists, update it with:

```bash
git -C ~/.codex/skills/collaborative-development-workflow pull --ff-only
```

Then invoke it explicitly with:

```text
$collaborative-development-workflow
```

The skill may also be copied into another filesystem-discovered skills
directory supported by the agent runtime.

## Runtime expectations

The workflow is instruction-level guidance. Its strongest guarantees depend on
runtime features for isolated workspaces, foreground agent waits, immutable
artifact access, runtime identity binding, cancellation/stop confirmation, and
compare-and-set task state. A runtime that cannot provide those anchors should
report the affected delegation as blocked instead of treating silence or a
passing test suite as independent review approval.

The default configuration is conservative: delegated checks stay focused, the
main agent owns full validation, and commits are local unless the user
explicitly authorizes a push or other external mutation.

## Repository layout

```text
SKILL.md                         Entry point and routing rules
agents/openai.yaml               Codex UI metadata
references/task-contracts.md     Typed assignments and result envelopes
references/review-runtime.md     Binding, timing, identity, and CAS rules
references/review-recovery.md    Timeout, cancellation, and replacement rules
references/workflow.md           End-to-end lifecycle and acceptance gates
references/agent-templates.md    Delegated role prompt templates
references/failure-and-reporting.md
references/coordination-protocol.md
references/context-rollover.md       Context-limit handoff and fresh-task protocol
```

Detailed references are loaded only when their operation requires them, keeping
ordinary skill invocations small while retaining the full coordination protocol
for high-risk cases.

Context rollover uses a bounded, parent-owned manifest. Runtime task identity,
ownership, locks, active waits, stop confirmation, review proof, and acceptance
remain authoritative outside that temporary artifact.

## Validate locally

From this repository's parent directory, run the bundled Codex skill validator:

```bash
python3 /path/to/codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /path/to/collaborative-development-workflow
```

The validator checks frontmatter, naming, and unfinished scaffolding. It does
not replace behavioral review of the workflow or validation of a host runtime's
agent lifecycle semantics.

## Scope and safety

This is an unofficial, runtime-oriented workflow layer. It does not replace a
repository's own instructions, test suite, access controls, or deployment
process. It must not be used to infer user authorization for publishing,
deploying, or changing external services.

## License

MIT. See [LICENSE](LICENSE).
