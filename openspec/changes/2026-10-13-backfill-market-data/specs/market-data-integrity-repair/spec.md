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

### Requirement: Use valid daily history for recovery
The daily recovery MUST exclude weekends, dates without the market anchor, unsupported identifiers and duplicate stock-date rows before rolling calculations. Results on clean supported history MUST remain identical within rtol=1e-9 and atol=1e-12.

#### Scenario: Preserved historical anomalies exist
- **WHEN** duplicate rows, weekend rows or dates without the market symbol remain in the database
- **THEN** they do not influence the recovered daily indicators or recommendations

### Requirement: Validate margin balance enrichment
Margin enrichment MUST check the official source date, support the current TPEx tables response, and use short balance rather than short repayment.

#### Scenario: TPEx returns its current 20-column table
- **WHEN** margin enrichment reads a dated TPEx tables payload
- **THEN** it reads margin balance at index 6 and short balance at index 14, and rejects a stale date
