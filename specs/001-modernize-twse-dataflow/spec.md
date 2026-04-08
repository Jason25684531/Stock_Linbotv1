# Feature Specification: TWSE 數據流現代化

**Feature Branch**: `001-modernize-twse-dataflow`  
**Created**: 2026-04-01  
**Status**: Draft  
**Input**: User description: "定義「TWSE 數據流現代化」規格：將脆弱的 BeautifulSoup 網頁爬蟲替換為穩定的 MCP API，涵蓋個股基本資料、外資買賣超、歷史財報三大模組，並定義 JSON 到既有 schema 的精確映射，同時要求 mcp_client.py 支援非同步呼叫以優化每日選股流程。"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - 穩定日常市場同步 (Priority: P1)

作為維運人員，我需要每日的個股基本資料與外資買賣超改由穩定的 MCP 資料契約提供，讓市場網站版面或 HTML 結構調整時，不會中斷資料更新與後續選股。

**Why this priority**: 這是整個現代化工作的最小可交付價值。若每日行情與外資流向仍依賴脆弱抓取，後續財報與性能優化都無法真正降低營運風險。

**Independent Test**: 以單一交易日執行同步，驗證個股基本資料與外資買賣超可由 MCP 載入、正規化並寫入既有儲存欄位，且下游讀取不需修改查詢契約。

**Acceptance Scenarios**:

1. **Given** MCP 可提供指定交易日的個股基本資料與外資買賣超，**When** 每日同步流程執行，**Then** 系統會將欄位精確映射到既有市場資料表，且未映射欄位不會造成流程失敗。
2. **Given** MCP 回傳欄位順序改變或夾帶額外欄位，**When** 同步流程執行，**Then** 系統仍只寫入已定義欄位並記錄未使用欄位，不會因格式噪音而中斷。

---

### User Story 2 - 歷史財報可安全回補 (Priority: P2)

作為策略維護者，我需要歷史財報回補改走 MCP，而不是依賴 HTML 表格解析，才能在回測、財務篩選與特徵工程中持續取得一致且可追溯的季度資料。

**Why this priority**: 財報是 V34/V35 以及未來基本面策略的核心依賴。若回補路徑不穩，模型訓練、回測與日常篩選都會出現資料缺口或人工修補成本。

**Independent Test**: 以一個或多個已公告季度執行歷史回補，驗證收入、營業費用、營業利益、EPS 與衍生營業利益率能落入既有財報欄位，且既有財務查詢與策略合併流程維持可用。

**Acceptance Scenarios**:

1. **Given** MCP 提供某一季度的歷史財報資料，**When** 系統執行季度回補，**Then** 相同季度的舊資料會被安全覆寫為新資料，且下游仍可從既有財報表讀取一致欄位。
2. **Given** 某一家公司在該季度缺少選填欄位，**When** 系統正規化該批資料，**Then** 必填欄位仍可寫入，缺失欄位會使用既定預設值或衍生規則處理，並留下可追蹤日誌。

---

### User Story 3 - 並行資料擷取不拖慢日常選股 (Priority: P3)

作為排程與效能管理者，我需要新的 MCP 客戶端能以並行方式擷取三大模組資料，讓每日資料準備不再被串行網路等待拖慢，並保持失敗可診斷。

**Why this priority**: 這項工作不會改變選股邏輯本身，但會直接影響每日任務窗口、排程穩定性與問題排查效率，是現代化後的主要運營收益。

**Independent Test**: 以受控測試資料執行多模組同步，驗證在一個模組慢回應或失敗時，其餘模組仍可完成，且整體處理時間明顯優於串行抓取。

**Acceptance Scenarios**:

1. **Given** 三個模組都可由 MCP 提供資料，**When** 同步流程以並行方式執行，**Then** 完成時間應落在每日選股窗口內，且日誌能顯示各模組耗時與結果。
2. **Given** 其中一個模組暫時失敗，**When** 並行同步流程執行，**Then** 其他模組仍應完成並保留成功資料，而失敗模組需留下可重試的診斷資訊。

### Edge Cases

