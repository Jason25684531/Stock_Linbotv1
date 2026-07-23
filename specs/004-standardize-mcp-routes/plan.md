# Implementation Plan: TWSE MCP Server 路由標準化

**Branch**: `004-standardize-mcp-routes` | **Date**: 2026-04-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/004-standardize-mcp-routes/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

在 `scripts/twse_mcp_server.py` 將現有 `/v1/stock-basic-snapshot`、`/v1/foreign-investor-flow` 與資料組裝邏輯向上抽成標準工具路由層，新增 `/v1/tools/get_company_basic_info`、`/v1/tools/get_market_statistics`、`/v1/tools/get_foreign_investment` 三個正式端點，並讓 `tool/mcp_client.py` 改為直接命中這些工具路由而不再保留 404/405 fallback。設計重點是重用既有正規化函式與錯誤包裝，避免資料契約分裂，同時維持 `db_helper` 既有可接收的 JSON 形狀。

## Technical Context

**Language/Version**: Python 3.11.9 runtime in `myenv` (repo convention remains Python 3.10+)  
**Primary Dependencies**: Flask, pandas, requests, httpx, pytest  
**Storage**: N/A for server route layer itself; downstream compatibility must preserve `daily_market_data` and `financial_statements` contracts consumed through `tool/db_helper.py`  
**Testing**: pytest unit/integration tests, focused client/server route tests, existing rich menu and MCP integration tests  
**Target Platform**: Windows development host and Linux containerized runtime for `twse_mcp_server` / `stock_bot`  
**Project Type**: Python Flask internal HTTP service plus client library inside a larger trading automation application  
**Performance Goals**: New `/v1/tools/*` route path must not add extra network round trips; direct client success path should complete in one request without fallback retries on missing endpoints  
**Constraints**: No DB schema changes; preserve current success/error JSON envelope semantics; new route layer must live in `scripts/twse_mcp_server.py`; `tool/mcp_client.py` must remove backward-compatible fallback once server support exists; type hints and explicit error handling are mandatory  
**Scale/Scope**: Three new standard tool endpoints, one existing server entrypoint, one existing client module, and all current callers relying on market snapshot / foreign flow / company basic info compatibility

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Research Gate

- HTTP boundary: PASS. All external transport remains centralized in `scripts/twse_mcp_server.py` and `tool/mcp_client.py`; no application-layer file needs direct upstream HTTP changes for this feature.
- Type safety: PASS. New route handlers, dispatch table helpers, and client methods will preserve full type hints.
- Error handling: PASS. Existing `_error_response(...)` envelope and retryable semantics can be reused for the new tool routes; bare `except:` is not required.
- Dependency governance: PASS. No new library is required; feature is limited to restructuring existing Flask/httpx behavior.
- Documentation sync: PASS. This plan includes contract, quickstart, and data-model artifacts; implementation must update README and affected docstrings when the code change lands.

### Post-Design Re-check

- HTTP boundary: PASS. The design keeps upstream scraping/fetching in the server and removes client-side endpoint fallback branching.
- Type safety: PASS. `research.md`, `data-model.md`, and the contract document the request/response shapes and dispatch rules the implementation must satisfy.
- Error handling: PASS. The design standardizes validation and upstream-failure responses across both legacy and tool-style routes.
- Dependency governance: PASS. No new package drift is introduced.
- Documentation sync: PASS. Planning artifacts enumerate both server and client contract updates plus verification flow.

## Project Structure

### Documentation (this feature)

```text
specs/004-standardize-mcp-routes/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── twse-mcp-tools.openapi.yaml
└── tasks.md
```

### Source Code (repository root)

```text
README.md

scripts/
└── twse_mcp_server.py

tool/
└── mcp_client.py

test/
├── test_mcp_integration.py
├── test_richmenu_mcp_integration.py
└── test_richmenu_mcp_server_routes.py   # planned new validation file
```

**Structure Decision**: Keep the existing single-project layout and modify the current server/client modules in place. The server owns the new standardized tool routes; the client drops fallback logic and treats those routes as the canonical contract.

## Complexity Tracking

No constitution violations currently require exception tracking.
