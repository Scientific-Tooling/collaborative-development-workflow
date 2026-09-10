# Collaborative Development Workflow

A Codex skill for scoped software changes with explicit capability preflight,
single-writer implementation, validation, frozen-snapshot independent review,
bounded findings/fix rounds, and an optional explicitly requested local commit.

## V2 operating model

| Mode | Default? | Required runtime surface | Acceptance |
| --- | --- | --- | --- |
| `portable` | yes | identifiable read-only subagent, terminal result delivery, shared snapshot access | `ACCEPTED_PORTABLE` only through a valid `acceptance-evidence-v2` bundle |
| `strict` | no; explicit request only | portable surface plus an authoritative runtime adapter, atomic binding, CAS, monotonic timing, lifecycle events, exact targets, and immutable proofs | externally attested only; bundled tooling cannot claim `STRICT_READY` or `ACCEPTED_STRICT` |

Strict mode fails before mutation when its capability record is missing; it is never
silently downgraded. If portable review is unavailable, the main agent may still
run its own validation, but the result is `NOT_ACCEPTED` with
`REVIEW_UNAVAILABLE` and no commit. If review evidence exists but cannot be
validated, use `REVIEW_BLOCKED`.

All public statuses, closed record shapes, limits, path rules, and hash domains are
defined in [`references/contracts-v2.json`](references/contracts-v2.json). Older V1
text is legacy and unsupported by the bundled helpers. The latest released
baseline is 2.0.0; see the [changelog](CHANGELOG.md) for unreleased changes and the
[V1 migration guide](references/migrating-v1-to-v2.md).

## What it provides

- capability preflight before editing or delegation;
- explicit, model-neutral sub-agent selection requests with recorded resolution;
- bounded planner, researcher, implementer, verifier, and reviewer prompts;
- one-writer workspace and isolated-worktree discipline;
- deterministic, scoped, tamper-evident review snapshots;
- portable review evidence and strict-only runtime binding/recovery rules;
- bounded context rollover and independent-task handoffs;
- explicit handling for findings, unavailable review, cancellation, and quarantine;
- fail-closed acceptance when independent review evidence is unavailable; and
- an explicit-only local commit gate that refuses overlapping pre-existing edits.

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
references/model-selection.md    model request, fallback, and reviewer diversity policy
references/coordination-protocol.md shared/portable coordination rules
references/context-rollover.md   temporary handoff and fresh-task protocol
references/failure-and-reporting.md failure and user handoff rules
references/migrating-v1-to-v2.md V1-to-V2 migration and digest reset
scripts/contract_tool.py          dependency-free validator/digester/query helper
scripts/snapshot_tool.py          dependency-free snapshot creator/verifier
scripts/doctor.py                  local compatibility and preflight diagnostics
examples/                          validator-backed portable records
tests/                            standard-library conformance tests
requirements-dev.txt              optional PyYAML for the official skill validator
```

## Helper commands

Validate or digest a closed ContractV2 record:

```bash
python3 scripts/contract_tool.py validate --kind impact_scope examples/impact_scope.json
python3 scripts/contract_tool.py digest --kind context_handoff examples/context_handoff.json
python3 scripts/contract_tool.py validate --kind acceptance_evidence examples/acceptance_evidence.json
python3 scripts/contract_tool.py validate --kind model_request examples/model_request.json
python3 scripts/contract_tool.py describe --kind role_result
python3 scripts/contract_tool.py describe --section modes
python3 scripts/contract_tool.py describe --path records.role_result.fields.status
python3 scripts/contract_tool.py describe --path modes.required_capabilities.portable
```

`describe --kind` and `--section` preserve the broad queries. `describe --path`
resolves a dotted path against the effective expanded ContractV2 and emits only
that subtree, so a caller can inspect one field or capability list without loading
the whole ContractV2 source.

Every newly produced delegated TaskSpec includes a `model-request-v2`. It records
whether selection is explicit, inherited, or runtime-defaulted; the fallback
policy; and any reviewer model-diversity requirement. Concrete model IDs remain in
user, repository, or runtime configuration. The result records the resolved model,
effort, and whether selection was honored, fell back, or could not be observed.
See [`references/model-selection.md`](references/model-selection.md).

Create and check a review artifact outside the repository:

```bash
python3 scripts/snapshot_tool.py create /path/to/repository \
  --scope examples/impact_scope.json --output /tmp/cdw-review-task
python3 scripts/snapshot_tool.py verify /tmp/cdw-review-task \
  --scope examples/impact_scope.json \
  --expected-content-identity CONTENT_IDENTITY \
  --expected-manifest-identity MANIFEST_IDENTITY
python3 scripts/snapshot_tool.py compare /path/to/repository /tmp/cdw-review-task \
  --scope examples/impact_scope.json \
  --expected-content-identity CONTENT_IDENTITY \
  --expected-manifest-identity MANIFEST_IDENTITY
```

Contract helper exit `0` means valid and `2` means invalid input/contract.
Snapshot helper exit `0` means valid/matching, `1` means an identity mismatch, and
`2` means invalid input/artifact. Snapshot manifests record Git identity, scope,
present/deleted entries, modes, bytes, hashes, and symlink targets without following
symlinks. The manifest identity is the ContractV2 `snapshot_id`. Always retain both
identities emitted by `create` and require them during post-review `verify` and
`compare`. Snapshot publication is complete-or-absent and uses Linux
`renameat2(RENAME_NOREPLACE)` in the output directory; the helper fails closed when
that atomic no-clobber primitive is unavailable. All snapshot operations require
POSIX descriptor-relative `dir_fd`/`O_NOFOLLOW` operations; creation and comparison
also require a validated `/proc/self/fd` or `/dev/fd` path for descriptor-bound Git
queries. No racy pathname or check-then-rename fallback is used. Git queries disable
replacement objects and lazy fetches, never call porcelain status or worktree
content conversion, and have a contract-bounded runtime. Full attached refs cannot
alias detached HEAD; a stage inventory plus non-converting raw staged diff binds
intent-to-add semantics, while deleted/untracked inventories bind global path-class
changes. Exact scoped bytes and modes bind tracked worktree state, so repository
clean/process filters are not executed. Directory review scopes cannot contain, or
be contained by, a path-shaped explicit exclusion.

Diagnose the checkout without manufacturing runtime readiness:

```bash
python3 scripts/doctor.py --root .
python3 scripts/doctor.py --root . --preflight examples/capability_preflight.json
```

Without `--preflight`, doctor reports runtime readiness as `UNATTESTED` even when
all local helpers are usable. Local diagnostics also exercise the exact snapshot
platform primitives and a bounded, sanitized `git --version` probe.

## Development checks

The helpers run on Python 3.10–3.13 using only the standard library:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/doctor.py --root . --preflight examples/capability_preflight.json
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
