# Collaborative Development Workflow

A Codex Skill for scoped software changes on Linux. It keeps one writer, runs the
project's checks, freezes the files that need review, and gives that snapshot to an
independent reviewer. It creates a local commit only when the user asks for one.

## V2 operating model

| Mode | Default? | Requirements | Acceptance |
| --- | --- | --- | --- |
| `portable` | yes; Linux only | identifiable reviewer with an enforced read-only sandbox, terminal result delivery, shared snapshot access, protected Reviewer wait | `ACCEPTED_PORTABLE` only after live checks and a consistent `acceptance-evidence-v2` record |
| `strict` | no; explicit request only | portable features plus an outside runtime that binds work atomically, records lifecycle events and monotonic time, and safely rejects conflicting updates | only the outside runtime can claim `STRICT_READY` or `ACCEPTED_STRICT` |

Portable snapshot publication depends on Linux filesystem operations. A review
prompt is not a read-only boundary: the runtime must show that the reviewer's
effective sandbox is read-only. Strict mode fails before mutation when its
capability record is missing; it is never silently downgraded. If a reviewer later
becomes unavailable, the main agent may still run its own validation, but the result
is `NOT_ACCEPTED` with `REVIEW_UNAVAILABLE` and no commit. If a review arrives but
cannot be validated, use `REVIEW_BLOCKED`.

Read-only protects the workspace from reviewer writes; it does not hide files the
sandbox permits the reviewer to read. The preflight records that separate fact in
`artifact_only_read_enforcement`. Portable acceptance does not require exclusive
artifact reads, but it must not claim them unless the runtime enforces them.

All public statuses, closed record shapes, limits, path rules, and hash domains are
defined in [`references/contracts-v2.json`](references/contracts-v2.json). Older V1
text is legacy and unsupported by the bundled helpers. The latest released
baseline is 2.0.0; see the [changelog](CHANGELOG.md) for unreleased changes and the
[record migration guide](references/migrating-v1-to-v2.md).

## What it provides

- a live capability check before editing or delegation;
- recorded subagent model requests and actual selections, without hard-coded model IDs;
- limited planner, researcher, implementer, verifier, and reviewer prompts;
- one writer for each mutable workspace, with separate worktrees when needed;
- deterministic review snapshots for the declared files;
- portable review records and separate rules for strict runtime guarantees;
- small continuation records and clean starts for independent tasks;
- reviewer-first execution budgets with one uninterrupted blocking wait;
- bounded context rollover and independent-task handoffs;
- explicit handling for findings, unavailable review, cancellation, and quarantine;
- refusal to accept work when independent review evidence is unavailable; and
- a local commit only when explicitly requested and safe from overlapping old edits.

For small, deterministic changes, the fast path keeps planning, implementation,
focused checks, and full validation in the main agent and delegates only one
independent Reviewer. Planner, researcher, verifier, and context-handoff work is
reserved for concrete uncertainty or an actual continuation need.

## Install into Codex

For a personal source checkout while developing the Skill:

```bash
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/Scientific-Tooling/collaborative-development-workflow.git \
  "$HOME/.agents/skills/collaborative-development-workflow"
```

For an existing source checkout, update it with `git pull --ff-only` only when that
checkout is the intended installation copy. A copied installation is also valid:
copy the complete Skill directory, including `references/`, `scripts/`, `tests/`,
`agents/`, and `assets/`, into `$HOME/.agents/skills` or a repository's
`.agents/skills` directory. Do not edit a copied installation when the authoritative
source checkout is elsewhere; make the change in the source checkout and copy or
sync it as a separate authorized operation.

