# Data Model: TWSE MCP Server 路由標準化

## Entity 1: ToolRouteDefinition

Represents one canonical `/v1/tools/*` route registered by the MCP server.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `tool_name` | `str` | Yes | One of `get_company_basic_info`, `get_market_statistics`, `get_foreign_investment` |
| `dataset` | `str` | Yes | Canonical response dataset name |
| `required_fields` | `list[str]` | Yes | Request fields required before dispatch |
| `handler_name` | `str` | Yes | Internal server function used to build the response |
| `consumer` | `str` | Yes | Primary client capability that consumes the route |

Validation rules:
- `tool_name` must be unique.
- `dataset` must map to exactly one response schema.
- Every registered tool route must have a concrete handler.

## Entity 2: ToolRouteRequest

Represents the request payload accepted by one standard tool route.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `correlation_id` | `str` | Yes | Shared tracing identifier |
| `market` | `Literal['TWSE', 'TPEx', 'ALL']` | Conditional | Required for market/chip routes |
| `trade_date` | `str` | Conditional | ISO date for daily datasets |
| `stock_id` | `str` | Conditional | Required for company basic info |
| `include_etfs` | `bool` | No | Used by market-statistics route |

Validation rules:
- `correlation_id` must be non-empty.
- `trade_date` must be a valid ISO date when present.
- `stock_id` must be non-empty for company basic info.

## Entity 3: ToolSuccessResponse

Represents the canonical success envelope shared by all tool routes.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `dataset` | `str` | Yes | Response dataset identity |
| `as_of_date` | `str` | Conditional | Used by daily routes |
| `market` | `str` | Conditional | Used by daily routes |
| `record` | `dict[str, Any]` | Conditional | Used for single-record company info convenience |
| `records` | `list[dict[str, Any]]` | Yes | Canonical list payload for client normalization |
| `meta` | `dict[str, Any]` | No | Includes `record_count` and other diagnostics |

Validation rules:
- `records` must always be present, even when empty.
- `dataset` must match the route definition.
- `record`, when present, must equal the first element of `records` for company basic info.

## Entity 4: CompanyBasicInfoRecord

Represents one normalized issuer/company snapshot returned by `get_company_basic_info`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `stock_id` | `str` | Yes | Requested issuer code |
| `trade_date` | `str` | Yes | Data date used for the snapshot |
| `stock_name` | `str` | No | Issuer/security display name |
| `open_price` | `float` | No | Daily snapshot value when available |
| `high_price` | `float` | No | Daily snapshot value when available |
| `low_price` | `float` | No | Daily snapshot value when available |
| `close_price` | `float` | No | Daily snapshot value when available |
| `volume` | `float` | No | Daily trading volume |
| `pe_ratio` | `float` | No | Normalized valuation field |
| `security_type` | `str` | No | Stock / ETF classification |

Validation rules:
- Returned `stock_id` must equal the requested `stock_id`.
- If no matching record exists, the route must fail explicitly instead of returning another issuer.

## Entity 5: MarketStatisticsRecord

Represents one normalized row returned by `get_market_statistics`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `stock_id` | `str` | Yes | Security identifier |
| `trade_date` | `str` | Yes | ISO trade date |
| `open_price` | `float` | Yes | Daily open |
| `high_price` | `float` | Yes | Daily high |
| `low_price` | `float` | Yes | Daily low |
| `close_price` | `float` | Yes | Daily close |
| `volume` | `float` | Yes | Daily volume |
| `pe_ratio` | `float` | No | Optional valuation field |
| `stock_name` | `str` | No | Display field |
| `security_type` | `str` | No | Stock / ETF classification |

Validation rules:
- Same semantic rules as the existing `stock_basic_snapshot` response.

## Entity 6: ForeignInvestmentRecord

Represents one normalized row returned by `get_foreign_investment`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `stock_id` | `str` | Yes | Security identifier |
| `trade_date` | `str` | Yes | ISO trade date |
| `foreign_buy` | `float` | Yes | Net foreign buy/sell |
| `trust_buy` | `float` | No | Optional trust net-buy |
| `dealer_buy` | `float` | No | Optional dealer net-buy |

Validation rules:
- Same semantic rules as the existing `foreign_investor_flow` response.

## Entity 7: ErrorEnvelope

Represents the canonical failure contract reused by all standard tool routes.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `error_code` | `str` | Yes | Structured error code |
| `message` | `str` | Yes | Human-readable description |
| `retryable` | `bool` | Yes | Indicates whether retry is meaningful |
| `correlation_id` | `str` | Yes | Request trace id |
| `details` | `dict[str, Any]` | No | Route-specific diagnostics |

Validation rules:
- Validation failures set `retryable=false`.
- Upstream failures set `retryable=true` when retrying is meaningful.