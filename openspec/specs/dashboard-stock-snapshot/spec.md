# dashboard-stock-snapshot Specification

## Purpose
Provide a dense daily decision workstation that surfaces market breadth, recommendation ranks, institutional flow, technical strength, margin risk, and pipeline heartbeat in a single dashboard.
## Requirements
### Requirement: Dashboard shall provide fixed top summary cards
The dashboard SHALL show a fixed top summary card row above the main panels with the latest data date, market traffic-light state, institutional synchronization state, and system heartbeat state.

#### Scenario: Dashboard opens successfully
- **WHEN** an authenticated user opens `/dashboard`
- **THEN** the dashboard fetches `/api/market/summary`
- **THEN** the top summary row displays the resolved `as_of_date`
- **THEN** the top summary row displays market, institutional, and heartbeat states without requiring any tab switch

#### Scenario: Summary data is degraded
- **WHEN** `/api/market/summary` returns `degraded`, `empty`, or warnings
- **THEN** the summary cards remain visible
- **THEN** each unavailable card shows a clear unavailable or degraded state
- **THEN** tab navigation remains usable

### Requirement: Dashboard shall provide six lazy-loaded decision tabs
The dashboard SHALL provide six Alpine.js tabs backed by `activeTab` and `x-show`: Daily Recommendations, Market Snapshot, Institutional / Chips, Technical Strength, Margin / Short, and System Status.

#### Scenario: Default tab loads
- **WHEN** the dashboard initializes
- **THEN** `activeTab` is set to Daily Recommendations
- **THEN** only the summary endpoint and the Daily Recommendations endpoint are required for the initial useful render

#### Scenario: User activates a new tab
- **WHEN** the user selects a tab that has not been loaded
- **THEN** the dashboard fetches only the endpoint mapped to that tab
- **THEN** the tab shows a loading state while the request is in progress
- **THEN** the returned payload is cached in Alpine state for that tab

#### Scenario: User returns to an already-loaded tab
- **WHEN** the user reselects a tab that has already loaded in the current page session
- **THEN** the dashboard shows the cached tab payload without automatically refetching
- **THEN** an explicit refresh action, if provided, refreshes only the active tab and top summary cards

### Requirement: Daily Recommendations tab shall show AI-ranked recommendations
The Daily Recommendations tab SHALL show multi-factor optimized stock candidates with their `ai_score` and canonical recommendation metadata.

#### Scenario: Recommendations exist
- **WHEN** `/api/market/recommendations` resolves candidate rows
- **THEN** the tab displays stock id, optional stock name, close price, `ai_score`, strategy label, recommendation date, and relevant technical or chip fields
- **THEN** candidates are ordered by the server-provided rank

#### Scenario: Recommendation heartbeat or fallback is used
- **WHEN** the recommendation resolver returns a heartbeat, fallback date, or empty result
- **THEN** the tab displays the canonical recommendation date and fallback state
- **THEN** the dashboard does not infer freshness from individual card fields

### Requirement: Market Snapshot tab shall show breadth and traffic-light metrics
The Market Snapshot tab SHALL show market-wide direction using a traffic-light meter and up/down participation ratio.

#### Scenario: Market snapshot is available
- **WHEN** `/api/market/snapshot` returns `ok`
- **THEN** the tab displays the market score or state
- **THEN** the tab displays up-count, down-count, neutral-count, and an up/down ratio bar

#### Scenario: Market rows are insufficient
- **WHEN** the server cannot resolve enough market rows for the requested date
- **THEN** the endpoint returns `degraded` or `empty`
- **THEN** the tab displays the degraded reason instead of broken meters

### Requirement: Institutional tab shall show three-institution flow and synchronized buy candidates
The Institutional / Chips tab SHALL show three-institution net buy/sell totals, foreign/trust/dealer top 10 lists, and synchronized institutional buy candidates.

#### Scenario: Institutional flow is available
- **WHEN** `/api/market/institutional` returns `ok`
- **THEN** the tab displays foreign, trust, dealer, and total net buy/sell values
- **THEN** the tab displays top 10 buy and sell lists for each institution where data is available
- **THEN** the tab displays synchronized buy candidates based on server-side agreement rules

#### Scenario: Some institution fields are missing
- **WHEN** one or more institution fields are unavailable
- **THEN** the endpoint marks the payload as `degraded`
- **THEN** available institution lists still render
- **THEN** missing lists show an unavailable state

### Requirement: Technical Strength tab shall show market strength snapshots
The Technical Strength tab SHALL show weekly gain leaders, volume-price breakout candidates, and RSI overbought/oversold snapshots.

#### Scenario: Technical data is available
- **WHEN** `/api/market/technical` returns `ok`
- **THEN** the tab displays weekly gain Top 10
- **THEN** the tab displays volume-price breakout candidates
- **THEN** the tab displays RSI overbought and RSI oversold groups

#### Scenario: Derived fields must be calculated
- **WHEN** weekly gain or volume-price flags are not persisted as direct columns
- **THEN** the server calculates them from available `daily_market_data` history
- **THEN** the endpoint identifies the source or derived nature of the fields

### Requirement: Margin tab shall show margin, short, and short-to-margin risk lists
The Margin / Short tab SHALL show margin balance increase/decrease, short balance increase/decrease, and high short-to-margin ratio risk stocks.

#### Scenario: Margin and short fields are available
- **WHEN** `/api/market/margin` returns `ok`
- **THEN** the tab displays margin-increase, margin-decrease, short-increase, and short-decrease lists
- **THEN** the tab displays high short-to-margin ratio risk stocks

#### Scenario: Margin fields are unavailable
- **WHEN** `margin_balance` or `short_balance` fields are unavailable for the latest date
- **THEN** the endpoint returns `degraded` or `empty`
- **THEN** the tab shows an explicit unavailable state

### Requirement: System Status tab shall show pipeline heartbeat
The System Status tab SHALL show the latest execution heartbeat for the core pipeline steps `1_update`, `2_run`, and `5_push`.

#### Scenario: Pipeline run rows exist
- **WHEN** `/api/market/system-status` resolves latest `pipeline_runs` rows
- **THEN** the tab displays the status for `1_update`, `2_run`, and `5_push`
- **THEN** statuses are normalized to `Success`, `Failed`, `Running`, `Not Run`, or `Unknown`
- **THEN** the tab includes run date, started time, finished time, and a short error summary when available

#### Scenario: Pipeline run rows are missing
- **WHEN** no matching `pipeline_runs` rows exist
- **THEN** the endpoint returns `empty` or `degraded`
- **THEN** each missing pipeline step displays `Unknown` or `Not Run`
