## ADDED Requirements

### Requirement: Every scheduled run shall record observable run state

The system SHALL persist observable execution state for each scheduled daily pipeline run.

#### Scenario: Scheduled run starts
- **WHEN** the official daily scheduler begins a run
- **THEN** the system SHALL create or update persisted run state that identifies the run and its step progression

### Requirement: Data update shall record source date or trade date

The daily data update step SHALL record the relevant source date or market trade date associated with loaded market data.

#### Scenario: Market data update succeeds
- **WHEN** the daily market data update completes
- **THEN** the persisted run state SHALL identify the source date, trade date, or equivalent market-date provenance for the loaded data

### Requirement: Data update shall record row counts when practical

The daily data update step SHALL record inserted and updated row counts when those counts can be measured practically from the implementation path.

#### Scenario: Market rows are written
- **WHEN** the update step inserts or updates market data rows
- **THEN** the run state SHALL record row-count summaries or an equivalent measurable summary

### Requirement: Failed steps shall persist failure status and error summary

Scheduler step failures SHALL be visible from persisted state instead of only console logs.

#### Scenario: A scheduled step fails
- **WHEN** a scheduler step raises an error or exits unsuccessfully
- **THEN** the persisted run state SHALL mark that step as failed
- **AND** the system SHALL retain an error summary suitable for operational diagnosis

### Requirement: Consumers shall be able to tell whether today's data update ran

Operational consumers SHALL be able to answer whether the official daily update ran for the current requested date.

#### Scenario: Operator checks current-day freshness
- **WHEN** an operator or downstream status surface checks daily freshness
- **THEN** the system SHALL expose whether the data update ran today
- **AND** it SHALL identify whether recommendation generation, lightweight backtest validation, and Line push also ran

### Requirement: Stale or fallback data shall not be silently treated as fresh

The system SHALL visibly distinguish stale, fallback, or prior-trading-day data from fresh current data.

#### Scenario: Latest available data is from a previous trading day
- **WHEN** the system serves a price or recommendation based on an older trade date than the requested day
- **THEN** the response SHALL expose that older date or stale state explicitly
- **AND** it SHALL NOT silently label the data as fresh current-day output