Direct Skill folders are intended for local authoring and repository use. For
installation by other people, package the Skill as a plugin. Plugins are the
documented distribution format and may also include connectors. See OpenAI's
[Build skills](https://learn.chatgpt.com/docs/build-skills) and
[Build plugins](https://learn.chatgpt.com/docs/build-plugins) documentation.

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
assets/cdw-reviewer.toml         read-only custom reviewer template
references/contracts-v2.json     machine-readable ContractV2 source of truth
references/task-contracts.md     readable task, result, and digest rules
references/review-runtime.md     portable snapshots and frozen-artifact review
references/review-runtime-strict.md strict binding, timing, events, and conflict checks
references/review-recovery.md    portable findings loop and review failure
references/review-recovery-strict.md strict cancellation and replacement recovery
references/workflow.md            end-to-end lifecycle and commit gate
references/agent-templates.md    limited delegated-role prompts
references/model-selection.md    model request, fallback, and reviewer diversity policy
references/coordination-protocol.md shared/portable coordination rules
references/context-rollover.md   temporary handoff and fresh-task protocol
references/failure-and-reporting.md failure and user handoff rules
references/migrating-v1-to-v2.md older-record migration and digest reset
scripts/contract_tool.py          dependency-free validator/digester/query helper
scripts/snapshot_tool.py          dependency-free snapshot creator/verifier
scripts/workflow_tool.py          starter-record, prompt, and acceptance helper
scripts/doctor.py                  local compatibility and preflight diagnostics
scripts/validate_metadata.py       Skill and UI YAML validator
scripts/behavior_eval.py           isolated Codex behavior-evaluation runner
evals/doctor-pretty.json           representative Skill behavior case
examples/                          fictional contract-conformance fixtures
tests/                            standard-library conformance tests
requirements-dev.txt              metadata validation dependencies
```

## Set the Skill path

Codex usually runs commands from the project being changed. A relative helper path
could therefore run a file from that project.
Before using a bundled helper, set `CDW_SKILL_DIR` to the absolute directory that
contains this README and `SKILL.md`. Do not derive it from the target project's
current directory.

For the personal installation above:

```bash
CDW_SKILL_DIR="$HOME/.agents/skills/collaborative-development-workflow"
```

When using a source checkout elsewhere, replace the value with that checkout's
absolute path. Keep target-project paths separate from `CDW_SKILL_DIR`.

## Workflow helper

Create starter records after the final scope is known. The output must be an
absolute path outside the target repository and must not already exist:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" init \
  --output /tmp/cdw-task-1 \
  --root /absolute/path/to/repository \
  --changed-path src/example.py \
  --review-path tests/test_example.py
```

This creates the impact scope, focused-check starter, model request, capability
preflight, and planner TaskSpec. The preflight is deliberately `NOT_READY` with
`read_only_enforcement=unverified` and
`artifact_only_read_enforcement=unknown`; replace placeholders only with facts
observed for the current task and runtime. The JSON report prints the new `run_id`
and `task_id` for later commands.

Render the bounded prompt for any validated delegated TaskSpec instead of copying
the prompt reference by hand:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" prompt \
  --task-spec /tmp/cdw-task-1/task_spec.json
```

The compact JSON report includes the TaskSpec digest and a `prompt` value with the
common and role-specific rules. Supply applicable repository instructions and live
runtime facts separately. The helper rejects a portable delegated implementer
because portable mode keeps the main agent as the only writer. It also rejects an
output budget too small for the shortest successful, correlated result allowed for
that TaskSpec.

After the frozen review and acceptance record are ready, run:

```bash
python3 "$CDW_SKILL_DIR/scripts/workflow_tool.py" accept \
  --evidence /absolute/path/to/acceptance-evidence.json \
  --root /absolute/path/to/repository \
  --expected-run-id RUN_ID
```

`accept` validates the linked records, checks the expected run ID, reopens and
verifies the snapshot, binds the evidence's base snapshot and index identity to the
manifest, and compares the declared review scope with the workspace.
It reports this as `review_scope_match`; it does not claim that unrelated tracked
files match. Inspect the complete diff and status separately before acceptance.
It cannot observe that the reviewer actually ran in the claimed
sandbox or prove the preflight time, so
the parent still confirms those facts during the live run. Its preflight report is
therefore `RUN_BOUND_NOT_TIME_VERIFIED`, not a claim of time freshness.
Even on exit `0`, the report sets `helper_confirmed_acceptance=false`, lists the
unobserved parent checks in `live_confirmation_required`, and only marks
`evidence_and_review_scope_valid=true`. The parent may report formal acceptance
only after it has completed those checks and every gate in the full workflow. The
list explicitly requires all writers to remain paused and the scoped state to stay
unchanged after the snapshot is frozen.

## Contract helper

Check or calculate the digest of a ContractV2 record:

```bash
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" validate --kind impact_scope \
  "$CDW_SKILL_DIR/examples/impact_scope.json"
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" digest --kind context_handoff \
  "$CDW_SKILL_DIR/examples/context_handoff.json"
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" validate --kind acceptance_evidence \
  "$CDW_SKILL_DIR/examples/acceptance_evidence.json"
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" validate --kind model_request \
  "$CDW_SKILL_DIR/examples/model_request.json"
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --kind role_result
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --section modes
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --path records.role_result.fields.status
python3 "$CDW_SKILL_DIR/scripts/contract_tool.py" describe --path modes.required_capabilities.portable
```

`describe --kind` and `--section` preserve the broad queries. `describe --path`
resolves a dotted path against the effective expanded ContractV2 and emits only
that subtree, so a caller can inspect one field or capability list without loading
the whole ContractV2 source. Validation confirms that supplied records have the
expected shape and agree with each other. It does not inspect the live runtime,
prove that a subagent ran, or open and compare a snapshot. The live review and
workspace checks in [`references/workflow.md`](references/workflow.md) are separate
acceptance requirements.

The files under `examples/` are fictional conformance fixtures. They show valid
record shapes but are not reusable capability, reviewer, artifact, or acceptance
evidence. In particular, a nested accepted example does not prove that the current
runtime is ready.

Every newly produced delegated TaskSpec includes a `model-request-v2`. It records
whether selection is explicit, inherited, or runtime-defaulted; the fallback
policy; and any reviewer model-diversity requirement. Concrete model IDs remain in
user, repository, or runtime configuration. The result records the resolved model,
effort, and whether selection was honored, fell back, or could not be observed.
See [`references/model-selection.md`](references/model-selection.md).

## Snapshot helper

This helper is Linux-only. Create and check a review snapshot outside the target
repository:

Reviewer TaskSpecs use the Extended execution profile by default (`10800` seconds,
`128` turns, and `524288` output bytes) unless a shorter budget is explicitly
requested. The parent waits on the same reviewer invocation and does not poll or
interrupt it before the protected deadline; a host that cannot honor that contract
cannot silently claim a completed independent review.

```bash
python3 "$CDW_SKILL_DIR/scripts/snapshot_tool.py" create /path/to/repository \
  --scope /path/to/task-scope.json --output /tmp/cdw-review-task
python3 "$CDW_SKILL_DIR/scripts/snapshot_tool.py" verify /tmp/cdw-review-task \
  --scope /path/to/task-scope.json \
  --expected-content-identity CONTENT_IDENTITY \
  --expected-manifest-identity MANIFEST_IDENTITY
python3 "$CDW_SKILL_DIR/scripts/snapshot_tool.py" compare \
  /path/to/repository /tmp/cdw-review-task \
  --scope /path/to/task-scope.json \
  --expected-content-identity CONTENT_IDENTITY \
  --expected-manifest-identity MANIFEST_IDENTITY
```

Contract helper exit `0` means valid and `2` means invalid input or contract.
Snapshot helper exit `0` means valid or matching, `1` means an identity mismatch,
and `2` means invalid input or artifact. Keep both identities printed by `create`
and pass them to later `verify` and `compare` operations.

The snapshot records the Git baseline and the exact scoped paths, including
deletions, file modes, hashes, and symlink targets. It does not follow symlinks. It
rejects traversal, concurrent source changes, oversized inputs, and unsafe Git
features. Publication is all-or-nothing and refuses to replace an existing output
directory. These guarantees depend on Linux filesystem operations; there is no
weaker fallback. See
[`references/review-runtime.md`](references/review-runtime.md) for the detailed
rules.

Diagnose the Skill checkout without claiming that the live runtime is ready:

```bash
python3 "$CDW_SKILL_DIR/scripts/doctor.py" --root "$CDW_SKILL_DIR"
python3 "$CDW_SKILL_DIR/scripts/doctor.py" --root "$CDW_SKILL_DIR" \
  --preflight "$CDW_SKILL_DIR/examples/capability_preflight.json"
```

Doctor always reports live runtime readiness as `UNATTESTED`, even when all local
helpers are usable. With `--preflight`, it validates the supplied record and reports
that record's result separately; it does not attest the record's claims or promote
them to live readiness. Local diagnostics also exercise the required snapshot
platform operations and a limited, sanitized `git --version` probe.

## Configure an enforced read-only reviewer

Portable acceptance needs a custom reviewer whose effective sandbox is read-only.
The template at [`assets/cdw-reviewer.toml`](assets/cdw-reviewer.toml) follows the
current custom-agent format. Copy it, with user authorization, to either
`.codex/agents/cdw-reviewer.toml` in the target project or
`$HOME/.codex/agents/cdw-reviewer.toml` for personal use. A standalone Skill cannot
install or activate custom-agent configuration by itself.

The template sets `sandbox_mode="read-only"` and `approval_policy="never"`.
Sandbox-allowed reads remain available, while any command that would need permission
to leave the read-only boundary fails instead of asking for an exception. These
settings block writes; they do not conceal the target workspace or other readable
paths.

Select this custom agent by its configured name, `cdw_reviewer`; the filename is
not the agent identity. Before each review, confirm that the runtime selected it
and that its effective `sandbox_mode` is `read-only`. Parent-session overrides can
affect a child's effective settings; also confirm that no writable override or
escalation applied during review. Telling a writable reviewer not to edit is not an
enforced boundary. See OpenAI's current
[Subagents documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
and [Sandbox documentation](https://learn.chatgpt.com/docs/sandboxing).

The template also tells the reviewer not to inspect the moving workspace. This is
an instruction, not an operating-system read boundary. Starting with
`fork_turns="none"` removes inherited conversation history but does not change
filesystem permissions. Use `artifact_only_read_enforcement=container_mount` only
when the reviewer can see only the mounted review input, `read_allowlist` when the
runtime permits only named read paths, `none` for a known shared readable
filesystem, and `unknown` when this was not checked. This field reports a boundary;
it is not a portable-readiness gate. Keep secrets out of any path the reviewer can
read.

The capability preflight records the current `run_id`, write protection in
`read_only_enforcement`, and the separate read boundary described above. Use
`custom_agent_sandbox` only after the installed custom reviewer was selected and
confirmed effective; shipping the template does not make that claim true. Use
`parent_sandbox` only when the runtime confirms inherited read-only mode. Otherwise
use `unverified`, which cannot produce a ready preflight.

## Development checks

The runtime helpers run on Python 3.10–3.13 using only the standard library. Install
the development dependency before running all repository checks:

```bash
python3 -m pip install -r "$CDW_SKILL_DIR/requirements-dev.txt"
python3 -m unittest discover -s "$CDW_SKILL_DIR/tests" -v
python3 "$CDW_SKILL_DIR/scripts/doctor.py" --root "$CDW_SKILL_DIR" \
  --preflight "$CDW_SKILL_DIR/examples/capability_preflight.json"
python3 "$CDW_SKILL_DIR/scripts/validate_metadata.py" --root "$CDW_SKILL_DIR"
```

The official Codex Skill validator is external to this repository:

```bash
python3 /path/to/codex/skills/.system/skill-creator/scripts/quick_validate.py \
  "$CDW_SKILL_DIR"
```

The validator checks frontmatter, naming, and unfinished scaffolding; it does not
prove a host runtime's agent lifecycle semantics. Run YAML parsing and relative-link
checks in addition to the unit tests and validator.

The manual
[`Manual Codex behavior evaluation`](.github/workflows/behavior-eval.yml) GitHub
Actions workflow runs
[`evals/doctor-pretty.json`](evals/doctor-pretty.json) with pinned Codex CLI 0.154.0 in
an isolated temporary repository and uploads its report. The workflow builds its
Codex runner and validation images before the step that receives the secret. The
runner container has unrestricted Docker-bridge network access, including the
access needed for the Codex API. It also has a read-only root filesystem and one
writable host-shared directory for the temporary workspace. The separate validation
container has no network access, and no OpenAI or Codex credential is passed
directly into its environment. It still reads the runner-modified workspace, which
can contain a credential if the runner wrote one there. Neither container mounts
the Docker socket. Both run as the host user with dropped capabilities, an isolated
process view, and resource limits.

This workflow requires Docker and an `OPENAI_API_KEY`, so it is intentionally
separate from routine pull-request checks. Configure that secret and suitable
protection rules on the `codex-behavior-eval` GitHub environment; naming an
environment in the workflow does not protect it by itself. The writable workspace
uses the host filesystem and has no hard quota. The evaluator applies file,
process, memory, CPU, output, entry-count, and post-run size limits, but those checks
do not prevent a runner from filling the host filesystem first. Use a disposable
CI runner with its own storage limits and no route to a trusted host or LAN. Docker
bridge networking is not limited to OpenAI endpoints.

The Codex process and the commands it starts share the runner container, so those
commands can read and send the `OPENAI_API_KEY`. Docker does not isolate that key
from model-controlled commands. Use a separate project key for this evaluation,
grant only the needed API permissions, give it a short expiration, monitor its use,
and revoke or rotate it after the run. Do not use a long-lived personal or
administrative key. OpenAI documents both
[restricted key permissions](https://help.openai.com/en/articles/8867743-assign-api-key-permissions)
and [API key safety practices](https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety).

Docker is the default for both execution phases. The `unsafe-direct` choice exists
only for explicit local debugging and is not an isolation boundary; do not use it
with untrusted cases or code. It requires a caller-managed `--workspace`, which the
evaluator does not delete. A report path must be outside both the source and the
temporary workspace, and an existing report is never overwritten. The manual
workflow uses a new report directory and uploads only after a separate marker
matches the report's SHA-256 digest. The report is a smoke-test signal, not proof of
formal acceptance: an exact status line is still only a claim in the runner's text.
Paths listed by the trusted case remain readable. Runner-created or
validation-created path names are reported only as a count and a domain-separated
SHA-256 identifier, so a model cannot copy the API key into an uploaded report by
using it as a filename.

## Safety boundary

This is an unofficial runtime-oriented workflow layer. It does not replace a
repository's own instructions, test suite, access controls, or deployment process.
It must not infer authorization for publishing, deploying, changing external
services, or pushing a branch.

## License

MIT. See [LICENSE](LICENSE).
