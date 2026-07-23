## Purpose
Define the canonical Flex-message behavior for Line rich menu macro summary, journal reflection, and strategy selection flows so these postbacks return consistent card-based experiences across supported strategies.

## Requirements

### Requirement: Rich Menu macro summary shall render a unified Flex card
The system SHALL respond to `macro_summary` postbacks with a single Flex message that combines the current trading day's macro news summary, market breadth snapshot, and institutional chip-flow snapshot in one card.

#### Scenario: All summary sources are available
- **WHEN** the user triggers the `macro_summary` postback and news, market, and chip data are all available
- **THEN** the system returns a Flex message instead of a text message
- **THEN** the card shows the summary date, macro news highlights, market breadth metrics, and institutional net-flow metrics in the same response

#### Scenario: One summary source is unavailable
- **WHEN** any one of the news, market, or chip sources fails or returns no data during macro summary assembly
- **THEN** the system still returns a Flex message
- **THEN** the unavailable section is presented as a clear degraded or empty state within the card rather than falling back to text-only output

### Requirement: Journal reflection shall begin with a strategy selection Flex card
The system SHALL respond to `journal_reflection` postbacks with a Flex message that asks the user to choose which registered strategy's backtest reflection to view.

#### Scenario: User opens journal reflection from Rich Menu
- **WHEN** the user triggers the `journal_reflection` postback
- **THEN** the system returns a Flex message with strategy buttons for all registered strategies, including V31 through V38 when they are registered
- **THEN** each button uses postback data in the form `action=backtest_reflect&strategy=<strategy-key>`

### Requirement: Strategy-specific backtest reflection shall return a Flex summary
The system SHALL respond to `backtest_reflect` postbacks with a Flex message that presents a strategy backtest summary using persisted database data or the latest fallback CSV data.

#### Scenario: Strategy backtest data exists
- **WHEN** the postback payload contains a valid strategy key and backtest data exists for that strategy
- **THEN** the system returns a Flex message labeled as a strategy backtest summary
- **THEN** the card includes the strategy name, ROI, win rate, approximate MDD, trade count, and rule-based reflection suggestions

#### Scenario: Strategy backtest data does not exist
- **WHEN** the postback payload contains a valid strategy key but no backtest data exists for that strategy
- **THEN** the system returns an empty-state Flex message explaining that the strategy has no backtest data yet
- **THEN** the response does not fall back to a text message

#### Scenario: Strategy parameter is missing for reflection
- **WHEN** the `backtest_reflect` payload does not contain a `strategy` parameter
- **THEN** the system returns an empty-state Flex message asking the user to select a strategy again

### Requirement: Strategy stock selection shall begin with a Flex strategy picker
The system SHALL respond to `choose_strategy` postbacks with a Flex message that asks the user to choose which registered strategy's stock-selection view to open.

#### Scenario: User opens strategy selection from Rich Menu
- **WHEN** the user triggers the `choose_strategy` postback
- **THEN** the system returns a Flex message with buttons for all registered strategies, including V31 through V38 when they are registered
- **THEN** each button uses postback data in the form `action=strategy_select&strategy=<strategy-key>`

### Requirement: Strategy stock selection results shall remain Flex-only and backward compatible
The system SHALL respond to `strategy_select` postbacks, and the legacy `select_strategy` alias, by returning the strategy's stock-selection result as a Flex message or a Flex empty state.

#### Scenario: New strategy selection payload returns recommendations
- **WHEN** the postback payload contains `action=strategy_select` with a valid strategy key and recommendation data exists
- **THEN** the system returns the existing strategy recommendation Flex output for that strategy
- **THEN** the response does not include a text-only analysis message

#### Scenario: Legacy strategy selection payload remains supported
- **WHEN** the postback payload contains `action=select_strategy` with a valid strategy key
- **THEN** the system routes the request to the same strategy-selection result flow used by `strategy_select`
- **THEN** the response remains a Flex message

#### Scenario: Strategy selection has no data or no parameter
- **WHEN** the strategy-selection result flow is invoked without a strategy parameter or without recommendation data for the selected strategy
- **THEN** the system returns an empty-state Flex message that explains the issue and guides the user to reselect a strategy