## Purpose
Define guided conversational flows for LINE Rich Menu actions so diagnosis, summaries, and strategy picks return structured interactive responses.

## Requirements

### Requirement: Rich Menu stock diagnosis shall be conversational
The system SHALL treat the Rich Menu stock-diagnosis entry as a guided interaction that first prompts for a 4-digit stock code and only runs the existing diagnosis Flex reply after the user provides a valid code.

#### Scenario: User taps the diagnosis menu item
- **WHEN** a user taps the Rich Menu item for stock diagnosis
- **THEN** the bot replies with a prompt asking for a 4-digit stock code such as 2330
- **THEN** the bot stores enough short-lived interaction state to interpret the user's next valid reply as a diagnosis request

#### Scenario: User replies with a valid stock code after the prompt
- **WHEN** the user is in the pending diagnosis flow and sends a valid 4-digit stock code
- **THEN** the bot returns the existing multi-dimension stock diagnosis Flex card for that stock
- **THEN** the pending diagnosis state is cleared after the reply is generated

### Requirement: Rich Menu summary actions shall return Flex-based summaries
The system SHALL answer Rich Menu macro-summary and journal-reflection actions with Flex Messages instead of plain text blocks.

#### Scenario: User requests the macro summary
- **WHEN** a user taps the Rich Menu macro-summary action
- **THEN** the bot fetches the current market summary from the news agent flow
- **THEN** the bot replies with a readable Flex summary that includes a title, key outline, and AI commentary section

#### Scenario: User requests the journal reflection
- **WHEN** a user taps the Rich Menu journal-reflection action
- **THEN** the bot replies with a Flex card showing the currently enabled strategy or strategies
- **THEN** the card includes the latest total ROI, win rate, and whether today's enabled strategies currently have picks

### Requirement: Strategy picks shall require explicit strategy selection
The system MUST ask the user which strategy they want to inspect before sending strategy-specific daily-pick cards from the Rich Menu entry.

#### Scenario: User taps the strategy selection menu item
- **WHEN** a user taps the Rich Menu strategy-selection action
- **THEN** the bot replies with a selectable list of strategy choices covering V31 through V38
- **THEN** the reply clearly asks the user to choose the strategy they want to inspect

#### Scenario: User selects a specific strategy
- **WHEN** the user chooses one of the presented strategies
- **THEN** the bot returns that strategy's daily-pick Flex response for the current baseline date
- **THEN** the bot returns a structured empty-state reply if that strategy has no current picks
