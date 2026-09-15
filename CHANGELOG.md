# Changelog

All notable changes to this project are documented here. The repository follows
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- Model-neutral `model-request-v2` records for explicit, inherited, or runtime
  selection, fallback policy, and reviewer model diversity.
- Resolved selection outcomes in role-result model provenance, with validator
  checks that bind successful reviews to their requests.
- A `workflow_tool.py` helper that creates no-clobber starter records and performs
  the final run, artifact, and review-scope prerequisite checks without claiming
  that unobserved live gates passed.
- Deterministic delegated-prompt rendering from a validated TaskSpec, including
  contract-derived result fields and statuses.
- A read-only custom-reviewer template, Skill metadata validation, and a manual
  Codex behavior evaluation.

### Changed

- Every delegated TaskSpec must record model selection explicitly.
- Every delegated role result must record resolved model, effort, and selection
  outcome, using `unknown` when the runtime does not expose them.
- Reviewer model requests remain stable across findings rounds, and required model
  diversity now fails closed when provenance is unknown or matches the authoring
  model.
- Impact scopes must include path-shaped callers, consumers, tests, and
  configuration in `review_paths`. Implementer write scopes must cover intended
  changes and remain inside that review scope.
- Capability preflights bind the workflow run and record how the reviewer's
  read-only boundary was enforced. An unverified write boundary cannot be ready.
  They now record artifact-only read enforcement separately without making it a
  portable-readiness gate.
- Acceptance outcomes, including non-accepting outcomes, bind the supplied
  preflight, final review round, snapshot, and content identities.
- Execution budgets now have fixed upper bounds, and workflow validation summaries
  must agree with their check records and final reviewer result.
- The workflow helper's live checklist now calls out paused writers and unchanged
  post-freeze state before a parent may accept the review.
- Local setup uses the documented `.agents/skills` path. Portable snapshot review
  is now stated as Linux-only, and helper commands use the selected Skill's
  absolute path.
- Doctor validates supplied preflight shape without claiming that saved records
  prove live runtime readiness.
- Skill instructions use progressive disclosure, one owner for each detailed
  rule, and size budgets that keep the default workflow concise.
- Reusable Git test fixtures and event-based timeout tests reduce CI time without
  reducing the supported Python matrix or regression coverage.

### Fixed

- Malformed nested records now return stable validation errors instead of raising
  implementation exceptions.
- Record validation also reports non-string object keys without raising an
  implementation exception for direct Python callers.
- Metadata validation rejects duplicate YAML keys at every nesting level.
- Metadata validation rejects unknown OpenAI metadata keys while accepting the
  documented optional interface and tool dependency fields.
- Boolean snapshot sizes are rejected as invalid integers.
- File-location references now map to their repository paths without letting a
  similarly spelled semantic reference satisfy path coverage. An explicit `file:`
  escape keeps colon-containing repository paths representable.

### Security

- The manual behavior runner starts from Git-tracked regular files, includes
  tracked cache-named files, binds the repository state and workspace directory,
  distinguishes content changes from timestamp-only changes, checks ignored as
  well as visible files, and applies bounded reads, output, time, and inventory
  limits.
- Docker is now the default boundary for both the Codex runner and validation.
  Containers use a read-only root, an isolated process view, dropped
  capabilities, no Docker socket, one workspace bind mount, and explicit CPU,
  memory, process, open-file, and file-size limits. Direct host execution is marked
  `unsafe-direct` and must be selected explicitly.
- The behavior workflow builds separate runner and validation images before the
  secret-bearing step. Only the runner receives the API key and network access;
  validation gets neither through its environment, but it can read anything the
  runner writes into the shared workspace. The report and documentation now state
  that model-controlled runner commands can access the key. Reports are published
  outside the workspace with safeguards that prevent overwriting an existing file.
- Evaluator Git calls use a trusted system path and disable repository-controlled
  hooks, file-system monitors, and the untracked cache before inspecting runner
  changes.
- Behavior cases require exact output lines, so a negated sentence containing a
  status name does not count as a successful status claim.
- Behavior reports keep case-declared paths readable but replace runner-created
  or validation-created names with counts and domain-separated SHA-256 identifiers;
  low-level inspection errors do not repeat runner-controlled paths.
- Contract and snapshot readers reject named pipes without waiting, and snapshot,
  starter-record, and acceptance paths reject lexical aliases that point into the
  repository.
- Snapshot verification bounds declared scope paths before entry-to-scope coverage
  checks, including for forged manifests.
- Snapshot Git probes and queries use the operating system's fixed trusted command
  path instead of a repository-controlled caller path.
- Reviewer guidance and runtime docs no longer imply that a read-only sandbox or a
  no-history spawn hides the live workspace. Exclusive artifact access is claimed
  only when a container mount or read allowlist enforces it.

### Migration

- These unreleased ContractV2 changes are not compatible with 2.0.0 TaskSpec,
  preflight, acceptance, or digest values. Regenerate those records with the
  updated helpers instead of adding guessed fields to old evidence.

## [2.0.0] - 2026-09-10

### Added

- Structured artifact-access, review-coverage, review-round, and acceptance-evidence records.
- Explicit `review_paths`, typed execution budgets, and commit lifecycle status.
- Transactional snapshot publication, expected-identity flags, bounded artifact reads, and mode-specific capability maps.
- Validator-backed examples, an end-to-end snapshot/evidence test, local doctor diagnostics (including Git), and Python 3.10–3.13 CI.

### Changed

- Acceptance now requires one integrated, fully covered final-snapshot review bundle.
- Portable delegated tasks are read-only and use provisional transport binding.
- Bundled tooling reports strict mode as externally attested only; it cannot manufacture strict readiness or acceptance.
- Snapshot scope is the review path set rather than only the paths intended for a commit.

### Security

- Snapshot and comparison operations now reject Git environment/local-fsmonitor/replacement-object poisoning, disable lazy fetches, avoid porcelain status and filter execution, neutralize diff ordering/submodule presentation configuration, encode attached refs without detached-HEAD aliases, bind intent-to-add staging semantics, reject repository subdirectory roots, transient root and artifact path swaps, workspace drift between reads, ancestor/descendant overlap with explicit exclusions, duplicate semantic evidence IDs, incomplete check coverage, and partial or clobbering artifact publication.

### Migration

- ContractV1 and earlier ContractV2 drafts are intentionally not accepted as current evidence. See [Migrating V1 to V2](references/migrating-v1-to-v2.md).
