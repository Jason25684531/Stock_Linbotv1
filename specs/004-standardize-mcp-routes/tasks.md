# Tasks: TWSE MCP Server 路由標準化

**Input**: Design documents from `/specs/004-standardize-mcp-routes/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Include focused pytest coverage because the feature spec requires direct-route success, contract parity, and error-semantics validation.  
**Organization**: Tasks are grouped by user story so each route-standardization outcome can be implemented and verified independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (`[US1]`, `[US2]`, `[US3]`)
- Every task includes exact file paths for implementation traceability

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the shared verification scaffold used by all route-standardization work.

- [X] T001 Create shared Flask route test scaffold for standardized MCP tool routes in `test/test_richmenu_mcp_server_routes.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the shared dispatch and endpoint mapping layer before any user story work begins.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Define shared `/v1/tools/*` dispatch helpers, request validation reuse, and response-builder entry points in `scripts/twse_mcp_server.py`
- [X] T003 [P] Define canonical tool-route endpoint constants and request-path mapping for `TWSEMCPClient` in `tool/mcp_client.py`
- [X] T004 Add reusable response-parity and error-envelope assertions in `test/test_richmenu_mcp_server_routes.py`

**Checkpoint**: Foundation ready. User story implementation can begin.

---

## Phase 3: User Story 1 - 讓 MCP Client 直接命中標準工具路由 (Priority: P1) 🎯 MVP

**Goal**: Expose the three standardized tool routes and make `TWSEMCPClient` use them as the direct success path.

**Independent Test**: POST valid payloads to `/v1/tools/get_company_basic_info`, `/v1/tools/get_market_statistics`, and `/v1/tools/get_foreign_investment`, then verify `TWSEMCPClient` succeeds without fallback behavior.

### Tests for User Story 1

- [X] T005 [P] [US1] Add direct-success route tests for the three standardized tool endpoints in `test/test_richmenu_mcp_server_routes.py`
- [X] T006 [P] [US1] Update direct-hit client tests for canonical tool routes and no-normal-path-fallback behavior in `test/test_mcp_integration.py`

### Implementation for User Story 1

- [X] T007 [US1] Implement dynamic standardized tool-route dispatch for `/v1/tools/<tool_name>` in `scripts/twse_mcp_server.py`
- [X] T008 [US1] Implement `get_company_basic_info` issuer filtering and single-record success payload assembly in `scripts/twse_mcp_server.py`
- [X] T009 [US1] Remove 404/405 compatibility fallback from `get_company_basic_info`, `get_market_statistics`, and `get_foreign_investment` in `tool/mcp_client.py`
- [X] T010 [US1] Align quickstart verification commands with the final direct-hit client flow in `specs/004-standardize-mcp-routes/quickstart.md`

**Checkpoint**: Standardized tool routes exist and `TWSEMCPClient` can use them directly as the primary contract.

---

## Phase 4: User Story 2 - 保持新舊路由回應契約一致 (Priority: P2)

**Goal**: Ensure standardized tool routes and legacy endpoints share the same success semantics, validation behavior, and retryable failure contract.

**Independent Test**: For the same valid and invalid inputs, compare standardized tool-route responses against the legacy response contract and confirm required fields and error envelopes remain consistent.

### Tests for User Story 2

- [X] T011 [P] [US2] Add success-response parity tests between `/v1/tools/*` and legacy `/v1/*` MCP routes in `test/test_richmenu_mcp_server_routes.py`
- [X] T012 [P] [US2] Add validation-error and upstream-failure parity tests for standardized tool routes in `test/test_richmenu_mcp_server_routes.py`

### Implementation for User Story 2

- [X] T013 [US2] Refactor legacy route handlers to reuse the shared standardized response builders in `scripts/twse_mcp_server.py`
- [X] T014 [US2] Align canonical dataset parsing and error semantics with the standardized route contract in `tool/mcp_client.py`

**Checkpoint**: New and legacy routes share one response contract and one validation/error model.

---

## Phase 5: User Story 3 - 降低日後整合與診斷成本 (Priority: P3)

**Goal**: Make route intent obvious in logs and documentation so future integration and debugging do not depend on guessing whether fallback intervened.

**Independent Test**: Route-level tests and documentation review show the canonical tool name, correlation ID, and expected endpoint without needing to inspect fallback chains.

### Tests for User Story 3

