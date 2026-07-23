## ADDED Requirements

### Requirement: Compose healthcheck targets /health

`docker-compose.yaml` SHALL use `/health` as the application healthcheck target.

#### Scenario: Compose checks app health
- **WHEN** the app container healthcheck runs
- **THEN** it SHALL request `/health`

### Requirement: Compose healthcheck uses Python stdlib

`docker-compose.yaml` healthchecks SHALL use Python stdlib instead of assuming `curl` or `wget` exists.

#### Scenario: Minimal Python image is used
- **WHEN** the container image does not include `curl` or `wget`
- **THEN** the healthcheck SHALL still work through Python stdlib

### Requirement: Runtime docs match healthcheck behavior

Runtime documentation SHALL describe `/health` consistently with compose and code.

#### Scenario: Dashboard payload API is documented
- **WHEN** `README.md` describes dashboard diagnostics
- **THEN** it SHALL distinguish `/api/dashboard/health-check` from `/health`
- **AND** it SHALL state that `/api/dashboard/health-check` 不是容器 health endpoint
