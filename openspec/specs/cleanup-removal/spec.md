# cleanup-removal Specification

## Purpose
TBD - created by archiving change remove-deprecated-daily-compatibility-flows. Update Purpose after archive.
## Requirements
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

### Requirement: Removed numeric launchers shall not be recreated during cleanup
Cleanup implementation SHALL NOT recreate `1_update_database.py`, `2_rundaily.py`, `3_train_model.py`, or `6_optimize_params.py` when the target branch already removed them.

#### Scenario: Removed launcher is referenced historically
- **WHEN** the cleanup audit finds a reference to a removed numeric launcher
- **THEN** it SHALL classify whether the reference is active or historical
- **AND** it SHALL NOT recreate the launcher solely to satisfy historical references

### Requirement: Remaining compatibility wrappers shall be retained until deletion gates pass
Cleanup implementation SHALL retain `4_run_backtest.py`, `5_push_to_line.py`, `app.py`, and `tool/*` unless all deletion gates pass.

#### Scenario: Compatibility wrapper appears redundant
- **WHEN** a wrapper proxies to a canonical module
- **THEN** cleanup SHALL verify imports, CLI references, active docs, tests, scheduler wiring, Compose usage, and user-facing workflows before deletion
- **AND** cleanup SHALL keep the wrapper if any active dependency remains

### Requirement: Low-risk cleanup candidates shall not imply broad deletion
Tracked runtime artifacts and bare exception handlers SHALL be treated as narrow cleanup candidates, not justification for unrelated refactors.

#### Scenario: Low-risk candidate is selected
- **WHEN** `.coverage` or a bare `except:` occurrence is selected for future cleanup
- **THEN** the implementation SHALL limit the change to that candidate and directly related tests or validation notes
- **AND** it SHALL NOT change strategy behavior, scheduler behavior, LINE push behavior, or MCP boundaries

