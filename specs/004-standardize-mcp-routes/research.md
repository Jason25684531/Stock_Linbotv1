# Research: TWSE MCP Server 路由標準化

## Decision 1: Use a dynamic `/v1/tools/<tool_name>` dispatch route as the canonical entrypoint

- Decision: Implement a single dynamic Flask route for `/v1/tools/<tool_name>` backed by an internal dispatch table, while allowing thin explicit aliases only if implementation ergonomics require them.
- Rationale: The user explicitly allowed either a dynamic route or three explicit endpoints, and the dynamic route is the lower-maintenance option because validation, correlation-id handling, and error-envelope logic can be shared in one place.
- Alternatives considered: Create three entirely separate tool-route handlers. Rejected because it duplicates validation and response assembly logic already present in the legacy `/v1/*` handlers.

## Decision 2: Preserve the existing success/error JSON envelopes and reuse them for tool routes

- Decision: The new tool routes will return the same response envelope structure already emitted by the legacy MCP endpoints: `dataset`, date/period metadata, `records`, `meta`, and the same `error_code/message/retryable/correlation_id/details` error shape.
- Rationale: `tool/mcp_client.py`, `db_helper`-compatible normalization, and existing downstream tests already assume those response shapes. Standardizing route names should not create a second response contract.
- Alternatives considered: Introduce a new tool-specific envelope such as `{ tool_name, result }`. Rejected because it would force broader client and test rewrites without delivering additional value.

## Decision 3: Implement `get_company_basic_info` by filtering the existing normalized market snapshot dataset

- Decision: The server will derive company basic info from the normalized market snapshot path and filter it to the requested `stock_id`, rather than introducing a second upstream crawler or a new persistence model.
- Rationale: The server already knows how to normalize daily market snapshot fields into a db-helper-compatible shape. Filtering one normalized record satisfies the requested tool behavior with minimal additional moving parts.
- Alternatives considered: Add a brand-new upstream company profile source. Rejected because the feature request is route standardization, not data-source expansion.

## Decision 4: Remove client fallback once the server supports `/v1/tools/*`

- Decision: `tool/mcp_client.py` will stop treating `/v1/*` legacy endpoints as a normal fallback path for `get_company_basic_info`, `get_market_statistics`, and `get_foreign_investment`.
- Rationale: The feature goal is to let the client hit standard routes directly and precisely. Retaining fallback as a normal success path would keep the old ambiguity in place.
- Alternatives considered: Keep fallback indefinitely for safety. Rejected because it would undermine the main success criterion and keep server/client observability ambiguous.

## Decision 5: Keep legacy `/v1/*` routes temporarily, but make tool routes the canonical contract

- Decision: Legacy endpoints may remain in the server for transitional compatibility, but they will be implemented on top of the same shared helpers and will no longer be the primary contract used by the client.
- Rationale: This allows an incremental change that does not break unrelated callers while still letting the new standard become the official path.
- Alternatives considered: Delete all legacy routes in the same change. Rejected because that expands blast radius beyond the stated route-standardization goal.

## Decision 6: Validate route behavior with Flask-level contract tests and focused client tests

- Decision: Verification will include direct route tests for `/v1/tools/*` and focused client tests ensuring direct hits succeed without fallback branching.
- Rationale: This feature is a contract and dispatch change, so testing must verify both server behavior and client routing assumptions.
- Alternatives considered: Rely only on existing integration tests. Rejected because current tests cover client degradation behavior but do not prove the new server-side tool routes exist as a canonical contract.