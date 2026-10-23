## ADDED Requirements

### Requirement: Separate engine, data, backtest and evidence states
Each of the 22 strategies SHALL be reported on four independent axes (ENGINE, DATA per dataset and range, BACKTEST as DEVELOPMENT / DIAGNOSTIC / BLOCKED, EVIDENCE), no axis SHALL be inferred from another, and a high Sharpe, CAGR or win rate SHALL NOT by itself mark a strategy effective.

#### Scenario: Strong backtest on uncertified data
- **WHEN** a strategy shows a high Sharpe but one required input is DATA_BLOCKED
- **THEN** its BACKTEST is BLOCKED or DIAGNOSTIC and its EVIDENCE is INSUFFICIENT

### Requirement: Family boundaries are never crossed
Development backtests SHALL be truncated to each family's existing development boundary, SHALL NOT read the Day3 strict OOS window (2026-01-01..2026-07-28) or rewrite the G2/G3 Fresh OOS ledger, and no development result SHALL be labelled OOS.

#### Scenario: Day3 rerun
- **WHEN** a Day3 configuration is re-run on certified data
- **THEN** no input row after 2025-12-31 is read and the result is DEVELOPMENT

### Requirement: Training and inference parity
The training frame builder SHALL produce, for any session, model features identical (rtol 1e-9, atol 1e-12, same order) to the recovered Daily frame for the same inputs, and training SHALL refuse to run without an owner approval file that carries the approved specification SHA-256.

#### Scenario: Training without approval
- **WHEN** `train` is called and no matching owner approval file exists
- **THEN** it raises before reading any data

### Requirement: Execution audit independent of the engine
Every fill of every backtest SHALL be checked against official bars for next-session timing, limit-locked opens, suspensions, delistings and the share of the stock's daily traded value at 1 %, 2 % and 5 %, counting trades and value above each threshold; the daily traded value SHALL be used only after the fact, no engine or order rule SHALL change, and a flag-only audit SHALL NOT be described as a verified fill simulation.

#### Scenario: Buy at a limit-up locked open
- **WHEN** a buy fills on a session whose open equals the limit-up price and high equals low
- **THEN** the fill is flagged unfillable and counted in the audit

#### Scenario: Material excess fills
- **WHEN** flagged or over-threshold fills materially change the result
- **THEN** the evidence grade is lowered to DIAGNOSTIC

### Requirement: Delisting exits are never assumed
A position in a stock that delists SHALL NOT be assumed sold after the last official trading day; without a verifiable exit price or corporate-action payout the run SHALL carry DELISTING_EXIT_UNVERIFIED and SHALL NOT obtain full performance certification, and delisted stocks SHALL NOT be dropped from the universe.

#### Scenario: No verifiable exit
- **WHEN** a held stock delists and no cash payout is documented
- **THEN** the conservative valuation and the evidenced disposition are reported separately and the run is flagged DELISTING_EXIT_UNVERIFIED

### Requirement: Total return only from verifiable cash dividends
Total return SHALL include only pure cash-dividend events, mixed rights-and-dividend events SHALL keep TOTAL_RETURN_BLOCKED for the strategies holding through them, and price return SHALL never be compared with a total-return benchmark.

#### Scenario: Holding through a mixed event
- **WHEN** a position is held through a rights-and-dividend event whose cash part is not separable
- **THEN** the strategy's total return is TOTAL_RETURN_BLOCKED with the event listed

### Requirement: Selection-bias adjusted evidence
A development result SHALL be marked CANDIDATE_FOR_INDEPENDENT_VALIDATION only if it passes the registered cost, bootstrap (net excess return), deflated-Sharpe (conservative trial count covering all 384 configurations and known prior searches), concentration and regime criteria with enough sessions in each regime, and no required certification (total return, execution, survivorship) is missing; it SHALL never be labelled OOS_PASS, PROFITABLE_PROVEN or PRODUCTION_READY, and a multi-factor strategy SHALL be reported as better than a single factor only if the bootstrap interval of the difference excludes zero.

#### Scenario: Multi-factor with overlapping interval
- **WHEN** the Sharpe difference interval of a combination versus its best single factor includes zero
- **THEN** the report states that they cannot be distinguished

### Requirement: Research valuation never writes an invalid close into equity
A research backtest using `valuation_policy='finite_positive_stale'` SHALL value a position only at a finite positive close, SHALL otherwise carry the last valid close as a recorded STALE_VALUATION event (date, stock, reason, price source date, carry length, shares, value, equity share), SHALL stop with VALUATION_BLOCKED when no valid close exists since entry, and SHALL NOT change any order, fill, exit or cost; the default `legacy` policy SHALL stay identical to the pre-change engine.

#### Scenario: Held stock without a board-lot price
- **WHEN** a held stock has a session row whose close is missing, infinite, zero or negative
- **THEN** the research policy carries the previous valid close, records the event and keeps equity finite, while the legacy policy behaves exactly as before

#### Scenario: Carried mark is never a fill
- **WHEN** an order executes on a session where a position was valued at a carried close
- **THEN** the fill uses the official next-session open as before and never the carried price
