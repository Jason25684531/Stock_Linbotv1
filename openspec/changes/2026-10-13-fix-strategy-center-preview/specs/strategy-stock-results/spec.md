## ADDED Requirements

### Requirement: Strategy previews shall render recommendation envelopes correctly
The Strategy Center SHALL render stock links from the recommendation API envelope's data.recommendations, and SHALL expose selection through both native checkboxes and their associated strategy name labels in desktop and mobile layouts.

#### Scenario: A selected strategy returns persisted stock recommendations
- **WHEN** the API returns a successful envelope containing data.recommendations with stock 2330
- **THEN** the preview renders a link to /stock-analysis?id=2330 with its stock identifier and optional name
- **THEN** the displayed count agrees with the visible recommendations

#### Scenario: User clicks a strategy name
- **WHEN** the user clicks a strategy name label in either catalog layout
- **THEN** its associated checkbox selection changes and the existing read-only preview flow updates
- **THEN** the results are shown above the catalog without requiring a separate execution action

### Requirement: Application errors shall remain retryable preview failures
The Strategy Center SHALL treat an API envelope with status error as a preview failure even when the HTTP status is 200, and SHALL preserve independent successful strategy results.

#### Scenario: HTTP request succeeds but recommendation loading fails
- **WHEN** the recommendation API returns HTTP 200 with status error and diagnostic warnings
- **THEN** the affected strategy displays an error and permits retry instead of claiming a missing or zero-candidate snapshot

### Requirement: Strategy capabilities shall have an understandable purpose
The Strategy Center SHALL explain that selection previews persisted snapshots, Daily applies settings, and the backtest action transfers selections into the backtest workflow. It SHALL explain that execution availability does not establish performance validation.

#### Scenario: Visitor reads the Strategy Center
- **WHEN** the page is rendered
- **THEN** these purposes and the distinction between execution capability and performance validation are visible
