## ADDED Requirements

### Requirement: Stock Analysis shall be an independent routable view
The application SHALL provide `/stock-analysis` and SHALL accept `/stock-analysis?id=<stock_id>` without rendering the full analysis workspace on Dashboard.

#### Scenario: User follows a stock link
- **WHEN** the user follows a Dashboard search or recommendation link for stock `2330`
- **THEN** the browser navigates to `/stock-analysis?id=2330`
- **THEN** the Stock Analysis page requests authoritative data for `2330`

### Requirement: Stock Analysis shall render quote and data provenance
The page SHALL display available stock identity, quote, price change, OHLC, volume, and turnover fields without inventing missing values, and SHALL display the payload's normal, degraded, fallback, or unavailable state.

#### Scenario: Local fallback provides valid data
- **WHEN** the upstream MCP request fails but the existing local fallback returns valid stock data
- **THEN** the page displays `DEGRADED` and `Local fallback data`
- **THEN** available quote and analysis content remains usable

#### Scenario: A quote field is unavailable
- **WHEN** the stock-analysis payload does not contain an authoritative value for a quote field
- **THEN** the page displays an unavailable placeholder for that field
- **THEN** the page does not substitute a fabricated numeric value

### Requirement: Stock Analysis shall construct a financial chart from existing data
The page SHALL call `LightweightCharts.createChart(...)` and SHALL construct a candlestick series, MA5 line series, MA20 line series, MA60 line series, and volume histogram from the existing `/api/stock-analysis` schema without changing backend field meaning.

#### Scenario: Candle payload is returned
- **WHEN** `/api/stock-analysis` returns valid candles, volume, MA5, MA20, and MA60 values
- **THEN** the page creates a Lightweight Charts chart
- **THEN** it creates and populates the candlestick, three moving-average, and volume series

#### Scenario: Degraded payload contains local candles
- **WHEN** `/api/stock-analysis` reports degraded MCP status and returns valid local fallback candles and indicators
- **THEN** the same chart series are created and populated
- **THEN** the degraded badge remains visible near the chart

#### Scenario: Candle payload is unavailable
- **WHEN** no valid candle data can be resolved
- **THEN** the chart region displays an explicit unavailable state
- **THEN** raw JSON is not used as the primary replacement interface

### Requirement: Stock Analysis shall support usable time ranges
The chart SHALL offer 1M, 3M, 6M, 1Y, and ALL controls by using the existing backend timeframe contract when available or by filtering the existing returned history without rewriting the backend K-line algorithm.

#### Scenario: User selects a shorter range
- **WHEN** the chart has loaded and the user selects `3M`
- **THEN** every visible price, moving-average, and volume series uses a consistent three-month interval
- **THEN** original candle and indicator meanings are unchanged

### Requirement: Analysis cards shall preserve domain boundaries
The page SHALL separately present technical metrics, institutional flows, and per-strategy matches from authoritative payloads, and MUST NOT add scores from different strategies into a unified score.

#### Scenario: Multiple strategies selected the same stock
- **WHEN** persisted recommendation evidence identifies multiple matching strategies for a stock
- **THEN** each strategy appears as a separate match with its own canonical identity, alias if present, and lifecycle or eligibility evidence
- **THEN** no aggregate cross-strategy score is calculated

#### Scenario: Strategy-match evidence is unavailable
- **WHEN** no authoritative persisted match source can be resolved
- **THEN** the Strategy Matches card states that match data is unavailable
- **THEN** the browser does not rerun strategies to fabricate matches

### Requirement: Stock chart layout shall remain usable across supported viewports
The chart SHALL retain a usable minimum height, resize to its content width, and release chart/resize resources when its component is replaced.

#### Scenario: Viewport width changes
- **WHEN** the Stock Analysis content width changes on desktop, tablet, or mobile
- **THEN** the chart resizes through the chart API without collapsing to an unreadable height
- **THEN** the volume region remains visible
