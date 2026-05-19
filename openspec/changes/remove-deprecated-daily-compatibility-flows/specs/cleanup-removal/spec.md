## ADDED Requirements

### Requirement: Cleanup deletion shall use inventory-backed candidate selection

The cleanup removal workflow SHALL use `docs/cleanup_inventory.md` as the primary evidence source for selecting deprecated daily compatibility launchers and wrappers for review.

#### Scenario: A cleanup deletion batch is prepared
- **WHEN** maintainers begin planning removal of deprecated compatibility flows
- **THEN** they SHALL start from candidates classified in `docs/cleanup_inventory.md`
- **AND** they SHALL preserve active paths and unknown paths until refreshed evidence says otherwise

### Requirement: A candidate shall not be deleted without full no-reference verification

A deprecated launcher or wrapper SHALL NOT be deleted until maintainers verify it has no imports, no CLI references, no README or docs references, no docker-compose references, no scheduler references, no test dependencies, no active OpenSpec references beyond historical or deprecation notes, and no user-facing documented workflow.

#### Scenario: A removable candidate reaches final review
- **WHEN** maintainers prepare to delete a deprecated compatibility path
- **THEN** they SHALL verify every no-reference gate passes across code, docs, tests, Compose, scheduler wiring, and OpenSpec references
- **AND** the candidate SHALL remain undeleted if any active dependency remains

### Requirement: Cleanup removal shall define fallback and recovery guidance

Each deletion batch SHALL include fallback and recovery guidance for any removed compatibility launcher or wrapper.

#### Scenario: A removed launcher is still needed
- **WHEN** operators or maintainers discover that a deleted compatibility path is still required
- **THEN** the cleanup plan SHALL provide a documented recovery path
- **AND** recovery SHALL restore the supported workflow without changing the official scheduler contract

### Requirement: Cleanup deletion shall remain separate from runtime consolidation

Cleanup removal SHALL NOT be used to change scheduler logic, daily validation behavior, price provenance, `pipeline_runs`, `DB_URL`, or `MODEL_PATH`.

#### Scenario: A cleanup implementation is reviewed
- **WHEN** maintainers review a cleanup deletion batch
- **THEN** the diff SHALL be limited to verified removals, related docs, and related tests
- **AND** runtime behavior changes SHALL be rejected as out of scope
