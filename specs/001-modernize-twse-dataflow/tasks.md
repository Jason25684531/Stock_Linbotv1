# Tasks: TWSE 數據流現代化

**Input**: Design documents from `/specs/001-modernize-twse-dataflow/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/twse-mcp-service.openapi.yaml`, `quickstart.md`

**Tests**: 本次未額外拆出先寫測試的 TDD 任務；但每個任務都附帶可立即執行的驗證方式。  
**Organization**: 任務依使用者故事分組，並補上依賴順序、修改檔案、預期行為與驗證方法。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可平行處理（不同檔案、且不依賴未完成任務）
- **[Story]**: 對應的使用者故事（`[US1]`、`[US2]`、`[US3]`）
- 每個任務都必須在描述中寫出：修改檔案、預期行為、驗證方式、依賴

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 先把容器拓樸與依賴宣告固定下來，後續才能安全實作 `tool/mcp_client.py` 與健康檢查。

- [X] T001 修改 `docker-compose.yaml` 新增 `stock-network`；預期：`db`、`stock_bot`、`twse_mcp_server` 位於同一虛擬網路；驗證：執行 `docker compose config`。
- [X] T002 修改 `docker-compose.yaml` 為 `db` 加入健康檢查；預期：MySQL 先進入 `healthy` 才允許相依服務啟動；驗證：執行 `docker compose config`；依賴：T001。
- [X] T003 修改 `docker-compose.yaml` 加入 `stock_bot` 服務、環境變數與 `depends_on`；預期：bot 容器改用服務名稱連線 `db` 與 `twse_mcp_server`；驗證：執行 `docker compose config`；依賴：T002。
- [X] T004 修改 `docker-compose.yaml` 加入 `twse_mcp_server` 服務與健康檢查；預期：MCP 服務只在 compose 內網可達且可被 readiness 探測；驗證：執行 `docker compose config`；依賴：T003。
- [X] T005 [P] 修改 `requirements.txt`；預期：新增 `httpx` 與最小 LangChain `BaseTool` 依賴，並同步整理 Gemini 套件宣告；驗證：執行 `rg "httpx|langchain|google-genai|google-generativeai" requirements.txt`。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 建立所有故事共用的 MCP 傳輸邊界、設定來源與健康檢查入口。

**⚠️ CRITICAL**: 未完成此 Phase 前，不應開始實作任一使用者故事。

- [X] T006 [P] 修改 `config.py`；預期：集中定義 `MCP_BASE_URL`、逾時、重試次數與健康檢查相關設定；驗證：執行 `python -c "from config import Config; print(bool(Config.MCP_BASE_URL))"`；依賴：T005。
- [X] T007 修改 `tool/mcp_client.py`；預期：建立 `MCPClient`、請求 DTO 與錯誤類別骨架，明確覆蓋三個 MCP dataset；驗證：執行 `python -c "from tool.mcp_client import MCPClientError; print(MCPClientError.__name__)"`；依賴：T006。
- [X] T008 修改 `tool/mcp_client.py`；預期：加入 `httpx.AsyncClient` 生命週期與 JSON POST 反序列化流程；驗證：執行 `python -c "from tool.mcp_client import MCPClient; print(hasattr(MCPClient, '__aenter__'))"`；依賴：T007。
- [X] T009 修改 `tool/mcp_client.py`；預期：加入同步 facade 方法，讓 CLI 腳本可不直接管理 event loop；驗證：執行 `python -c "from tool.mcp_client import MCPClient; print(callable(MCPClient.fetch_stock_basic_snapshot_sync))"`；依賴：T008。
- [X] T010 修改 `tool/mcp_client.py`；預期：加入 bounded retry、backoff、correlation log 與顯式例外處理，不出現 bare `except:`；驗證：執行 `rg "retry|backoff|correlation|except Exception|except httpx" tool/mcp_client.py`；依賴：T009。
- [X] T011 [P] 修改 `app.py`；預期：新增 `/health` 路由供 `stock_bot` 健康檢查，不影響既有登入、Webhook 與 Dashboard；驗證：執行 `python -c "from app import app; c=app.test_client(); print(c.get('/health').status_code)"`；依賴：T003。

