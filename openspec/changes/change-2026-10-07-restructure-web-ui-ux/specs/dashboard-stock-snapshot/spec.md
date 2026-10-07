## ADDED Requirements

### Requirement: Dashboard shall provide quick stock search and recent navigation
The dashboard SHALL provide a prominent stock-ID-or-name search that navigates to `/stock-analysis?id=<stock_id>` and SHALL keep any recent-search convenience state in the browser rather than changing the database schema.

#### Scenario: User searches for a valid stock
- **WHEN** the user selects or submits stock `2330`
- **THEN** the browser navigates to `/stock-analysis?id=2330`
- **THEN** Dashboard does not expand a full candlestick workspace in place

#### Scenario: User selects a recent search
- **WHEN** a recent-search entry contains a valid stock identity
- **THEN** selecting it navigates to the same dedicated Stock Analysis route

### Requirement: Dashboard shall remain a summary-only page
The dashboard SHALL render compact market, recommendation, active-strategy, system, and quick-search summaries and MUST NOT render the full stock chart, complete Strategy catalog, full Backtest flow, detailed market tabs, or large raw JSON as primary UI.

#### Scenario: Dashboard initializes
- **WHEN** an authenticated user opens `/dashboard`
- **THEN** the initial view contains summary cards and links to dedicated pages
- **THEN** no full K-line chart container or `LightweightCharts.createChart` call is created by Dashboard

#### Scenario: Debug payload access is retained
- **WHEN** development diagnostics require access to a raw payload
- **THEN** the payload is available only in an explicitly labeled collapsed debug section
- **THEN** raw JSON is not visible as the primary user interface

### Requirement: Dashboard shall show evidence-backed market summary dimensions
The dashboard SHALL show compact trend, breadth, institutional, technical, and margin states where existing APIs provide authoritative evidence, and SHALL show Unknown or Unavailable for unsupported values.

#### Scenario: Summary dimensions are available
- **WHEN** the existing market APIs resolve authoritative summary states
- **THEN** the Dashboard displays those states without rendering the former full detail panels

#### Scenario: Exact index value is not in the contract
- **WHEN** the existing market summary does not provide an authoritative TWSE index value or change percentage
- **THEN** the corresponding card shows an unavailable state or a supported alternative
- **THEN** the UI does not fabricate the example value

## MODIFIED Requirements

### Requirement: Dashboard shall provide fixed top summary cards
The dashboard SHALL show a compact fixed top summary row above the main content with Market, Today's Recommendations, Active Strategies, and System cards using authoritative API data and clear normal, degraded, empty, disabled, or unknown states.

#### Scenario: Dashboard opens successfully
- **WHEN** an authenticated user opens `/dashboard`
- **THEN** the dashboard fetches only the summary contracts required for the initial useful render
- **THEN** the top row displays market, recommendation count, active-strategy count, and system state without requiring a tab switch

#### Scenario: Summary data is degraded
- **WHEN** a summary contract returns `degraded`, `empty`, warnings, or lacks evidence
- **THEN** all four summary cards remain visible
- **THEN** each unavailable value shows a clear unavailable, degraded, or unknown state
- **THEN** navigation to independent pages remains usable

### Requirement: Daily Recommendations tab shall show AI-ranked recommendations
The Dashboard recommendation section SHALL show a compact, server-ranked recommendation table with stock identity, concise recommendation reason, strategy identity, direction, canonical recommendation date, and fallback state where available.

#### Scenario: Recommendations exist
- **WHEN** `/api/market/recommendations` resolves candidate rows
- **THEN** the compact table displays stock ID, optional stock name, concise reason, canonical strategy label, and direction from authoritative fields
- **THEN** candidates retain the server-provided rank
- **THEN** a `查看全部` affordance links to a verified deeper destination when one exists

#### Scenario: Recommendation heartbeat or fallback is used
- **WHEN** the recommendation resolver returns a heartbeat, fallback date, degraded source, or empty result
- **THEN** the section displays the canonical recommendation date and fallback or empty state
- **THEN** the dashboard does not infer freshness from individual row fields

### Requirement: System Status tab shall show pipeline heartbeat
The Dashboard system summary SHALL compactly present verified operational evidence and broker-disabled state, while a dedicated System view SHALL own available detailed pipeline heartbeat fields for `1_update`, `2_run`, and `5_push`.

#### Scenario: Pipeline run rows exist
- **WHEN** `/api/market/system-status` resolves latest `pipeline_runs` rows
- **THEN** Dashboard displays a compact truthful aggregate or key statuses
- **THEN** the System view can display `1_update`, `2_run`, and `5_push` statuses normalized to `Success`, `Failed`, `Running`, `Not Run`, or `Unknown`
- **THEN** detailed run date, started time, finished time, and short error summary remain outside the Dashboard summary

#### Scenario: Pipeline run rows are missing
- **WHEN** no matching `pipeline_runs` rows exist
- **THEN** Dashboard and System views display `Unknown`, `Not Run`, `empty`, or `degraded` as supported by the contract
- **THEN** neither view fabricates a Normal status

#### Scenario: Broker submission is disabled
- **WHEN** broker order submission configuration is disabled
- **THEN** the system summary displays `Broker Order Submission — DISABLED`
- **THEN** disabled broker submission is not classified as an error

## REMOVED Requirements

### Requirement: Dashboard shall provide six lazy-loaded decision tabs
**Reason**: Detailed market and operational workflows are being separated into dedicated views so Dashboard remains a concise daily summary.

**Migration**: Replace the six-tab navigation with summary sections and links. Existing APIs remain available for compatible consumers.

### Requirement: Market Snapshot tab shall show breadth and traffic-light metrics
**Reason**: Full breadth and traffic-light detail no longer belongs in the summary-only Dashboard.

**Migration**: Display only evidence-backed compact market states on Dashboard and route detailed analysis to its verified owning page or API consumer.

### Requirement: Institutional tab shall show three-institution flow and synchronized buy candidates
**Reason**: Detailed institutional lists belong to dedicated stock or market analysis rather than the Dashboard summary layer.

**Migration**: Preserve `/api/market/institutional`; surface per-stock institutional evidence in Stock Analysis and only compact aggregate state on Dashboard.

### Requirement: Technical Strength tab shall show market strength snapshots
**Reason**: Full technical leader and RSI groups are too detailed for the revised Dashboard role.

**Migration**: Preserve `/api/market/technical`; show only compact technical state on Dashboard and stock-specific metrics in Stock Analysis.

### Requirement: Margin tab shall show margin, short, and short-to-margin risk lists
**Reason**: Full margin and short lists are operational detail rather than top-level daily summary.

**Migration**: Preserve `/api/market/margin` and show a compact evidence-backed Margin state on Dashboard; detailed presentation requires a verified destination.
