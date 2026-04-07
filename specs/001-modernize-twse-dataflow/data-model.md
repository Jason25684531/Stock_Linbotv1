# Data Model: TWSE 數據流現代化

## Entity 1: MCPServiceConfig

Represents the runtime configuration for the internal MCP service endpoint consumed by `tool/mcp_client.py`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `service_name` | `str` | Yes | Expected value: `twse_mcp_server` |
| `base_url` | `str` | Yes | Internal compose URL such as `http://twse-mcp-server:8080` |
| `health_path` | `str` | Yes | Relative path, default `/health` |
| `timeout_seconds` | `float` | Yes | Positive request timeout |
| `max_retries` | `int` | Yes | Bounded retry count for retryable failures |
| `backoff_seconds` | `list[float]` | Yes | Retry spacing policy |
| `default_headers` | `dict[str, str]` | No | Correlation and content headers |

Validation rules:
- `base_url` must be a valid HTTP URL.
- `health_path` must start with `/`.
- `timeout_seconds` must be greater than `0`.
- `max_retries` must be between `0` and `5`.

Relationships:
- One `MCPServiceConfig` is used by many `MCPRequestContext` instances.
- One `MCPServiceConfig` is referenced by compose health checks and the sync/async client facade.

## Entity 2: MCPRequestContext

Represents a typed request for one MCP dataset.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `dataset` | `Literal[...]` | Yes | `stock_basic_snapshot`, `foreign_investor_flow`, `historical_financial_statements` |
| `market` | `Literal[...]` | Yes | `TWSE`, `TPEx`, or `ALL` |
| `trade_date` | `date | None` | Conditional | Required for daily market datasets |
| `year` | `int | None` | Conditional | Required for historical financial statements |
| `quarter` | `int | None` | Conditional | Required for historical financial statements |
| `correlation_id` | `str` | Yes | Propagated through logs and error responses |
| `triggered_by` | `str` | Yes | Scheduler/script origin such as `1_update_database.py` |

Validation rules:
- Exactly one target mode must be valid: `trade_date`, or `year + quarter`.
- `quarter` must be between `1` and `4` when present.
- `correlation_id` must be non-empty.

Relationships:
- One `MCPRequestContext` yields one dataset response.
- One orchestration batch contains many `MCPRequestContext` values.

## Entity 3: StockBasicSnapshotRecord

Represents one normalized daily market row returned by the MCP service before persistence.

| Field | Type | Required | Maps to |
|-------|------|----------|---------|
| `stock_id` | `str` | Yes | `daily_market_data.stock_id` |
| `trade_date` | `date` | Yes | `daily_market_data.trade_date` |
| `open_price` | `float` | Yes | `daily_market_data.open_price` |
| `high_price` | `float` | Yes | `daily_market_data.high_price` |
| `low_price` | `float` | Yes | `daily_market_data.low_price` |
| `close_price` | `float` | Yes | `daily_market_data.close_price` |
| `volume` | `float` | Yes | `daily_market_data.volume` |
| `pe_ratio` | `float` | No | `daily_market_data.pe_ratio` |
| `stock_name` | `str` | No | Validation/logging only |
| `security_type` | `str` | No | Filtering only |

Validation rules:
- `stock_id` must be a supported security identifier string.
- `close_price` must be strictly positive to be persisted.
- `high_price` must be greater than or equal to `low_price`.
- Missing optional numeric values default to neutral values only where the persistence contract already accepts them.

Relationships:
- Merges with `ForeignInvestorFlowRecord` on `(stock_id, trade_date)`.

## Entity 4: ForeignInvestorFlowRecord

Represents one normalized institutional flow record for a trading day.

| Field | Type | Required | Maps to |
|-------|------|----------|---------|
| `stock_id` | `str` | Yes | `daily_market_data.stock_id` |
| `trade_date` | `date` | Yes | `daily_market_data.trade_date` |
| `foreign_buy` | `float` | Yes | `daily_market_data.foreign_buy` |
| `trust_buy` | `float` | No | `daily_market_data.trust_buy` |
| `dealer_buy` | `float` | No | `daily_market_data.dealer_buy` |

Validation rules:
- `foreign_buy`, `trust_buy`, and `dealer_buy` may be signed numeric values.
- Optional institutional columns default to `0` only when the MCP payload omits them and the mapped DB column remains required by downstream logic.