**Checkpoint**: Compose 拓樸、共用設定、MCP client 與 bot health endpoint 已就緒，使用者故事可以開始。

---

## Phase 3: User Story 1 - 穩定日常市場同步 (Priority: P1) 🎯 MVP

**Goal**: 讓每日個股基本資料與外資買賣超改由 MCP 供應，不再依賴舊的直接抓取流程。

**Independent Test**: 在容器或 host mode 執行一次 `python 1_update_database.py`，確認日線資料與外資欄位可以進入 `daily_market_data`，且 `2_rundaily.py` 不需改查詢契約。

- [X] T012 [US1] 修改 `1_update_database.py`；預期：在主流程中建立並注入 `MCPClient`，不再由業務函式直接碰觸 covered HTTP 邏輯；驗證：執行 `rg "MCPClient" 1_update_database.py`；依賴：T009、T010。
- [X] T013 [US1] 修改 `1_update_database.py`；預期：以 `MCPClient.fetch_stock_basic_snapshot_sync(...)` 取代個股基本資料來源；驗證：執行 `rg "stock_basic_snapshot|fetch_stock_basic_snapshot_sync" 1_update_database.py`；依賴：T012。
- [X] T014 [US1] 修改 `1_update_database.py`；預期：以 `MCPClient.fetch_foreign_investor_flow_sync(...)` 取代外資買賣超來源；驗證：執行 `rg "foreign_investor_flow|fetch_foreign_investor_flow_sync" 1_update_database.py`；依賴：T012。
- [X] T015 [US1] 修改 `1_update_database.py`；預期：將 MCP 個股資料與外資資料依 `stock_id`、`trade_date` 合併成既有 `daily_market_data` 欄位集合；驗證：執行 `rg "pd.merge|foreign_buy|trade_date" 1_update_database.py`；依賴：T013、T014。
- [X] T016 [US1] 修改 `1_update_database.py`；預期：保留 `tool/crawlers/chip_data_scraper.py` 的融資融券合併，但只作為 MCP 資料後置 enrich，不覆蓋 covered dataset；驗證：執行 `rg "fetch_margin_balance" 1_update_database.py`；依賴：T015。
- [X] T017 [US1] 修改 `1_update_database.py`；預期：`run_price_update` 實際走 MCP helper 路徑，covered dataset 的 live path 不再依賴 legacy fetcher；驗證：執行 `rg "run_price_update|fetch_twse_data|fetch_tpex_data" 1_update_database.py`；依賴：T016。
- [X] T018 [US1] 修改 `1_update_database.py`；預期：加入市場快照/外資資料缺失時的 partial-success 日誌與摘要，不因單一模組失敗刪除成功資料；驗證：執行 `rg "partial|warning|retry|success" 1_update_database.py`；依賴：T017。
- [X] T019 [US1] 修改 `1_update_database.py`；預期：移除或明確退場 `fetch_twse_data`、`fetch_tpex_data` 的 covered flow 敘述與實際主流程引用；驗證：執行 `rg "fetch_twse_data|fetch_tpex_data" 1_update_database.py`；依賴：T017。

**Checkpoint**: `1_update_database.py` 能在不直接抓 TWSE/TPEx covered endpoint 的前提下完成日常市場同步。

---

## Phase 4: User Story 2 - 歷史財報可安全回補 (Priority: P2)

**Goal**: 將單季財報更新與歷史回補統一改走 MCP financial contract。

**Independent Test**: 執行 `python tool/update_financials_mops.py --year <ROC年> --quarter <Q> --dry-run` 與 `python tool/update_history_financials.py --start-year <ROC年> --end-year <ROC年>`，確認兩條路徑都走 MCP，且財報欄位仍可落入既有 upsert 契約。