- 若 MCP 在交易日回傳空集合，系統必須區分「休市無資料」與「上游異常」兩種情況，避免將錯誤誤判為正常空資料。
- 若個股基本資料存在，但外資買賣超缺少部分股票，系統不得刪除已存在的其他市場資料列，且需對缺口留下模組級日誌。
- 若歷史財報回補遇到同一季度重複資料，系統必須維持冪等寫入結果，不能產生重複季度紀錄。
- 若 MCP 財報數值單位與既有儲存單位不同，正規化流程必須先完成單位轉換，否則該批資料不得寫入。
- 若 MCP 僅提供外資欄位而未提供投信或自營商欄位，系統必須使用已定義的預設策略處理，而不是讓下游欄位遺失。
- 若三個模組之一超時，系統必須將該模組標記為失敗並保留其他模組成功結果，不得因單點失敗回滾整批成功資料。

## Integration & Operational Constraints *(mandatory)*

- 受此功能影響的外部資料源統一由 `tool/mcp_client.py` 擁有，至少包含三個 MCP 契約：`stock_basic_snapshot`、`foreign_investor_flow`、`historical_financial_statements`。
- 每個 MCP 呼叫都必須具備明確的逾時、有限次數重試、退避與系統日誌。模組級日誌至少需包含模組名稱、目標日期或季度、耗時、重試次數與最終狀態。
- 此功能的正常路徑不再回退到 BeautifulSoup 或 HTML 表格解析；當 MCP 失敗時，系統應保留既有有效資料並以可重試的失敗結果結束該模組。
- 若為了並行 I/O 引入新套件，必須在同一變更中更新 `requirements.txt`，並說明為何現有 `requests` 路徑不足以支援並行與統一重試治理；`httpx` 為預期優先選項。
- 功能交付時需同步更新至少以下文件或同等文檔：`README.md`、`openspec/project.md`、受影響模組 docstrings，以及描述遷移與操作差異的變更文件。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系統必須以 MCP API 取代本功能範圍內對 BeautifulSoup、`pd.read_html(...)` 或等價 HTML 解析的正式資料來源依賴，涵蓋個股基本資料、外資買賣超與歷史財報三個模組。
- **FR-002**: 所有新建或重構後的外部 HTTP 整合都必須集中在 `tool/mcp_client.py`，核心業務腳本不得直接發送這三個模組的 HTTP 請求。
- **FR-003**: 系統必須接受 `stock_basic_snapshot` MCP 回傳的標準 JSON，並將 `stock_id`、`trade_date`、`open_price`、`high_price`、`low_price`、`close_price`、`volume`、`pe_ratio` 映射到既有 `daily_market_data` 儲存契約。
- **FR-004**: 系統必須接受 `foreign_investor_flow` MCP 回傳的標準 JSON，並將 `stock_id`、`trade_date`、`foreign_buy` 映射到既有 `daily_market_data` 儲存契約；若同批資料包含 `trust_buy` 或 `dealer_buy`，也必須以相同主鍵規則寫入相容欄位。
- **FR-005**: 系統必須接受 `historical_financial_statements` MCP 回傳的標準 JSON，並將 `stock_id`、`year`、`quarter`、`revenue`、`rd_expense`、`operating_expense`、`operating_profit`、`eps` 寫入既有 `financial_statements` 儲存契約，且 `operating_margin` 必須依既有規則衍生並持久化。
- **FR-006**: JSON 欄位對映必須明確定義哪些欄位為必填、哪些欄位可選、哪些欄位僅供驗證不落地，且對未落地欄位的處理方式必須被記錄。
- **FR-007**: 系統必須維持與既有儲存結構相同的欄位意義與單位，其中 `revenue`、`operating_expense`、`operating_profit` 需以現行資料庫預期單位儲存，`operating_margin` 需以百分比儲存，`eps` 需維持每股盈餘語義。
- **FR-008**: 同一交易日或季度的重複同步必須是冪等的；重新執行時只能覆寫相同主鍵資料，不得新增重複列或破壞其他日期資料。
- **FR-009**: 若某模組 MCP 回傳缺少必填欄位或單位資訊，系統必須拒絕該模組寫入並回報具體缺欄原因，不得靜默以不可信值補齊。
- **FR-010**: 對可恢復的 MCP 失敗，系統必須使用顯式例外處理、有限次數重試與系統日誌；嚴禁使用 bare `except:` 吞沒失敗原因。
- **FR-011**: `tool/mcp_client.py` 必須提供可並行擷取多個 MCP 模組的能力，使每日資料準備不需完全串行等待網路回應。
- **FR-012**: 新增或修改的函式必須提供完整 PEP 484 type hints，覆蓋參數、回傳值與跨模組傳遞的結構化資料。
- **FR-013**: 若此功能引入任何新依賴，系統必須在同一變更更新 `requirements.txt` 並說明依賴理由；同類型 HTTP 客戶端不得無理由並存多套。
- **FR-014**: 在此功能交付後，現有讀取 `daily_market_data` 與 `financial_statements` 的下游流程，包括每日選股、財務補值與回測，不得因資料欄位契約改變而必須重寫查詢。
- **FR-015**: 當單一模組同步失敗時，系統必須保留其他模組的成功寫入結果與既有有效歷史資料，並將整體執行狀態標記為部分成功或可重試失敗。
- **FR-016**: 任何行為、操作方式或資料契約的變更都必須同步更新對應 Markdown 文件、OpenSpec 產物或 docstrings，否則此功能不得視為完成。

