## MODIFIED Requirements

### Requirement: Canonical-only public strategy listing

`StrategyManager.list_strategies()` and every user-facing strategy list (Dashboard strategy options, `/backtest` GET options, `/api/strategies`, backtest CLI `all` expansion) SHALL contain only registered canonical strategy ids, with no legacy alias appearing alongside its canonical id. The registered canonical ids SHALL include the 7 original strategies and every ported TQuant strategy. The LINE strategy picker SHALL list only canonical strategies whose metadata `source` is `native`.

#### Scenario: list_strategies returns canonical ids only
- **WHEN** `StrategyManager().list_strategies()` is called
- **THEN** the result equals the canonical ids of `STRATEGY_METADATA` and contains no id listed in any `legacy_ids`

#### Scenario: No duplicated strategy in user-facing surfaces
- **WHEN** the Dashboard page, the LINE strategy picker, or the `/backtest` form is rendered
- **THEN** each strategy appears exactly once, identified by its canonical id

#### Scenario: LINE picker stays within its display limit
- **WHEN** ported TQuant strategies are registered and the LINE strategy picker is built
- **THEN** it lists exactly the 7 original native canonical strategies

## ADDED Requirements

### Requirement: Ported TQuant strategies require complete source data
A strategy ported from `Strategy_example/` SHALL be registered only when every field its source notebook's selection logic requires exists in the local database. It SHALL record `source="tquant"`, have no legacy alias, and use an existing or `fundamental` category. Missing fields SHALL NOT be substituted, and the omission reason SHALL be recorded in the change design.

#### Scenario: Notebook requires unavailable cash-flow data
- **WHEN** a notebook's selection condition requires operating cash flow that the local database does not store
- **THEN** the notebook is not registered and the design mapping records it as excluded with the missing field

#### Scenario: Original strategies are unaffected
- **WHEN** ported strategies are registered
- **THEN** every original canonical id and V-alias resolves exactly as before and their backtest trades match the characterization baseline
