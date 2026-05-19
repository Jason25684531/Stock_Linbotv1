## ADDED Requirements

### Requirement: Official daily scheduler entrypoint

The system SHALL treat `jobs/scheduler.py` as the official scheduled entrypoint for the daily recommendation pipeline.

#### Scenario: Scheduled daily pipeline runs through the official entrypoint
- **WHEN** the scheduled daily recommendation job is triggered
- **THEN** the workflow SHALL be coordinated through `jobs/scheduler.py`
- **AND** the pipeline SHALL follow the current repo path:
  `jobs/update_database.py` -> `jobs/run_daily.py` -> `daily_recommendations` -> `/api/daily-signals` and Line push

### Requirement: Daily recommendation persistence completeness

`jobs/run_daily.py` SHALL persist daily recommendation state for every persistence strategy on a market date.

#### Scenario: Strategy produces candidates
- **WHEN** a persistence strategy runs on a market date
- **AND** the strategy produces candidate recommendations
- **THEN** candidate rows SHALL be persisted to `daily_recommendations`

#### Scenario: Strategy produces no candidates
- **WHEN** a persistence strategy runs on a market date
- **AND** the strategy produces no candidate recommendations
- **THEN** an explicit heartbeat row SHALL be persisted to `daily_recommendations`

### Requirement: Daily signals API consumes persisted recommendation state

`/api/daily-signals` SHALL resolve persisted daily recommendation state from `daily_recommendations`.

#### Scenario: Same-day candidates exist
- **WHEN** same-day candidate rows exist
- **THEN** `/api/daily-signals` SHALL return the persisted recommendation data

#### Scenario: Same-day heartbeat exists without candidates
- **WHEN** same-day heartbeat rows exist
- **AND** no candidate rows exist for that strategy
- **THEN** `/api/daily-signals` SHALL represent the strategy as having run with no candidates
