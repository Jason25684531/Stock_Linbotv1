## ADDED Requirements

### Requirement: Daily lightweight backtest validation shall be supported after recommendation generation

The official daily pipeline SHALL support an optional lightweight validation backtest step after recommendation persistence completes.

#### Scenario: Daily validation is enabled
- **WHEN** the official scheduler runs with daily validation enabled
- **THEN** recommendation generation SHALL complete before the validation step begins

### Requirement: Daily validation shall be bounded and scheduler-safe

Daily validation SHALL be operationally bounded so it is safe for the scheduled daily pipeline.

#### Scenario: Validation job is configured
- **WHEN** lightweight daily validation runs
- **THEN** it SHALL use a bounded recent window, configured strategy subset, and fixed validation universe
- **AND** it SHALL remain fast enough for daily scheduler use

### Requirement: Daily validation shall persist status and summary

The system SHALL persist validation run status and a summary suitable for operational diagnosis.

#### Scenario: Validation completes
- **WHEN** the daily validation step finishes
- **THEN** the system SHALL persist whether it succeeded or failed
- **AND** it SHALL persist summary outputs such as scope, window, or anomaly indicators

### Requirement: Failed validation shall not silently block or corrupt recommendation persistence

Validation failure SHALL be observable without silently rewriting or corrupting persisted recommendations.

#### Scenario: Validation detects an anomaly
- **WHEN** the lightweight validation step fails or reports invalid results
- **THEN** the failure SHALL be recorded explicitly
- **AND** already-persisted `daily_recommendations` rows SHALL remain distinguishable from validation status

### Requirement: Full research backtesting remains outside the daily scheduler

The daily scheduler SHALL NOT absorb full historical research backtests or parameter optimization workflows.

#### Scenario: A user needs historical research or optimization
- **WHEN** a workflow requires full historical coverage, parameter sweeps, or expensive research runs
- **THEN** that workflow SHALL remain outside the daily scheduler
- **AND** the daily validation path SHALL stay limited to lightweight operational checks
