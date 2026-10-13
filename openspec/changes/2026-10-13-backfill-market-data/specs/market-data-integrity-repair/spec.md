## ADDED Requirements

### Requirement: Validate exchange dates before normalization
The canonical MCP fetchers MUST verify the authoritative date of every nonempty official payload before assigning a canonical trade_date.

#### Scenario: Exchange returns a different date
- **WHEN** an exchange returns rows for a date other than the requested date
- **THEN** ingestion rejects those rows before persistence

### Requirement: Reject incomplete market ingestion
An ALL-market response MUST reject a result with only one exchange populated. The updater MUST reject nonempty prices with unavailable institutional data and MUST preserve absent individual institutional values as NULL.

#### Scenario: TWSE institutional feed is not published
- **WHEN** TPEx has rows and TWSE has no rows
- **THEN** the ALL institutional response fails and no price batch is written using fabricated zero flows

#### Scenario: Individual symbol has no institutional row
- **WHEN** both exchange feeds are available but a symbol has no institutional record
- **THEN** its missing institutional values remain NULL through persistence
