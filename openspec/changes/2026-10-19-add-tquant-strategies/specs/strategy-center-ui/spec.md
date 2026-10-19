## MODIFIED Requirements

### Requirement: Strategy Center shall expose one truthful catalog
The application SHALL provide an independent `/strategies` view containing every registered canonical strategy (the seven original legacy-backed strategies and ported TQuant strategies) and `fundamental_g2g3_top5_reb60_score_weighted_v1`, with category, lifecycle or eligibility, aliases, source, and action capabilities derived from authoritative backend contracts. Category filters SHALL be a button group using `aria-pressed`, and each filter SHALL include exactly the catalog entries whose backend category belongs to it: 技術 (`Technical`), 品質 (`Quality / *`), 籌碼 (`Institutional`), 基本面 (`Fundamental`). Entries with source `tquant` SHALL show their source notebook and state that holdings, rebalance timing, and exits follow this project's backtest engine.

#### Scenario: Catalog loads successfully
- **WHEN** a visitor opens `/strategies` without signing in
- **THEN** the catalog contains the seven legacy-backed canonical IDs, every ported TQuant canonical ID, and the Fundamental canonical ID
- **THEN** filters allow the user to view all, technical, quality, institutional, and fundamental categories

#### Scenario: User filters technical strategies
- **WHEN** the user activates the 技術 filter
- **THEN** only entries with category `Technical` are listed, including ported technical strategies such as `macd_cross`
- **THEN** `quality_growth` and `quality_value_low_volatility` are not listed

#### Scenario: User filters fundamental strategies
- **WHEN** the user activates the 基本面 filter
- **THEN** ported strategies with category `fundamental` are listed together with the Fundamental runtime strategy

#### Scenario: Mobile catalog card shows capabilities
- **WHEN** the catalog renders on a mobile viewport
- **THEN** each card shows its alias when present, its source notebook when ported, and separately shows the Daily and Backtest unavailable reasons

### Requirement: Catalog enrichment shall remain backward compatible
Any enrichment of `/api/strategies` SHALL preserve existing response fields and meanings while adding only evidence-backed presentation metadata.

#### Scenario: Existing API consumer requests strategies
- **WHEN** an existing consumer calls `/api/strategies` after catalog enrichment
- **THEN** previously supported fields and seven legacy strategy identities remain compatible
- **THEN** added ported strategies, `source`, Fundamental, or capability metadata do not alter StrategyRunner or StrategyRegistry routing semantics
