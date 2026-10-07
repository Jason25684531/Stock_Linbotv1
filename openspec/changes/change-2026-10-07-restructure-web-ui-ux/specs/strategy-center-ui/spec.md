## ADDED Requirements

### Requirement: Strategy Center shall expose one truthful catalog
The application SHALL provide an independent `/strategies` view containing the seven canonical legacy-backed strategies and `fundamental_g2g3_top5_reb60_score_weighted_v1`, with category, lifecycle or eligibility, aliases, and action capabilities derived from authoritative backend contracts.

#### Scenario: Catalog loads successfully
- **WHEN** an authenticated user opens `/strategies`
- **THEN** the catalog contains the seven legacy-backed canonical IDs and the Fundamental canonical ID
- **THEN** filters allow the user to view all, technical, fundamental, and institutional or equivalent verified categories

### Requirement: Canonical IDs shall remain strategy identities
The Strategy Center SHALL use canonical IDs for action payloads and SHALL treat V31, V33, V34, V35, V36, V37, and V38 only as presentation aliases for their existing canonical strategies.

#### Scenario: Legacy strategy is selected
- **WHEN** the user selects `Defensive Low Volatility` displayed with alias `V33`
- **THEN** the UI state and action contract retain `defensive_low_volatility` as the canonical identity
- **THEN** historical records are not rewritten to another identifier

#### Scenario: Fundamental strategy is displayed
- **WHEN** the catalog renders `fundamental_g2g3_top5_reb60_score_weighted_v1`
- **THEN** no V32 or other V alias is assigned
- **THEN** lifecycle and eligibility are shown from the Fundamental backend evidence rather than hardcoded as Production

### Requirement: Cross-category multi-selection shall be allowed
The catalog SHALL allow users to select legacy-backed and Fundamental entries together in transient UI state and SHALL show the selected count and identities in a sticky action bar.

#### Scenario: User selects technical and Fundamental strategies
- **WHEN** the user selects one technical strategy and the Fundamental strategy
- **THEN** both remain selected in the catalog
- **THEN** the sticky action bar displays both selections without merging their runtime implementations

### Requirement: Daily actions shall require backend-authorized capability
The Strategy Center MUST enable `套用到 Daily` only when every selected canonical strategy is authorized by the existing Daily selection contract, and SHALL explain each unsupported or unknown capability.

#### Scenario: All selected strategies support Daily
- **WHEN** every selected canonical strategy is accepted by the authoritative Daily settings contract
- **THEN** the Daily action is enabled
- **THEN** submission uses the existing compatible endpoint and canonical or supported alias resolution

#### Scenario: Fundamental is selected without Daily support
- **WHEN** the selected set includes Fundamental and no authoritative Daily capability exists for it
- **THEN** the Daily action is disabled or blocks submission with a clear capability reason
- **THEN** Fundamental is not written into the legacy-only `strategy_settings.json` contract

### Requirement: Backtest actions shall require backend-authorized capability
The Strategy Center MUST enable `執行組合回測` only when every selected canonical strategy is supported by the existing Backtest contract, and SHALL not hardcode presentation-only support.

#### Scenario: Selected set is Backtest-capable
- **WHEN** every selected strategy has verified Backtest capability
- **THEN** the Backtest action is enabled and transfers supported canonical selections into the existing Backtest workflow

#### Scenario: Capability is unavailable or unknown
- **WHEN** any selected strategy has unavailable or unknown Backtest capability
- **THEN** the Backtest action identifies the unsupported selections and does not submit them
- **THEN** the UI does not claim that Fundamental is Backtest-capable without backend evidence

### Requirement: Catalog enrichment shall remain backward compatible
Any enrichment of `/api/strategies` SHALL preserve existing response fields and meanings while adding only evidence-backed presentation metadata.

#### Scenario: Existing API consumer requests strategies
- **WHEN** an existing consumer calls `/api/strategies` after catalog enrichment
- **THEN** previously supported fields and seven legacy strategy identities remain compatible
- **THEN** added Fundamental or capability metadata does not alter StrategyRunner or StrategyRegistry routing semantics