### Key Entities *(include if feature involves data)*

- **Stock Basic Snapshot Payload**: 表示某一交易日的個股日線與基本市場欄位集合，關鍵屬性為 `as_of_date`、`market`、`records[]`，每筆記錄至少包含股票代號、交易日、開高低收、成交量與本益比。
- **Foreign Investor Flow Payload**: 表示某一交易日外資買賣超資料集合，關鍵屬性為 `as_of_date`、`market`、`records[]`，每筆記錄至少包含股票代號、交易日與外資買賣超，並可選擇攜帶投信與自營商欄位以維持既有相容性。
- **Historical Financial Statement Payload**: 表示某一季度財報資料集合，關鍵屬性為 `period`、`unit`、`records[]`，每筆記錄至少包含股票代號、營收、營業費用、營業利益與 EPS，供系統衍生營業利益率後寫入。
- **Sync Batch Result**: 表示一次現代化同步工作的執行結果，關鍵屬性為模組名稱、目標日期或季度、成功/失敗狀態、重試次數、耗時與錯誤摘要，用於營運監控與重跑判斷。

### Canonical Payload Contracts & Schema Mapping

#### 1. Stock Basic Snapshot

```json
{
  "dataset": "stock_basic_snapshot",
  "as_of_date": "2026-04-01",
  "market": "TWSE",
  "records": [
    {
      "stock_id": "2330",
      "trade_date": "2026-04-01",
      "open_price": 946.0,
      "high_price": 955.0,
      "low_price": 940.0,
      "close_price": 950.0,
      "volume": 24567890,
      "pe_ratio": 22.4,
      "stock_name": "台積電",
      "security_type": "COMMON_STOCK"
    }
  ]
}
```

| MCP field | Required | Target schema | Handling rule |
|-----------|----------|---------------|---------------|
| `records[].stock_id` | Yes | `daily_market_data.stock_id` | Preserve as string primary identifier |
| `records[].trade_date` or top-level `as_of_date` | Yes | `daily_market_data.trade_date` | Record value wins; top-level date acts as batch default |
| `records[].open_price` | Yes | `daily_market_data.open_price` | Numeric normalization required |
| `records[].high_price` | Yes | `daily_market_data.high_price` | Numeric normalization required |
| `records[].low_price` | Yes | `daily_market_data.low_price` | Numeric normalization required |
| `records[].close_price` | Yes | `daily_market_data.close_price` | Rows with non-positive close must be rejected |
| `records[].volume` | Yes | `daily_market_data.volume` | Store as numeric trading volume |
| `records[].pe_ratio` | No | `daily_market_data.pe_ratio` | Default to `0` when upstream omits value |
| `records[].stock_name` | No | None | Validation/logging only; not persisted in current schema |
| `records[].market` or top-level `market` | No | None | Validation/filtering only; not persisted in current schema |
| `records[].security_type` | No | None | Used for security filtering only; not persisted in current schema |

#### 2. Foreign Investor Flow

```json
{
  "dataset": "foreign_investor_flow",
  "as_of_date": "2026-04-01",
  "market": "TWSE",
  "records": [
    {
      "stock_id": "2330",
      "trade_date": "2026-04-01",
      "foreign_buy": 12345,
      "trust_buy": -500,
      "dealer_buy": 210
    }
  ]
}
```