- [X] T015 [P] [US3] Add route-intent and correlation-id logging assertions for standardized tool dispatch in `test/test_richmenu_mcp_server_routes.py`

### Implementation for User Story 3

- [X] T016 [US3] Add structured tool-route dispatch logging and explicit unknown-tool error handling in `scripts/twse_mcp_server.py`
- [X] T017 [US3] Update canonical route guidance, fallback-removal notes, and operator examples in `README.md`
- [X] T018 [US3] Update `TWSEMCPClient` docstrings and route-intent comments for canonical `/v1/tools/*` usage in `tool/mcp_client.py`

**Checkpoint**: Canonical route intent is visible in logs, client code, and operator documentation.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validate the whole feature and close cross-story documentation/regression gaps.

- [X] T019 [P] Run focused route and client pytest coverage for `test/test_richmenu_mcp_server_routes.py` and `test/test_mcp_integration.py`
- [X] T020 [P] Run regression integration coverage for `test/test_richmenu_mcp_integration.py`
- [X] T021 Validate Python problems/syntax state for `scripts/twse_mcp_server.py` and `tool/mcp_client.py`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Story 1 (Phase 3)**: Starts after Foundational and delivers the MVP route contract.
- **User Story 2 (Phase 4)**: Depends on User Story 1 because parity requires the standardized routes to exist.
- **User Story 3 (Phase 5)**: Depends on User Story 1 and can proceed after the canonical routes/logging hooks exist.
- **Polish (Phase 6)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **US1**: No dependency on other stories once Foundational is complete.
- **US2**: Depends on US1 standardized routes and direct client path being available.
- **US3**: Depends on US1 canonical route names and dispatch path existing; can run independently of US2 once that baseline is in place.

### Within Each User Story

- Tests must be written before implementation and should fail against the old behavior first.
- Shared server helpers precede route-specific handlers.
- Route handlers precede client cleanup.
- Documentation/logging updates follow once canonical behavior is stable.

### Parallel Opportunities

- `T003` can run in parallel with `T004` after `T002` starts.
- `T005` and `T006` can run in parallel because they touch different test files.
- `T011` and `T012` can run in parallel within US2.
- `T019` and `T020` can run in parallel during final validation.

---

## Parallel Example: User Story 1

```text
Task: "Add direct-success route tests for the three standardized tool endpoints in test/test_richmenu_mcp_server_routes.py"
Task: "Update direct-hit client tests for canonical tool routes and no-normal-path-fallback behavior in test/test_mcp_integration.py"
```

## Parallel Example: User Story 2

```text
Task: "Add success-response parity tests between /v1/tools/* and legacy /v1/* MCP routes in test/test_richmenu_mcp_server_routes.py"
Task: "Add validation-error and upstream-failure parity tests for standardized tool routes in test/test_richmenu_mcp_server_routes.py"
```

## Parallel Example: User Story 3

```text
Task: "Add route-intent and correlation-id logging assertions for standardized tool dispatch in test/test_richmenu_mcp_server_routes.py"
Task: "Update canonical route guidance, fallback-removal notes, and operator examples in README.md"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational.
3. Complete Phase 3: User Story 1.
4. Validate direct hits to all three `/v1/tools/*` endpoints.
5. Stop and confirm `TWSEMCPClient` no longer needs fallback for normal success cases.

### Incremental Delivery

1. Deliver US1 to establish the canonical contract.
2. Add US2 to guarantee parity with legacy routes and error semantics.
3. Add US3 to improve logs and operator-facing documentation.
4. Finish with focused regression and Problems-panel validation.

### Parallel Team Strategy

1. One developer completes server dispatch foundations in `scripts/twse_mcp_server.py`.
2. One developer updates client routing semantics in `tool/mcp_client.py` after the dispatch contract is stable.
3. One developer expands route/client tests and documentation in `test/test_richmenu_mcp_server_routes.py`, `test/test_mcp_integration.py`, and `README.md`.

---

## Notes

- `[P]` tasks are limited to different files or logically independent validation work.
- Keep all DB-facing data shapes compatible with existing `tool/db_helper.py` consumers.
- Legacy `/v1/*` endpoints may remain temporarily, but they must not diverge from the canonical `/v1/tools/*` contract.
- Avoid reintroducing fallback as a normal success path in `tool/mcp_client.py`.