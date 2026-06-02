# docs-and-tests Specification

## Purpose
TBD - created by archiving change remove-deprecated-daily-compatibility-flows. Update Purpose after archive.
## Requirements
### Requirement: Documentation shall align to the supported path before and after removal

Operator-facing docs SHALL point to the supported scheduler path and SHALL NOT depend on removed compatibility launchers after cleanup.

#### Scenario: A compatibility launcher is approved for removal
- **WHEN** maintainers prepare docs for a cleanup deletion
- **THEN** README and related operator docs SHALL already point to `jobs/scheduler.py` as the official daily entrypoint
- **AND** removed compatibility paths SHALL no longer be presented as supported operator workflows

### Requirement: Cleanup deletion shall be protected by contract tests

Cleanup removal SHALL be backed by tests that protect scheduler ownership, docs alignment, and the absence of active references to removed compatibility files.

#### Scenario: A deletion batch is ready to merge
- **WHEN** maintainers validate a cleanup batch
- **THEN** targeted contract tests SHALL confirm the official scheduler path remains intact
- **AND** targeted cleanup tests SHALL confirm removed paths are no longer required by supported docs or workflows

### Requirement: Historical references shall be treated differently from active dependencies

Historical or deprecation-only notes in OpenSpec or docs SHALL NOT by themselves block deletion, but active guidance and active test dependencies SHALL block deletion until removed or updated.

#### Scenario: Archived notes still mention a deprecated launcher
- **WHEN** maintainers review OpenSpec or docs references for a deletion candidate
- **THEN** historical references may remain if they are clearly non-operational
- **AND** active runtime guidance, active test expectations, or active support docs SHALL be removed before deletion proceeds

### Requirement: Active references shall be separated from historical references
Cleanup validation SHALL distinguish active references from archived or historical references before treating a reference as a blocker.

#### Scenario: Reference scan finds an old path
- **WHEN** a removed or deprecated path is found in docs, OpenSpec, tests, CI, scripts, or archived material
- **THEN** validation SHALL classify it as active guidance, active test or CI dependency, active script dependency, current workflow reference, or historical-only reference
- **AND** historical-only references SHALL NOT block cleanup by themselves

### Requirement: Cleanup validation shall include baseline and post-change evidence
Future cleanup implementation SHALL record baseline verification before changes and post-change verification after changes.

#### Scenario: Cleanup validation report is written
- **WHEN** the validation report is produced
- **THEN** it SHALL include results for compile checks, targeted pytest commands, focused strategy/Rich Menu/MCP tests, and safe smoke checks where available
- **AND** any skipped command SHALL include a concrete reason and residual risk

### Requirement: Documentation updates shall not rewrite historical archives by default
Cleanup documentation work SHALL update active operator guidance and active tests before considering archived OpenSpec or historical notes.

#### Scenario: Active docs and archives disagree
- **WHEN** active docs use the current canonical path and archived docs mention a removed path
- **THEN** cleanup SHALL preserve the archived reference unless the archive is explicitly in active use
- **AND** cleanup SHALL note the mismatch in the audit report instead of silently rewriting history