- [X] T020 [US2] 修改 `tool/mcp_client.py`；預期：加入 `historical_financial_statements` 請求方法與財報 payload 正規化入口；驗證：執行 `rg "historical_financial_statements" tool/mcp_client.py`；依賴：T010。
- [X] T021 [US2] 修改 `tool/update_financials_mops.py`；預期：單季財報更新改用 `MCPClient` 而非 `QuarterlyScraper`；驗證：執行 `rg "QuarterlyScraper|requests|read_html" tool/update_financials_mops.py`；依賴：T020。
- [X] T022 [US2] 修改 `tool/update_history_financials.py`；預期：歷史回補重用相同 MCP 財報抓取路徑，不再自行建立 scraper；驗證：執行 `rg "QuarterlyScraper" tool/update_history_financials.py`；依賴：T021。
- [X] T023 [US2] 修改 `1_update_database.py`；預期：`run_financial_update` 轉接到 MCP-backed quarter updater，讓每日同步與手動季度同步邏輯一致；驗證：執行 `rg "run_financial_update|update_quarter" 1_update_database.py`；依賴：T021。
- [X] T024 [US2] 修改 `tool/update_financials_mops.py`；預期：在寫入前檢查 `unit` 與必填欄位，缺欄或單位未知時直接拒絕寫入；驗證：執行 `rg "unit|ValueError|required|operating_margin" tool/update_financials_mops.py`；依賴：T021。
- [X] T025 [P] [US2] 修改 `tool/db_helper.py`；預期：為 `upsert_financial_statements` 補齊型別提示與 MCP 輸入契約 docstring，明確保留 `(stock_id, year, quarter)` 冪等語意；驗證：執行 `rg "def upsert_financial_statements|operating_margin" tool/db_helper.py`；依賴：T020。
- [X] T026 [US2] 修改 `tool/update_history_financials.py`；預期：歷史回補失敗日誌帶出 year、quarter、retry context，便於重跑；驗證：執行 `rg "retry|quarter|correlation|cooldown" tool/update_history_financials.py`；依賴：T022。

**Checkpoint**: 每日季度更新、單季手動更新、歷史回補三條財報路徑都已共享同一 MCP transport boundary。

---

## Phase 5: User Story 3 - 並行資料擷取不拖慢日常選股 (Priority: P3)

**Goal**: 將 MCP 擷取做成可並行使用的 client，並讓 `tool/news_agent.py` 具備 LangChain `BaseTool` 邊界。

**Independent Test**: 以 compose 啟動服務後，執行 `python 1_update_database.py` 與 `python 2_rundaily.py`，確認市場同步不再完全串行等待，且 `tool/news_agent.py` 仍保有既有公開函式。

- [X] T027 [US3] 修改 `tool/mcp_client.py`；預期：加入 `fetch_many` 或等價並行 gather helper，可同時抓多個 dataset；驗證：執行 `rg "fetch_many|asyncio.gather" tool/mcp_client.py`；依賴：T020。
- [X] T028 [US3] 修改 `1_update_database.py`；預期：市場快照與外資買賣超在進入 merge 前以並行方式擷取，縮短 `2_rundaily.py` 前置等待；驗證：執行 `rg "fetch_many|asyncio" 1_update_database.py`；依賴：T027、T015。
- [X] T029 [P] [US3] 修改 `tool/news_agent.py`；預期：將 MCP 市場/財報方法包裝成 LangChain `BaseTool` 類別或等價 tool 物件；驗證：執行 `rg "BaseTool|tool_name|args_schema" tool/news_agent.py`；依賴：T009、T020。
- [X] T030 [US3] 修改 `tool/news_agent.py`；預期：更新 agent prompt，要求優先透過 MCP-backed tools 取得市場與財務上下文；驗證：執行 `rg "MCP|tool|prompt" tool/news_agent.py`；依賴：T029。
- [X] T031 [US3] 修改 `tool/news_agent.py`；預期：在加入 `BaseTool` 後，`get_morning_news_summary`、`get_news_sector_boost`、`get_stock_news_mentions` 仍保有既有公開介面；驗證：執行 `rg "def get_morning_news_summary|def get_news_sector_boost|def get_stock_news_mentions" tool/news_agent.py`；依賴：T030。
- [X] T032 [US3] 修改 `tool/news_agent.py`、`config.py`；預期：news agent 與 updater scripts 共用同一組 MCP env/config，不出現第二套 endpoint 設定；驗證：執行 `python -c "from config import Config; print(bool(Config.MCP_BASE_URL))"`；依賴：T029、T030。

