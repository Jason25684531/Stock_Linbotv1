## MODIFIED Requirements

### Requirement: Dashboard live-signal cards shall support degraded responses
The Dashboard SHALL show an accurate loading state while compact recommendations are fetched and SHALL render available recommendation rows even when AI score or news enrichment fields are absent because fallback was triggered.

#### Scenario: Recommendation retrieval begins
- **WHEN** Dashboard requests the compact recommendation payload or the user explicitly refreshes it
- **THEN** the recommendation section shows a loading state that communicates retrieval is in progress
- **THEN** the loading message remains accurate even if AI or news enrichment is skipped

#### Scenario: Response contains no news details
- **WHEN** the recommendation contract returns candidates with empty news reason items and null or missing AI score values
- **THEN** Dashboard renders each compact row using available stock, strategy, direction, and canonical date fields
- **THEN** the UI omits unavailable enrichment instead of showing broken placeholders, blocking the table, or exposing raw JSON

#### Scenario: Deeper strategy controls move off Dashboard
- **WHEN** a user wants to change active strategies or inspect full strategy details
- **THEN** Dashboard links to Strategy Center rather than rendering the former full live-signal strategy control surface
