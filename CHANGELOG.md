# Changelog

All notable changes to this project are documented here. The repository follows
[Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- Model-neutral `model-request-v2` records for explicit, inherited, or runtime
  selection, fallback policy, and reviewer model diversity.
- Resolved selection outcomes in role-result model provenance, with validator
  checks that bind successful reviews to their requests.

### Changed

- New workflow-produced delegated tasks must record model selection explicitly;
  the schema keeps the field optional only for compatibility with 2.0.0 records.
- Reviewer model requests remain stable across findings rounds, and required model
  diversity now fails closed when provenance is unknown or matches the authoring
  model.

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