**Checkpoint**: MCP client 已具備並行抓取能力，且 `tool/news_agent.py` 已有可重用的 LangChain tool 邊界。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 收斂文件、依賴與操作驗證，確保新架構可被維運與後續任務直接接手。

- [X] T033 [P] 修改 `README.md`、`openspec/project.md`；預期：文件反映 `twse_mcp_server`、`stock_bot`、健康檢查與 MCP transport boundary；驗證：執行 `rg "twse_mcp_server|MCPClient|health" README.md openspec/project.md`；依賴：T028、T032。
- [X] T034 修改 `specs/001-modernize-twse-dataflow/quickstart.md`、`tool/mcp_client.py`、`1_update_database.py`、`tool/news_agent.py`；預期：quickstart 與 docstring 都描述實際 MCP flow、compose 指令與 `BaseTool` 使用方式；驗證：執行 `rg "MCP|docker compose|BaseTool" specs/001-modernize-twse-dataflow/quickstart.md tool/mcp_client.py 1_update_database.py tool/news_agent.py`；依賴：T028、T032。
- [X] T035 修改 `docker-compose.yaml`、`requirements.txt`；預期：最終 compose 與依賴宣告消除遺漏或漂移，容器啟動假設與 Python 依賴一致；驗證：執行 `docker compose config` 與 `rg "httpx|langchain|twse_mcp_server" requirements.txt docker-compose.yaml`；依賴：T004、T005、T032。
- [X] T036 修改 `docker-compose.yaml`、`app.py`；預期：三個服務可成功 `up` 並全部轉為 healthy；驗證：執行 `docker compose up --build -d db twse_mcp_server stock_bot` 與 `docker compose ps`；依賴：T035。
- [X] T037 修改 `docker-compose.yaml`、`1_update_database.py`、`tool/mcp_client.py`；預期：容器內執行 `python 1_update_database.py` 能完成 MCP-backed 每日同步；驗證：執行 `docker compose exec stock_bot python 1_update_database.py`；依賴：T036。
- [X] T038 修改 `docker-compose.yaml`、`2_rundaily.py`、`tool/news_agent.py`；預期：容器內執行 `python 2_rundaily.py` 仍可消費更新後的資料表與 news tool 邊界；驗證：執行 `docker compose exec stock_bot python 2_rundaily.py`；依賴：T037。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1: Setup**: 先完成 `docker-compose.yaml` 與 `requirements.txt` 的骨架，否則後續健康檢查與依賴驗證都無法開始。
- **Phase 2: Foundational**: 依賴 Setup 完成；必須先有 `config.py`、`tool/mcp_client.py` 與 `/health`，才能開始任何故事實作。
- **Phase 3: US1**: 依賴 Foundational；先把日常市場同步切到 MCP，才有穩定的 MVP。
- **Phase 4: US2**: 依賴 Foundational；與 US1 邏輯上可獨立，但都共用同一 `MCPClient`。
- **Phase 5: US3**: 依賴 Foundational，且 `1_update_database.py` 的並行化部分依賴 US1 的 MCP 日常同步已落地。
- **Phase 6: Polish**: 依賴所有欲交付的使用者故事完成。

### User Story Dependencies

