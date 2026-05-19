## ADDED Requirements

### Requirement: DB_URL is the app database configuration surface

The application SHALL use `DB_URL` as the public database configuration surface.

#### Scenario: Application database connection is configured
- **WHEN** the app runtime needs a database connection
- **THEN** it SHALL resolve the connection through `DB_URL`

### Requirement: Application DB_URL examples use non-root credentials

Documentation and examples SHALL recommend non-root database credentials for application runtime.

#### Scenario: .env.example documents DB_URL
- **WHEN** `.env.example` shows application database configuration
- **THEN** the example `DB_URL` SHALL use a non-root app user DSN

#### Scenario: README documents DB_URL
- **WHEN** `README.md` documents application database configuration
- **THEN** it SHALL describe `DB_URL` as the app runtime DSN
- **AND** it SHALL recommend non-root credentials

### Requirement: Database initialization credentials are separate from app runtime DB_URL

The system SHALL keep database initialization credentials conceptually separate from application runtime `DB_URL`.

#### Scenario: docker-compose initializes the database
- **WHEN** database initialization credentials are present in compose
- **THEN** they SHALL NOT replace or redefine the app runtime `DB_URL` contract
