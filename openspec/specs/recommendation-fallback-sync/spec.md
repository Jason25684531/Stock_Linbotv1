## Purpose
Define the canonical persisted recommendation resolution and completeness contract shared by Web, LINE, scheduled pushes, and repair tooling.
## Requirements
### Requirement: Recommendation queries shall resolve persisted strategy snapshots first
The system SHALL resolve user-facing recommendations from persisted `daily_recommendations` snapshots before considering any older fallback date, using the requested strategy as the primary lookup key.

#### Scenario: Same-day persisted snapshot exists
- **WHEN** a Web or Line recommendation request targets strategy `S` and the market anchor date has persisted candidate rows for strategy `S`
- **THEN** the system returns those persisted candidate rows for strategy `S`
- **THEN** `requested_date` and `recommendation_date` identify the anchor date and `fallback_used` is `false`

#### Scenario: Same-day heartbeat exists
- **WHEN** a Web or Line recommendation request targets strategy `S` and the market anchor date has only a heartbeat row for strategy `S`
- **THEN** the system returns an empty recommendation result for that same date
- **THEN** the response identifies the result as a completed zero-candidate snapshot instead of falling back to an older populated list

### Requirement: Recommendation queries shall fall back within the same strategy history only when the strategy-day snapshot is missing
The system SHALL backtrack to the latest older persisted snapshot for the same strategy only when no persisted candidate rows or heartbeat exists for that strategy on the market anchor date.

#### Scenario: Holiday request resolves to prior strategy snapshot
- **WHEN** the requested calendar date is later than the latest market date and strategy `S` has persisted rows on the latest market date
- **THEN** the system returns strategy `S` rows from that latest persisted date
- **THEN** the response reports the original `requested_date` and the older `recommendation_date`

#### Scenario: Partial strategy persistence triggers strategy-only fallback
- **WHEN** the market anchor date has persisted recommendation rows for strategy `A` but no rows at all for strategy `B`
- **THEN** a request for strategy `B` searches older persisted dates for strategy `B` only
- **THEN** the resolver SHALL NOT substitute strategy `A`'s date as if it were complete for strategy `B`

### Requirement: Daily persistence shall complete every user-visible strategy-day
The daily recommendation pipeline SHALL persist a completed snapshot for every strategy in the user-visible persistence set for each valid market date, even when optional factor sources are missing or degraded.

#### Scenario: Pipeline finishes a trading day with mixed candidate outcomes
- **WHEN** the daily pipeline runs for a valid market date across the user-visible persistence set
- **THEN** each strategy writes either candidate rows or one heartbeat row for that date
- **THEN** no user-visible strategy is left without a completion record for that market date

#### Scenario: Default active strategy changes
- **WHEN** the product changes which strategy is marked active for default display
- **THEN** the persistence coverage for other user-visible strategies remains unchanged
- **THEN** historical completeness continues to be measured against the full persistence set, not only the active strategy

#### Scenario: Optional factor data is missing during scoring
- **WHEN** a persistence strategy reaches AI scoring and one or more configured factor columns are missing, NaN, non-numeric, or zero-variance
- **THEN** the pipeline SHALL substitute neutral `0` Z-Score values for those model inputs
- **THEN** the strategy SHALL still persist candidate rows or one heartbeat row for that strategy-date

#### Scenario: News or large-holder enrichment fails
- **WHEN** news sentiment, stock-specific news mentions, or 400-share large-holder enrichment fails during the daily pipeline
- **THEN** the pipeline SHALL treat the missing enrichment as neutral model input
- **THEN** the failure SHALL NOT leave any user-visible persistence strategy without a completed `daily_recommendations` snapshot

### Requirement: Heartbeats shall represent authoritative zero-candidate completion
Heartbeat rows SHALL be treated as authoritative evidence that a strategy completed on a given market date and found zero candidates.

#### Scenario: Renderer receives same-day heartbeat result
- **WHEN** a user-facing channel resolves a same-day heartbeat for a strategy
- **THEN** it renders a zero-candidate state for that strategy-date
- **THEN** it does not present older candidate rows as if they were current for that strategy-date

#### Scenario: Completeness validation evaluates heartbeat rows
- **WHEN** completeness or backfill logic inspects a strategy-day that contains exactly one heartbeat row and no candidate rows
- **THEN** that strategy-day counts as complete pipeline output
- **THEN** it is not flagged as a missing recommendation snapshot

### Requirement: User-facing channels shall share recommendation resolution metadata and warning semantics
Web API responses, Line interactive recommendation replies, and scheduled Line pushes SHALL use the same recommendation resolution metadata fields and warning rules.

#### Scenario: Web and Line request the same strategy on the same day
- **WHEN** Web and Line resolve recommendations for the same requested date and strategy
- **THEN** they produce the same `recommendation_date`, `fallback_used`, and warning meaning
- **THEN** differences in rendering style do not change the underlying resolved snapshot contract

#### Scenario: Scheduled push resolves a fallback snapshot
- **WHEN** a scheduled push cannot find a same-day persisted snapshot for a display strategy and resolves an older strategy snapshot
- **THEN** the push content identifies the older recommendation date using the same warning semantics as Web and Line
- **THEN** the push SHALL NOT silently present the result as if it were a same-day snapshot

### Requirement: Recommendation gap repair shall validate completeness per strategy per market date
Backfill and diagnostic tooling SHALL detect recommendation gaps using a matrix of required strategies by valid market date.

#### Scenario: A date has rows for only one strategy
- **WHEN** a valid market date contains persisted rows for strategy `A` but no candidate or heartbeat rows for required strategy `B`
- **THEN** gap detection flags strategy `B` on that date as missing recommendation output
- **THEN** the date is not considered fully healthy until all required strategies have completion records

#### Scenario: Repair run rebuilds a missing strategy-day
- **WHEN** backfill reruns a missing strategy-day for a valid market date
- **THEN** the repaired output writes either candidate rows or a heartbeat row for that strategy-date
- **THEN** subsequent gap scans mark that strategy-date as complete

