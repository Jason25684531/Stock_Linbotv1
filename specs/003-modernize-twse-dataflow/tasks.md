# Tasks: TWSE MCP 現代化資料流與動態互動升級

**Input**: Design documents from `/specs/003-modernize-twse-dataflow/`  
**Prerequisites**: `plan.md`, `spec.md`

**Tests**: 每個 Python 修改節點都要執行語法與型別檢查；最後執行 focused pytest。  
**Organization**: 依照 user request 的修改順序安排，先做 `tool/mcp_client.py`，再做呼叫端整合，最後補測試與驗證。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行處理（不同檔案、且不依賴未完成任務）
- **[Story]**: 對應的使用者故事（`[US1]`、`[US2]`、`[US3]`）

## Phase 1: Setup

- [X] T001 [US1] 建立 `tool/mcp_client.py` 的相容層；預期：新增 `TWSEMCPClient`、`/v1/tools/*` 端點封裝、HTTP 500 安全回傳 `None`、欄位正規化入口；驗證：Pylance syntax/type check + focused unit test；依賴：無。
- [X] T002 [P] [US1] 補齊 `specs/003-modernize-twse-dataflow/plan.md` 與本任務檔；預期：implementation artifact 完整；驗證：check-prerequisites 可通過；依賴：無。

## Phase 2: Tests

- [X] T003 [US1] 新增 `test/test_mcp_integration.py`；預期：mock HTTP 500 時 `TWSEMCPClient` 安全回傳 `None`；驗證：pytest focused run；依賴：T001。

## Phase 3: Core

- [X] T004 [US1] 修改 `1_update_database.py`；預期：沿用既有 DB 寫入函式與參數，只抽換資料來源到 `TWSEMCPClient` 相容 API；驗證：syntax/type check + focused pytest；依賴：T001。
- [X] T005 [US1] 修改 `tool/update_financials_mops.py` 與 `tool/update_history_financials.py`；預期：財報更新統一走 `TWSEMCPClient`，資料 mapping 留在 client；驗證：syntax/type check；依賴：T001。

## Phase 4: Integration

- [X] T006 [US2] 修改 `app.py` 與 `scripts/setup_rich_menu.py`；預期：Rich Menu postback 與互動回覆明確使用 `TWSEMCPClient` 相容 API，保留 `action=market_summary|chip_trend|random_strategy`；驗證：syntax/type check + existing rich menu tests；依賴：T001。
- [X] T007 [US3] 修改 `tool/news_agent.py`；預期：LangChain tools 由 `TWSEMCPClient` 提供資料，查詢失敗時明確揭露限制；驗證：syntax/type check；依賴：T001。

## Phase 5: Polish

- [X] T008 [US1] 修改 `README.md`；預期：補充 MCP 相容 client 與測試方式；驗證：人工檢查；依賴：T004-T007。
- [X] T009 [US1] 執行 focused validation；預期：`test/test_mcp_integration.py` 與 `test/test_richmenu_mcp_integration.py` 通過，Problems panel 無新語法錯誤；驗證：pytest + get_errors；依賴：T003-T008。
- [X] T010 [US1] 重要節點提交 Git commit；預期：至少在 client 完成、整合完成、測試完成三個節點留下 commit；驗證：git log；依賴：對應階段完成。

## Dependencies & Execution Order

- T001 是所有程式修改的阻塞前置。
- T003 必須在 T001 後才能寫出正確的 mock 測試。
- T004-T007 必須在 T001 後序列進行，以避免跨檔案對 client API 的不同假設。
- T008、T009 在所有程式修改完成後執行。
- T010 隨重要節點穿插執行，不得混入無關檔案。

## Implementation Notes

- 若 `/v1/tools/*` 與既有 server 路由存在命名差異，client 應以相容策略吸收差異，不修改 DB schema。
- `1_update_database.py` 必須保持 `tool.db_helper.upsert_stock_data()` 的介面不變。
- 新增測試以 mock transport 為主，不依賴本機 MCP server 真實啟動。