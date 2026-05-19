## ADDED Requirements

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
