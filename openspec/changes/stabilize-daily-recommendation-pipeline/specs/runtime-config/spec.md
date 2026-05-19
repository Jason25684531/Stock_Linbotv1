## ADDED Requirements

### Requirement: Single public model path variable

The system SHALL use `MODEL_PATH` as the single public environment variable for model path configuration.

#### Scenario: MODEL_PATH is provided
- **WHEN** `MODEL_PATH` is set
- **THEN** model load, save, and read callers SHALL resolve the model path through the shared model-path contract

#### Scenario: MODEL_PATH is not provided
- **WHEN** `MODEL_PATH` is not set
- **THEN** the system SHALL default to `ML_Data/pkl/stock_ai_model.pkl`

### Requirement: No competing model path public config

The system SHALL NOT introduce another public environment variable that competes with `MODEL_PATH`.

#### Scenario: Runtime config is documented
- **WHEN** runtime config is described in `.env.example` or `README.md`
- **THEN** `MODEL_PATH` SHALL be documented as the public model path variable
- **AND** the default SHALL be `ML_Data/pkl/stock_ai_model.pkl`
