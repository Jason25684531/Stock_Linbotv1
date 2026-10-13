## ADDED Requirements

### Requirement: Verify refreshed persisted results end to end
The 2026-10-13 recovery MUST verify that the seven legacy strategies have a persisted same-day candidate snapshot or authoritative heartbeat and that the real Web renders the API date and outcome after strategy selection.

#### Scenario: Recovery succeeds
- **WHEN** market backfill and the 2026-10-13 daily job complete
- **THEN** the market anchor and seven legacy recommendation dates are 2026-10-13
- **THEN** real browser selections render the matching results and stock links or zero-candidate heartbeat

#### Scenario: Manual recovery follows a failed official pipeline
- **WHEN** manual recovery refreshes market data and recommendations after the latest official daily or evening pipeline failed
- **THEN** recommendation freshness is current while the official pipeline heartbeat remains degraded with its original failure and unexecuted steps
- **THEN** no manual success is presented as an official pipeline or LINE push success
