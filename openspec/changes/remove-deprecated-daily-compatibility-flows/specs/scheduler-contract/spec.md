## ADDED Requirements

### Requirement: `jobs/scheduler.py` shall remain the only official daily entrypoint during cleanup

The repository SHALL preserve `jobs/scheduler.py` as the only official scheduled entrypoint while deprecated compatibility flows are reviewed and removed.

#### Scenario: A cleanup deletion removes a legacy launcher
- **WHEN** maintainers delete a deprecated compatibility launcher or wrapper
- **THEN** `jobs/scheduler.py` SHALL remain the only official daily scheduler path
- **AND** no second scheduler path SHALL be introduced

### Requirement: Cleanup shall preserve the official daily flow

Cleanup work SHALL preserve the official daily operational flow `update_database -> run_daily -> daily_backtest_validation -> push_to_line`.

#### Scenario: Compatibility files are removed
- **WHEN** a cleanup batch removes deprecated daily compatibility files
- **THEN** the supported daily workflow SHALL remain `jobs/update_database.py` -> `jobs/run_daily.py` -> `jobs/run_daily_backtest_validation.py` -> `jobs/push_to_line.py`
- **AND** recommendation persistence and downstream consumers SHALL continue to depend on the official scheduler path

### Requirement: Cleanup shall not reclassify repair or research tooling as scheduler peers

Repair tooling, manual backfill paths, and research backtest tools SHALL be classified relative to the official scheduler and SHALL NOT become peer official daily entrypoints during cleanup.

#### Scenario: Non-daily tooling remains in the repository
- **WHEN** maintainers review backfill, research, or compatibility tooling during cleanup
- **THEN** those paths SHALL remain documented according to their actual role
- **AND** none of them SHALL be promoted into a parallel official daily scheduler path