| MCP field | Required | Target schema | Handling rule |
|-----------|----------|---------------|---------------|
| `records[].stock_id` | Yes | `daily_market_data.stock_id` | Must match an ingestible security identifier |
| `records[].trade_date` or top-level `as_of_date` | Yes | `daily_market_data.trade_date` | Same date resolution rule as stock snapshot |
| `records[].foreign_buy` | Yes | `daily_market_data.foreign_buy` | Signed numeric net-buy value |
| `records[].trust_buy` | No | `daily_market_data.trust_buy` | Persist when supplied; otherwise use documented neutral default |
| `records[].dealer_buy` | No | `daily_market_data.dealer_buy` | Persist when supplied; otherwise use documented neutral default |

#### 3. Historical Financial Statements

```json
{
  "dataset": "historical_financial_statements",
  "period": {
    "year": 2025,
    "quarter": 4
  },
  "unit": "thousand_TWD",
  "records": [
    {
      "stock_id": "2330",
      "revenue": 868461000,
      "rd_expense": 0,
      "operating_expense": 123450000,
      "operating_profit": 301230000,
      "eps": 12.35
    }
  ]
}
```

| MCP field | Required | Target schema | Handling rule |
|-----------|----------|---------------|---------------|
| `period.year` | Yes | `financial_statements.year` | Store as Gregorian year |
| `period.quarter` | Yes | `financial_statements.quarter` | Store values `1-4` only |
| `records[].stock_id` | Yes | `financial_statements.stock_id` | Normalize to 4-digit stock code string |
| `records[].revenue` | Yes | `financial_statements.revenue` | Convert to current stored unit before insert |
| `records[].rd_expense` | No | `financial_statements.rd_expense` | Default to `0` when unavailable |
| `records[].operating_expense` | Yes | `financial_statements.operating_expense` | Convert to current stored unit before insert |
| `records[].operating_profit` | Yes | `financial_statements.operating_profit` | Convert to current stored unit before insert |
| `records[].eps` | No | `financial_statements.eps` | Default to `0.0` when unavailable |
| Derived from `operating_profit / revenue * 100` | Yes | `financial_statements.operating_margin` | Persist derived percentage even if upstream also sends its own margin field |
| `unit` | Yes | None | Must be validated before numeric conversion; not persisted |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 在正常交易日條件下，個股基本資料與外資買賣超同步作業於 95% 執行中可在 120 秒內完成，且寫入後可被既有市場資料查詢直接讀取。
- **SC-002**: 在正常上游條件下，單一季度歷史財報回補於 95% 執行中可在 60 秒內完成，且同季度重跑不會產生重複紀錄。
- **SC-003**: 在驗收測試中，三大模組的必填 MCP 欄位有 100% 被映射到既有儲存欄位或被明確標示為不落地欄位，無任何靜默遺失欄位。
- **SC-004**: 在模擬單模組失敗的驗收案例中，100% 的執行都能保留其他模組成功資料與既有有效歷史資料，並提供可定位到模組與日期/季度的失敗訊息。
- **SC-005**: 每日資料準備完成後，現有依賴 `daily_market_data` 與 `financial_statements` 的主要下游流程可在不修改查詢契約的前提下完成既有驗收測試。

## Assumptions

- 本功能的驗收目標是維持 `tool/db_helper.py` 所管理的既有儲存契約與欄位語義，不因部署環境底層使用 SQLite 或 MySQL 相容儲存而改變對映規則。
- 「個股基本資料」在本功能中指的是目前每日同步與選股實際依賴的個股日線市場欄位，不包含建立新的公司主檔資料表。
- 月營收、融資融券與新聞資料不在此次現代化範圍內，除非它們是完成三大模組映射所不可避免的共用依賴。
- 若 MCP 同時提供 TWSE 與 TPEx 符合同一契約的資料，系統可沿用同一映射規則；若 MCP 僅先覆蓋 TWSE，此功能仍以 TWSE 範圍驗收為主。
- `2_rundaily.py` 的直接業務邏輯不必在本功能中改寫為直接發送 HTTP 請求；此功能要求的是提供可被日常排程採用的並行 MCP 客戶端能力與相容資料輸出。