- **US1 (P1)**: 僅依賴 Setup + Foundational，無需等待其他故事。
- **US2 (P2)**: 僅依賴 Setup + Foundational；財報更新路徑可獨立於 US1 驗證。
- **US3 (P3)**: `tool/news_agent.py` 的 `BaseTool` 包裝可在 Foundational 後開始，但 `1_update_database.py` 的並行抓取優化必須建立在 US1 已完成 MCP 市場同步之上。

### Task-Level Dependency Highlights

- 必須先完成 `docker-compose.yaml` 的服務與健康檢查配置（T001-T004），才能開始做 compose 相關驗證與容器 smoke test（T036-T038）。
- 必須先完成 `tool/mcp_client.py` 的 sync/async 基礎（T007-T010），才能開始把 `1_update_database.py`、`tool/update_financials_mops.py`、`tool/news_agent.py` 切換到 MCP。
- 必須先完成日常市場同步改造（T012-T019），才能安全進行 `1_update_database.py` 的並行抓取優化（T028）。
- 必須先完成 MCP financial method（T020），才能開始單季更新、歷史回補與 `db_helper` 財報契約整理（T021-T026）。
- 必須先完成 Docker 服務配置與 `tool/mcp_client.py` 基礎（T001-T004、T007-T010），才能開始驗證 `tool/mcp_client.py` 在容器內的行為（T036-T038）。

### Parallel Opportunities

- Setup 階段可平行：T005 可與 T001-T004 分工處理，因為只修改 `requirements.txt`。
- Foundational 階段可平行：T006 與 T011 可分開進行；T007-T010 仍需在 `tool/mcp_client.py` 內序列完成。
- US2 可平行：T022 與 T025 在 T020 完成後可由不同人分別處理。
- US3 可平行：T029 在 T009、T020 完成後可與 T027 分頭處理，之後再回到 T030-T032 整合。

---

## Parallel Example: User Story 1

US1 內部沒有標記 `[P]` 的任務，因為主要修改集中在 `1_update_database.py`，需要保持序列化以避免同檔衝突。

---

## Parallel Example: User Story 2

```bash
# After T020 is done, these can run in parallel:
Task: "修改 tool/update_history_financials.py；預期：歷史回補重用 MCP 財報路徑；驗證：rg \"QuarterlyScraper\" tool/update_history_financials.py"
Task: "修改 tool/db_helper.py；預期：upsert_financial_statements 契約與型別提示明確化；驗證：rg \"def upsert_financial_statements|operating_margin\" tool/db_helper.py"
```

---

## Parallel Example: User Story 3

```bash
# After T020 and T009 are done, these can run in parallel:
Task: "修改 tool/mcp_client.py；預期：加入 fetch_many 並行 helper；驗證：rg \"fetch_many|asyncio.gather\" tool/mcp_client.py"
Task: "修改 tool/news_agent.py；預期：加入 LangChain BaseTool wrappers；驗證：rg \"BaseTool|tool_name|args_schema\" tool/news_agent.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. 完成 Phase 1: Setup。
2. 完成 Phase 2: Foundational。
3. 完成 Phase 3: US1，讓日常市場同步先穩定落在 MCP 上。
4. 用 `python 1_update_database.py` 驗證 `daily_market_data` 寫入與 downstream 相容性。

### Incremental Delivery

1. 先交付 US1，消除每日市場同步的 scraper 風險。
2. 再交付 US2，讓財報的單季更新與歷史回補共用 MCP。
3. 最後交付 US3，把並行抓取與 LangChain tool 邊界補上。
4. 以 Phase 6 的 compose smoke test 作為整體完成條件。

### Recommended Execution Order

1. T001-T005
2. T006-T011
3. T012-T019
4. T020-T026
5. T027-T032
6. T033-T038

---

## Notes

- 共 38 個任務。
- US1 任務數：8。
- US2 任務數：7。
- US3 任務數：6。
- 每條任務都已包含修改檔案、預期行為、驗證方式與依賴順序。
- 依 Speckit 規則，使用者故事任務均以 `[US1]`、`[US2]`、`[US3]` 標記，便於後續追蹤與拆派。