Relationships:
- Many `ForeignInvestorFlowRecord` rows merge into one `DailyMarketBundle` together with `StockBasicSnapshotRecord` rows.

## Entity 5: FinancialStatementRecord

Represents one quarterly financial statement record returned by the MCP service before persistence.

| Field | Type | Required | Maps to |
|-------|------|----------|---------|
| `stock_id` | `str` | Yes | `financial_statements.stock_id` |
| `year` | `int` | Yes | `financial_statements.year` |
| `quarter` | `int` | Yes | `financial_statements.quarter` |
| `revenue` | `int` | Yes | `financial_statements.revenue` |
| `rd_expense` | `int` | No | `financial_statements.rd_expense` |
| `operating_expense` | `int` | Yes | `financial_statements.operating_expense` |
| `operating_profit` | `int` | Yes | `financial_statements.operating_profit` |
| `eps` | `float` | No | `financial_statements.eps` |
| `unit` | `str` | Yes | Validation only |
| `operating_margin` | `float` | Derived | `financial_statements.operating_margin` |

Validation rules:
- `stock_id` must normalize to a 4-digit issuer code.
- `quarter` must be between `1` and `4`.
- `unit` must be recognized before conversion.
- `operating_margin` is computed from normalized values and not trusted blindly from upstream.

Relationships:
- One quarter batch contains many `FinancialStatementRecord` rows.
- Each persisted row is keyed by `(stock_id, year, quarter)`.

## Entity 6: DailyMarketBundle

Represents the merged payload that is ready for `tool.db_helper.upsert_stock_data`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `trade_date` | `date` | Yes | Bundle target date |
| `rows` | `list[dict]` | Yes | Flattened market rows ready for persistence |
| `missing_flow_ids` | `list[str]` | No | Securities missing institutional flow |
| `warnings` | `list[str]` | No | Non-fatal normalization issues |

Validation rules:
- No duplicate `(stock_id, trade_date)` rows may remain after merge.
- Rows must satisfy `daily_market_data` required-column expectations before upsert.

Relationships:
- Produced from `StockBasicSnapshotRecord` + `ForeignInvestorFlowRecord`.
- Consumed by `1_update_database.py` orchestration and `tool.db_helper.upsert_stock_data`.

## Entity 7: SyncExecutionStatus

Represents module-level execution state for one orchestration batch.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `batch_id` | `str` | Yes | Correlates one top-level run |
| `module_name` | `str` | Yes | Example: `stock_basic_snapshot` |
| `target_ref` | `str` | Yes | Trade date or `YYYY-QN` |
| `attempt_count` | `int` | Yes | Total attempts performed |
| `duration_ms` | `int` | Yes | Module elapsed time |
| `state` | `Literal[...]` | Yes | `pending`, `fetching`, `succeeded`, `partial_success`, `failed` |
| `retryable` | `bool` | Yes | Whether failure may be retried |
| `error_code` | `str | None` | No | Structured error identifier |
| `warning_count` | `int` | No | Count of non-fatal issues |

State transitions:

```text
pending -> fetching -> succeeded
pending -> fetching -> partial_success
pending -> fetching -> failed
failed -> fetching -> succeeded      # retry path
failed -> fetching -> partial_success
```

Relationships:
- One top-level update run aggregates multiple `SyncExecutionStatus` records.
- Compose health checks and application logs use this entity's values for diagnostics.

## Entity 8: NewsAgentToolRequest

Represents the typed input handed from `tool/news_agent.py` to a LangChain `BaseTool` wrapper backed by `MCPClient`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `tool_name` | `str` | Yes | LangChain-visible tool identifier |
| `dataset` | `str` | Yes | MCP dataset consumed by the tool |
| `market` | `str` | Yes | Default `TWSE` or `ALL` depending on prompt |
| `trade_date` | `date | None` | Conditional | Used for daily market context |
| `year` | `int | None` | Conditional | Used for financial context |
| `quarter` | `int | None` | Conditional | Used for financial context |
| `consumer_prompt` | `str` | Yes | Prompt fragment that frames tool usage |
| `cache_key` | `str` | No | Allows reuse within one news-analysis run |

Validation rules:
- `tool_name` must map to an allowed `BaseTool` implementation.
- Request fields must resolve to a valid `MCPRequestContext` before execution.

Relationships:
- `NewsAgentToolRequest` is transformed into `MCPRequestContext` and then into one of the dataset entities above.