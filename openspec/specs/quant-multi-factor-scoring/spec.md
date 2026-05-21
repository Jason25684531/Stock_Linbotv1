# quant-multi-factor-scoring Specification

## Purpose
TBD - created by archiving change refactor-quant-multi-factor-scoring. Update Purpose after archive.
## Requirements
### Requirement: Multi-factor matrix shall provide canonical model inputs
The system SHALL construct a canonical multi-factor matrix for model training and daily inference that includes technical, institutional, large-holder, and news sentiment factors.

#### Scenario: Matrix includes required factor families
- **WHEN** the system prepares model input rows for a market date
- **THEN** the matrix SHALL include RSI and moving-average bias technical factors
- **THEN** the matrix SHALL include institutional consecutive-buy days and institutional net buy value factors
- **THEN** the matrix SHALL include 400-share large-holder holding ratio and holding-ratio change factors when source columns are available
- **THEN** the matrix SHALL include a numeric AI news sentiment factor

#### Scenario: Optional source fields are unavailable
- **WHEN** large-holder, institutional, fundamental, or news source fields are missing from the input data
- **THEN** the matrix SHALL still return one row per eligible stock
- **THEN** missing optional raw factors SHALL be represented as neutral values before Z-Score fallback is applied

### Requirement: Cross-sectional Z-Score normalization shall be applied by trade date
The system SHALL normalize configured model factors with cross-sectional Z-Score values over the same trade date and market universe.

#### Scenario: Valid market sample exists
- **WHEN** a factor has valid numeric values for multiple stocks on a trade date
- **THEN** each stock's Z-Score SHALL be calculated as `(X - mean) / std` using that trade date's market mean and standard deviation
- **THEN** the resulting Z-Score column SHALL be available as the model input feature for that factor

#### Scenario: Factor has zero standard deviation
- **WHEN** every valid value for a factor on a trade date is identical
- **THEN** the Z-Score for that factor-date slice SHALL be `0` for every stock
- **THEN** model input assembly SHALL continue without raising an exception

### Requirement: Z-Score fallback shall treat missing feature data as market average
The system SHALL convert unusable feature values to `0` Z-Score so individual missing fields do not remove stocks from recommendations or crash stock detail views.

#### Scenario: Stock has missing factor value
- **WHEN** a stock has NaN, infinite, non-numeric, or absent data for a configured factor
- **THEN** the stock's Z-Score for that factor SHALL be `0`
- **THEN** the stock SHALL remain eligible for downstream strategy filtering and AI scoring

#### Scenario: Entire source is unavailable
- **WHEN** a factor source table, source column, or enrichment step is unavailable for a market date
- **THEN** the matrix SHALL create the corresponding Z-Score feature with value `0`
- **THEN** daily recommendation generation SHALL continue for every persistence strategy

### Requirement: News sentiment shall be encoded as a numeric factor
The system SHALL encode daily AI news sentiment produced by `news_agent.py` as a numeric model factor.

#### Scenario: Sentiment is bullish
- **WHEN** the daily news sentiment is bullish or stored with the existing localized bullish label
- **THEN** the numeric news sentiment factor SHALL be `1`

#### Scenario: Sentiment is neutral or missing
- **WHEN** the daily news sentiment is neutral, invalid, missing, or unavailable
- **THEN** the numeric news sentiment factor SHALL be `0`

#### Scenario: Sentiment is bearish
- **WHEN** the daily news sentiment is bearish or stored with the existing localized bearish label
- **THEN** the numeric news sentiment factor SHALL be `-1`

### Requirement: Training and inference shall use the same Z-Score feature contract
The system SHALL train XGBoost models and run daily inference with the same canonical Z-Score feature names.

#### Scenario: Model training prepares data
- **WHEN** `jobs/train_model.py` prepares training data for a strategy
- **THEN** it SHALL build the multi-factor Z-Score matrix before selecting model features
- **THEN** it SHALL train the strategy model using canonical Z-Score features rather than raw factor columns
- **THEN** it SHALL persist the feature list with the model artifact

#### Scenario: Daily inference loads model artifact
- **WHEN** `jobs/run_daily.py` loads a strategy model through `model_utils.load_model()`
- **THEN** it SHALL use the model artifact's stored feature list when available
- **THEN** every missing model feature column SHALL be created with value `0` before `predict_proba` is called

### Requirement: Daily recommendations shall persist market-relative AI confidence rankings
The system SHALL persist `daily_recommendations.ai_score` from model inference over Z-Score features so candidates are ranked by market-relative AI confidence.

#### Scenario: Strategy produces scored candidates
- **WHEN** a persistence strategy produces candidates and a compatible model returns probabilities
- **THEN** candidates SHALL be sorted by `ai_score` descending
- **THEN** the top persisted rows in `daily_recommendations` SHALL include the model probability as `ai_score`

#### Scenario: Model scoring is degraded
- **WHEN** model loading or scoring cannot produce probabilities for a strategy
- **THEN** daily recommendation persistence SHALL still complete with the existing degraded-score or heartbeat behavior
- **THEN** the failure SHALL NOT prevent other persistence strategies from completing

