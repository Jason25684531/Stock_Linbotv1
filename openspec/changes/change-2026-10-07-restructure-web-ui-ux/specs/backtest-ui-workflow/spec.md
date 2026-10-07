## ADDED Requirements

### Requirement: Backtest shall remain an independent existing workflow
The application SHALL retain `/backtest`, `backtest.html`, and `backtest_result.html` as the Backtest configuration and result flow without replacing the existing Backtest Engine.

#### Scenario: User opens Backtest
- **WHEN** an authenticated user follows the Backtest navigation link
- **THEN** the existing Backtest route renders configuration for strategy and period
- **THEN** execution continues through the existing backend workflow

### Requirement: Backtest strategy choices shall use shared capability evidence
The Backtest configuration SHALL derive selectable strategies from the same canonical catalog and SHALL allow only strategies with verified Backtest capability.

#### Scenario: Legacy strategy has characterized Backtest support
- **WHEN** catalog capability evidence marks a legacy-backed strategy as available for Backtest
- **THEN** it is selectable by canonical identity in Backtest configuration
- **THEN** its V alias may be displayed without replacing its identity

#### Scenario: Fundamental support is unproven
- **WHEN** the existing Backtest Engine has no authoritative support contract for Fundamental
- **THEN** Fundamental is disabled or omitted from selectable Backtest inputs with a clear limitation
- **THEN** the presentation layer does not submit it to the engine

### Requirement: Backtest accounting and metrics shall remain unchanged
The UI SHALL preserve existing Total Return, MDD, Sharpe, and Win Rate meanings and SHALL not recalculate accounting results in the browser.

#### Scenario: Backtest completes
- **WHEN** the existing engine returns a result
- **THEN** the result view displays Total Return, MDD, Sharpe, and Win Rate using backend-produced values
- **THEN** transaction timing, costs, slippage, signals, and metric semantics remain unchanged

#### Scenario: A metric is undefined
- **WHEN** the backend cannot calculate a requested metric
- **THEN** the UI presents the undefined value and its reason according to the existing `value=None` contract
- **THEN** the UI does not display zero or infinity as a substitute

### Requirement: Backtest results shall organize only run-specific evidence
The result page SHALL retain Equity Curve, Drawdown, and Monthly Return visualizations and MAY organize Overview, Equity, Drawdown, Monthly, and Trades into tabs only when each panel has authoritative data for the current run.

#### Scenario: Current run provides standard chart data
- **WHEN** a completed result includes equity, drawdown, and monthly-return series
- **THEN** those visualizations remain available in the result UI

#### Scenario: Current run does not provide trades
- **WHEN** run-specific trade rows are not supplied to the result template
- **THEN** the result UI omits or marks the Trades panel unavailable
- **THEN** it does not present global persisted trades as records from the current run
