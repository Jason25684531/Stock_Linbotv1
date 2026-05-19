## Purpose
Define the canonical full-market common-stock universe so candidate discovery and strategy evaluation exclude derivative-style instruments by default.

## Requirements

### Requirement: Shared market-universe queries shall exclude non-common instruments
The system SHALL restrict full-market stock data used for candidate discovery to identifiers that are exactly four numeric digits and do not use excluded derivative-style prefixes such as 03 or 08.

#### Scenario: Full-market data contains mixed instruments
- **WHEN** get_stock_data() loads a trade date that contains common stocks, warrants, ETFs, inverse products, or alphanumeric identifiers
- **THEN** only rows with valid common-stock identifiers remain in the returned universe
- **THEN** any identifier longer than four characters, containing letters, or matching excluded prefixes is removed

### Requirement: Strategy candidate evaluation shall inherit the common-stock rule
All strategy candidate selection flows MUST operate on the shared common-stock-filtered universe so that no strategy returns derivative or warrant-like identifiers.

#### Scenario: Candidate data includes invalid identifiers
- **WHEN** a candidate dataset includes stock identifiers such as 0312, 0812, 2330, 00878, 12345, and ABCD
- **THEN** only the valid common-stock rows remain available to strategy filtering and ranking
- **THEN** the final candidate or recommendation list never contains the invalid identifiers