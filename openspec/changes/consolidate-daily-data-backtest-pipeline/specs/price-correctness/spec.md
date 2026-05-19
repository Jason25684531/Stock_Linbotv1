## ADDED Requirements

### Requirement: Displayed price shall include trade date or source date context

Any user-facing price display SHALL include enough date provenance to identify what market date or source date the price came from.

#### Scenario: Dashboard or API returns a displayed price
- **WHEN** a dashboard payload or API response includes a displayed price
- **THEN** the response SHALL also include trade date, source date, or equivalent provenance context

### Requirement: Latest price selection shall order by market trade date

The system SHALL determine the latest displayed market price by market trade date, not ingestion timestamp alone.

#### Scenario: Multiple records exist with different ingestion timing
- **WHEN** candidate rows or market rows differ in insertion timing
- **THEN** latest-price selection SHALL prioritize the latest valid market `trade_date`
- **AND** `created_at` or ingestion time SHALL be diagnostic metadata rather than the primary ordering key

### Requirement: Raw close, adjusted close, and latest actual close shall not be silently mixed

The system SHALL distinguish price basis rather than silently presenting different bases under one unlabeled field.

#### Scenario: A consumer needs a price basis
- **WHEN** a dashboard, recommendation surface, or validation flow consumes price data
- **THEN** the selected price basis SHALL be explicit
- **AND** raw close, adjusted close, and latest actual close SHALL NOT be silently interchangeable

### Requirement: Dashboard display shall default to latest actual close unless explicitly configured otherwise

Dashboard-facing price display SHALL prefer the latest actual market close and its trade date unless a different basis is explicitly configured.

#### Scenario: Dashboard health-check or chart payload resolves a quote
- **WHEN** the dashboard builds a current price payload
- **THEN** it SHALL use latest actual close by default
- **AND** it SHALL expose the trade date used for that displayed price

### Requirement: Recommendation price basis shall be explicit

Recommendation surfaces SHALL identify the displayed recommendation price basis and recommendation trade date.

#### Scenario: Daily recommendation payload is returned
- **WHEN** `/api/daily-signals` or another recommendation reader returns a recommendation price
- **THEN** the payload SHALL identify the recommendation trade date
- **AND** the price basis SHALL be explicit rather than implied

### Requirement: Backtest price basis shall be explicit

Backtest validation SHALL declare whether it uses raw close or an explicitly configured adjusted basis.

#### Scenario: Daily validation backtest summary is persisted
- **WHEN** the system records a daily validation backtest result
- **THEN** the summary SHALL identify the price basis used for the validation

### Requirement: Stale fallback price shall be visibly marked

Fallback or stale prices SHALL be surfaced as stale or fallback, not silently presented as fresh.

#### Scenario: Previous-trading-day price is displayed
- **WHEN** the system falls back to a previous trading day for price display
- **THEN** the response SHALL visibly mark the value as stale or fallback
- **AND** it SHALL expose the older trade date used
