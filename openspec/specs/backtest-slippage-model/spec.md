# backtest-slippage-model Specification

## Purpose
Simulate adverse random execution slippage in backtests so ROI, win rate, and realized trade P/L reflect execution friction instead of ideal close-price fills.
## Requirements
### Requirement: Backtest execution shall support configurable adverse slippage

The backtest engine SHALL support a global maximum adverse slippage ratio for
simulated order execution.

#### Scenario: Slippage configuration exists
- **WHEN** backtest code reads runtime configuration
- **THEN** `Config.SLIPPAGE_MAX_PIPS` SHALL be available
- **AND** the default value SHALL be `0.001`

#### Scenario: Zero slippage is configured
- **WHEN** `Config.SLIPPAGE_MAX_PIPS` is `0`
- **THEN** actual buy and sell prices SHALL equal the ideal signal close price

### Requirement: Buy orders shall execute at an adverse random fill price

Buy-side matching SHALL calculate actual execution price by applying a random
non-negative premium to the ideal close price.

#### Scenario: A buy signal is matched
- **WHEN** a strategy buys at ideal `close_price`
- **THEN** the actual buy price SHALL be
  `close_price * (1 + random.uniform(0, Config.SLIPPAGE_MAX_PIPS))`
- **AND** the actual buy price SHALL be greater than or equal to `close_price`

### Requirement: Sell orders shall execute at an adverse random fill price

Sell-side matching SHALL calculate actual execution price by applying a random
non-negative discount to the ideal close price.

#### Scenario: A sell signal is matched
- **WHEN** a strategy sells at ideal `close_price`
- **THEN** the actual sell price SHALL be
  `close_price * (1 - random.uniform(0, Config.SLIPPAGE_MAX_PIPS))`
- **AND** the actual sell price SHALL be less than or equal to `close_price`

### Requirement: Transaction costs shall use actual execution value

Backtest fee and tax calculations SHALL use the slippage-adjusted transaction
amount as their basis.

#### Scenario: Buy-side fees are calculated
- **WHEN** a buy order executes with slippage
- **THEN** `FEE_RATE` and `MIN_FEE` SHALL be calculated from
  `shares * actual_buy_price`

#### Scenario: Sell-side fees and tax are calculated
- **WHEN** a sell order executes with slippage
- **THEN** `FEE_RATE`, `MIN_FEE`, and `TAX_RATE` SHALL be calculated from
  `shares * actual_sell_price`

### Requirement: Slippage shall not mutate historical market data

Random slippage SHALL be isolated to order execution and SHALL NOT modify
historical OHLCV records or indicator inputs.

#### Scenario: Backtest advances to the next trading day
- **WHEN** an order execution applies random slippage
- **THEN** stored `daily_market_data.close_price` values SHALL remain unchanged
- **AND** subsequent candidate selection and indicator calculations SHALL
  continue using original historical prices

### Requirement: Slippage comparison shall be verifiable

The backtest test suite SHALL support deterministic comparison between
zero-slippage and non-zero-slippage runs.

#### Scenario: Slippage-enabled backtest is compared with a baseline
- **WHEN** the same fixture is run once with zero slippage and once with
  deterministic non-zero adverse slippage
- **THEN** the non-zero slippage run SHALL NOT report higher ROI solely due to
  slippage
- **AND** trade count and signal dates SHALL remain stable when strategy inputs
  are unchanged
