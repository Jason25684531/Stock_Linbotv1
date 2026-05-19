## ADDED Requirements

### Requirement: `jobs/scheduler.py` shall remain the only official daily pipeline entrypoint

The system SHALL treat `jobs/scheduler.py` as the only official scheduled entrypoint for the daily operational pipeline.

#### Scenario: Daily scheduled flow is triggered
- **WHEN** the daily operational pipeline is scheduled to run
- **THEN** the workflow SHALL be coordinated through `jobs/scheduler.py`
- **AND** the official path SHALL remain `jobs/update_database.py` -> `jobs/run_daily.py` -> `daily_recommendations` -> `/api/daily-signals` and Line push

### Requirement: The system shall not introduce a second scheduler path

The daily operational pipeline SHALL NOT gain a second peer scheduler or a parallel official daily path.

#### Scenario: Auxiliary tooling exists
- **WHEN** the repository contains repair tooling, compatibility launchers, or manual scripts
- **THEN** those paths SHALL be classified relative to the official scheduler
- **AND** none of them SHALL be documented as an equal official daily scheduler path

### Requirement: The scheduler shall coordinate the full daily operational flow

The official scheduler SHALL define the order of daily data update, daily recommendation generation, optional lightweight daily backtest validation, and downstream read or push consumers.

#### Scenario: Full daily pipeline runs
- **WHEN** the official daily pipeline executes successfully
- **THEN** the scheduler SHALL coordinate data update first
- **AND** recommendation generation SHALL run before optional daily validation
- **AND** downstream read or push consumers SHALL run after recommendation persistence

### Requirement: Daily entrypoints shall be classified before cleanup

The implementation SHALL classify daily-related scripts and flows as active path, legacy compatibility, or removable candidate before any deletion.

#### Scenario: Duplicate or ambiguous launch paths are reviewed
- **WHEN** the implementation audits daily-related scripts, batch launchers, or wrappers
- **THEN** each path SHALL receive a classification
- **AND** removable status SHALL require later verification rather than proposal-time deletion

### Requirement: Documentation shall point to the official entrypoint

Operator-facing documentation SHALL identify `jobs/scheduler.py` as the official daily scheduled entrypoint.

#### Scenario: Daily run instructions are updated
- **WHEN** runtime or operator docs describe how the daily pipeline runs
- **THEN** those docs SHALL point to `jobs/scheduler.py`
- **AND** compatibility entrypoints SHALL be labeled as legacy or compatibility-only when still documented
