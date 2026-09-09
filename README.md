# Collaborative Development Workflow

A Codex skill for scoped software changes with explicit capability preflight,
single-writer implementation, frozen-snapshot independent review, bounded
findings/fix rounds, final validation, and an optional explicitly requested local
commit.

## V2 operating model

| Mode | Default? | Required runtime surface | Acceptance |
| --- | --- | --- | --- |
| `portable` | yes | identifiable read-only subagent, terminal result delivery, shared snapshot access | `ACCEPTED_PORTABLE` only with usable independent review, matching identities, and passing validation |
| `strict` | no; explicit request only | portable surface plus authoritative binding, CAS, monotonic timing, completion/stop events, exact targets, and immutable proofs | `ACCEPTED_STRICT` only with every strict proof |

Strict mode fails before mutation when its capability record is missing; it is never
silently downgraded. If portable review is unavailable, the main agent may still
run its own validation, but the result is `NOT_ACCEPTED` with
`REVIEW_UNAVAILABLE` and no commit. If review evidence exists but cannot be
validated, use `REVIEW_BLOCKED`.

All public statuses, closed record shapes, limits, path rules, and hash domains are
defined in [`references/contracts-v2.json`](references/contracts-v2.json). Older V1
text is legacy and unsupported by the bundled helpers.

## What it provides

- capability preflight before editing or delegation;
- bounded planner, researcher, implementer, verifier, and reviewer prompts;
- one-writer workspace and isolated-worktree discipline;
- deterministic, scoped, tamper-evident review snapshots;
- portable review evidence and strict-only runtime binding/recovery rules;
- bounded context rollover and independent-task handoffs;
- explicit handling for findings, unavailable review, cancellation, and quarantine;
- fail-closed acceptance when independent review evidence is unavailable; and
-- an explicit-only local commit gate that refuses overlapping pre-existing edits.

## Install into Codex

For a source checkout (recommended while developing the skill):

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/Scientific-Tooling/collaborative-development-workflow.git \
  ~/.codex/skills/collaborative-development-workflow
```

For an existing source checkout, update it with `git pull --ff-only` only when that
checkout is the intended installation copy. A copied installation is also valid:
copy the complete skill directory, including `references/`, `scripts/`, `tests/`,
and `agents/`, into a filesystem-discovered skills directory. Do not edit a copied
installed directory when the authoritative source checkout is elsewhere; make the
change in the source checkout and copy/sync it as a separate authorized operation.

Invoke explicitly:

```text
$collaborative-development-workflow
```

The skill does not imply a commit, push, publication, deployment, installation, or
other external mutation.

## Repository layout

```text
SKILL.md                         concise router and core gates
agents/openai.yaml               Codex UI metadata and explicit-only policy
references/contracts-v2.json     machine-readable ContractV2 source of truth
references/task-contracts.md     readable task, result, and digest rules
references/review-runtime.md     portable snapshots and frozen-artifact review
references/review-runtime-strict.md strict binding, timing, events, and CAS
references/review-recovery.md    portable findings loop and review failure
references/review-recovery-strict.md strict cancellation and replacement recovery
references/workflow.md            end-to-end lifecycle and commit gate
references/agent-templates.md    bounded delegated-role prompts
references/coordination-protocol.md shared/portable coordination rules
references/context-rollover.md   temporary handoff and fresh-task protocol
references/failure-and-reporting.md failure and user handoff rules
scripts/contract_tool.py          dependency-free validator/digester/query helper
scripts/snapshot_tool.py          dependency-free snapshot creator/verifier
tests/                            standard-library conformance tests
requirements-dev.txt              optional PyYAML for the official skill validator
```

## Helper commands

Validate or digest a closed ContractV2 record:

```bash
python3 scripts/contract_tool.py validate --kind impact_scope scope.json
python3 scripts/contract_tool.py digest --kind context_handoff handoff.json
python3 scripts/contract_tool.py describe --kind role_result
python3 scripts/contract_tool.py describe --section modes
python3 scripts/contract_tool.py describe --path records.role_result.fields.status
python3 scripts/contract_tool.py describe --path modes.required_capabilities.portable
```

`describe --kind` and `--section` preserve the broad queries. `describe --path`
resolves a dotted path against the effective expanded ContractV2 and emits only
that subtree, so a caller can inspect one field or capability list without loading
the whole ContractV2 source.

Create and check a review artifact outside the repository:

```bash
python3 scripts/snapshot_tool.py create /path/to/repository \
  --scope scope.json --output /tmp/cdw-review-task
python3 scripts/snapshot_tool.py verify /tmp/cdw-review-task --scope scope.json
python3 scripts/snapshot_tool.py compare /path/to/repository /tmp/cdw-review-task \
  --scope scope.json
```

Contract helper exit `0` means valid and `2` means invalid input/contract.
Snapshot helper exit `0` means valid/matching, `1` means an identity mismatch, and
`2` means invalid input/artifact. Snapshot manifests record Git identity, scope,
present/deleted entries, modes, bytes, hashes, and symlink targets without following
symlinks. Snapshot creation and verification require POSIX descriptor-relative
`dir_fd`/`O_NOFOLLOW` support; on a host without that surface the helper fails
closed instead of falling back to racy pathname traversal.

## Development checks

The helpers run on Python 3.10–3.13 using only the standard library:

```bash
python3 -m unittest discover -s tests -v
```

The official Codex skill validator is external to this repository. Install its
development dependency when needed:

```bash
python3 -m pip install -r requirements-dev.txt
python3 /path/to/codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /path/to/collaborative-development-workflow
```

The validator checks frontmatter, naming, and unfinished scaffolding; it does not
prove a host runtime's agent lifecycle semantics. Run YAML parsing and relative-link
checks in addition to the unit tests and validator.

## Safety boundary

This is an unofficial runtime-oriented workflow layer. It does not replace a
repository's own instructions, test suite, access controls, or deployment process.
It must not infer authorization for publishing, deploying, changing external
services, or pushing a branch.

## License

MIT. See [LICENSE](LICENSE).
