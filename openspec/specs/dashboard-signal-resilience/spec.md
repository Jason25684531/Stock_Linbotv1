## Purpose
Define resilience requirements for dashboard live-signal assembly so degraded AI or news dependencies never block successful signal delivery.

## Requirements

### Requirement: Dashboard stock-news enrichment shall fail fast
The system SHALL enforce a shared timeout and consecutive-failure breaker for Gemini-backed stock-news enrichment used by dashboard live signals so that technical and chip-based candidate selection can complete without waiting on stalled news analysis.

#### Scenario: Stock-news enrichment exceeds the latency budget
- **WHEN** /api/daily-signals has already selected candidate stocks and the Gemini-backed stock-news step exceeds 3 seconds
- **THEN** the system skips the remaining stock-news enrichment work for that response
- **THEN** the API still returns HTTP 200 with the selected signals and strategy metadata

#### Scenario: Consecutive Gemini failures open the breaker
- **WHEN** the configured threshold of consecutive Gemini stock-news failures is reached
- **THEN** subsequent /api/daily-signals requests bypass stock-news enrichment until the cooldown window expires
- **THEN** the API continues returning technical and chip-based signals without waiting on Gemini

### Requirement: Dashboard live-signal cards shall support degraded responses
The dashboard SHALL show a clear live-signal loading state during strategy switches and SHALL render signal cards even when AI score or news enrichment fields are absent because fallback was triggered.

#### Scenario: Strategy switch begins
- **WHEN** a user changes the selected strategy or Top N value on the dashboard
- **THEN** the interface shows a loading state that communicates live-signal retrieval is in progress
- **THEN** the loading message remains accurate even if AI/news enrichment is skipped

#### Scenario: Response contains no news details
- **WHEN** /api/daily-signals returns signals with empty news_reason_items and null or missing ai_score values
- **THEN** the dashboard renders each signal card using the available price and technical fields
- **THEN** the dashboard hides the news panel instead of showing broken placeholders or blocking the card layout

### Requirement: News enrichment fallback shall be non-fatal
The system MUST treat skipped or failed dashboard stock-news enrichment as a degradable condition rather than a fatal API error when candidate selection itself succeeds.

#### Scenario: Gemini returns service unavailable during signal assembly
- **WHEN** candidate selection succeeds but Gemini returns timeout, 503, or breaker-open status while building dashboard signal details
- **THEN** /api/daily-signals responds with the valid signal payload instead of a server error
- **THEN** news-specific fields are empty or defaulted in a way that keeps the dashboard functional

### Requirement: Dashboard live-signal API shall expose canonical recommendation resolution metadata
The `/api/daily-signals` response SHALL expose the same canonical recommendation resolution metadata used by other user-facing recommendation channels.

#### Scenario: API serves same-day persisted snapshot
- **WHEN** `/api/daily-signals` resolves candidate rows from a same-day persisted snapshot for the requested strategy
- **THEN** the response includes `requested_date`, `recommendation_date`, and `fallback_used = false`
- **THEN** the dashboard can render the signal date without inferring it from the card contents

#### Scenario: API serves strategy fallback snapshot
- **WHEN** `/api/daily-signals` cannot find a same-day persisted snapshot for the requested strategy and resolves an older persisted snapshot
- **THEN** the response includes the older `recommendation_date`, `fallback_used = true`, and a warning that identifies the result as non-current
- **THEN** the dashboard receives enough metadata to display the fallback state without custom per-strategy heuristics

#### Scenario: API serves same-day heartbeat
- **WHEN** `/api/daily-signals` resolves a same-day heartbeat for the requested strategy
- **THEN** the response returns an empty `signals` array with the same canonical date metadata
- **THEN** the dashboard can render a true zero-candidate state instead of guessing whether data is missing