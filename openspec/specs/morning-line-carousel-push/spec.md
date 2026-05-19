## Purpose
Define the canonical morning scheduled LINE push carousel, including ordering, degradation behavior, and uniform Flex sizing.

## Requirements

### Requirement: Morning scheduled push shall render a fixed three-card carousel
The system SHALL send the morning scheduled Line push as a Flex carousel containing exactly three cards in this order: market overview, news summary, and featured stock picks.

#### Scenario: Morning data is available
- **WHEN** the morning push flow builds a Flex payload with available market context, news summary, and recommendation data
- **THEN** the payload is a `FlexCarousel`
- **THEN** the carousel contains exactly three bubbles ordered as market overview, news summary, and featured picks

#### Scenario: Morning recommendations are unavailable
- **WHEN** the morning push flow cannot find featured picks for the selected baseline date
- **THEN** the system still returns a three-card Flex carousel
- **THEN** the featured-picks card is rendered as an empty state instead of dropping the carousel or falling back to a news-only message

### Requirement: Morning scheduled push shall use the latest available trading snapshot with explicit degradation
The system SHALL assemble morning market-overview and featured-picks content from a single baseline trading date and SHALL continue using the latest available trading snapshot when the current calendar day has not produced new market data yet.

#### Scenario: Current morning has no fresh trading snapshot yet
- **WHEN** the morning push runs before new same-day market or recommendation data exists
- **THEN** the system uses the latest available pipeline baseline date for both the market-overview card and the featured-picks card
- **THEN** those cards display that baseline date so the user can distinguish it from the current calendar morning

#### Scenario: Market inputs are incomplete
- **WHEN** market trend inputs for the baseline date are incomplete or unavailable
- **THEN** the market-overview card remains present in the carousel
- **THEN** the card displays a neutral degraded state indicating that market data is insufficient

### Requirement: Morning scheduled push shall use uniform carousel sizing and dated alt text
The system SHALL use one consistent Flex bubble size for every carousel bubble assembled by the scheduled push flow and SHALL identify the morning briefing in the message alt text.

#### Scenario: Morning carousel is built
- **WHEN** the system assembles the morning Flex carousel
- **THEN** every bubble in the carousel uses `size="mega"`
- **THEN** the message `alt_text` includes a morning identifier and the baseline date

#### Scenario: Evening scheduled push reuses scheduled carousel builders
- **WHEN** the evening push assembles overview, news, and picks bubbles from the same scheduled-push module
- **THEN** the bubble sizes in that carousel are also uniform
- **THEN** the payload does not mix `mega` and `giga` bubble sizes in a single carousel
