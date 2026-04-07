# Feature Specification: Rich Menu 數據驅動與 MCP 深度整合

**Feature Branch**: `002-richmenu-mcp-integration`
**Created**: 2026-04-02
**Status**: Draft
**Input**: User description: "將現有的 Rich Menu 從靜態文字觸發，重構為直接對接 TWSE MCP Server 與內部策略引擎的動態 Postback 系統，涵蓋總經大盤快照、籌碼動向與策略盲盒三大互動入口，並以記憶體快取降低並發壓力。"

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — 大盤快照一鍵即時取得 (Priority: P1)

身為 LINE Bot 使用者（投資人），我希望點選「總經與大盤」按鈕後，能立即收到今日市場統計摘要（包含成交量與漲跌家數），無需輸入任何文字指令。

**Why this priority**: 整個重構的最小可交付價值。大盤概況是每位使用者每日最基礎的資訊需求；若此入口無法正常觸發，後續籌碼與策略入口的價值也無從顯現。

**Independent Test**: 向 Webhook 送出 PostbackEvent (`data="action=market_summary"`)，確認回覆包含今日成交量數字與漲跌家數，且在 1 小時內再次觸發時服務快取結果而不重複呼叫上游。

**Acceptance Scenarios**:

1. **Given** 使用者點選「總經與大盤」按鈕，**When** Webhook 收到 `data="action=market_summary"` 的 PostbackEvent，**Then** Bot 回覆包含今日市場總成交量及漲跌家數的結構化訊息，且格式適合非技術背景使用者閱讀。
2. **Given** 同一交易日內大盤資料已於 1 小時內成功取得，**When** 另一位使用者再次觸發相同 postback，**Then** Bot 直接從快取提供結果，不再呼叫上游資料源。
3. **Given** 上游大盤資料源暫時無法回應，**When** postback 觸發，**Then** Bot 回覆「大盤資料暫時無法取得」等具說明性的使用者友善訊息，不回傳空白回覆或錯誤追蹤資訊。

---

### User Story 2 — 三大法人籌碼動向一鍵掌握 (Priority: P2)

身為 LINE Bot 使用者（投資人），我希望點選「籌碼動向」按鈕，立即收到今日三大法人（外資為必要、投信與自營商為選擇性）的淨買超/賣超摘要，清楚了解當日資金流向。

**Why this priority**: 籌碼資料是短線決策的核心訊號，是獨立可交付的功能切片，與大盤快照和策略盲盒互不依賴。

**Independent Test**: 送出 PostbackEvent (`data="action=chip_trend"`)，確認回覆包含外資淨買超金額，且格式呈現資金流向（買超為正、賣超為負）；快取行為與 P1 相同。

**Acceptance Scenarios**:

1. **Given** 使用者點選「籌碼動向」按鈕，**When** Webhook 收到 `data="action=chip_trend"` 的 PostbackEvent，**Then** Bot 回覆包含外資淨買超/賣超資料的資金流向訊息；若投信與自營商資料可取得，一併顯示。
2. **Given** 上游籌碼資料源不可用，**When** postback 觸發，**Then** Bot 回覆「籌碼資料暫時無法取得」等說明性訊息，不暴露系統內部錯誤。
3. **Given** 籌碼資料於同一交易日內已快取，**When** 再次觸發，**Then** Bot 使用快取結果，不重複呼叫上游。

---

### User Story 3 — 策略盲盒提供驚喜選股推薦 (Priority: P3)

身為 LINE Bot 使用者（投資人），我希望點選「策略盲盒」按鈕，系統隨機挑選一個策略並執行選股，回傳精選標的清單，為我的每日研究帶來多樣性與新意。

**Why this priority**: 差異化使用者體驗功能。依賴 P1/P2 共用的 Postback 路由架構，但業務邏輯完全獨立，可後接交付。

**Independent Test**: 多次送出 PostbackEvent (`data="action=random_strategy"`)；確認在合理樣本內出現不同策略名稱、每次回覆都包含策略標籤與至少一檔標的（或明確的「今日無標的」告知）。

**Acceptance Scenarios**:

1. **Given** 使用者點選「策略盲盒」，**When** Webhook 收到 `data="action=random_strategy"` 的 PostbackEvent，**Then** Bot 從可設定的策略池中隨機選出一個策略，執行其候選股篩選，並回覆帶有策略名稱標籤的標的清單。
2. **Given** 隨機選出的策略當日無符合條件的標的，**When** 組裝回覆時，**Then** Bot 告知使用者所選策略今日無推薦標的，並說明策略名稱，不回傳錯誤訊息。
3. **Given** 策略引擎在執行期間發生例外，**When** postback 觸發，**Then** Bot 回傳使用者友善的錯誤說明，Webhook response 仍正常返回 HTTP 200，不因例外中斷 LINE 回調。

### Edge Cases

- 若 Rich Menu 圖片資產尚未更新為對應四按鈕的版面，Postback Actions 必須在視覺資產更新前仍能正確觸發與路由。
- 自非交易日的跨日快取（例如週五資料於週一仍命中）必須被識別為過期；快取鍵必須含交易日期以避免跨日資料污染。
- 若 `market_summary` 或 `chip_trend` 上游回傳當日空資料集（非錯誤），不得將空結果寫入快取，以利稍後重試取得正確資料。
- LINE 的 Postback 至少一次遞送保證可能造成重複觸發；在同一快取窗口內的重複 postback 必須產生冪等回覆，不得重複執行策略引擎。
- 若策略池設定為空，`random_strategy` 必須回覆「目前無已設定的策略」說明訊息，而非丟出未處理例外。
- `market_summary` 與 `chip_trend` 的快取失效（TTL 到期或跨日）必須相互獨立，不得因一個快取失效而清除另一個的快取。

---

## Integration & Operational Constraints *(mandatory)*

- Postback 事件的路由分派必須採用映射表（dispatch map）結構，新的三個 action 鍵（`market_summary`、`chip_trend`、`random_strategy`）必須能不修改路由核心邏輯地獨立擴充。
- 記憶體 TTL 快取（1 小時）僅適用於上游市場資料呼叫（`market_summary`、`chip_trend`）；`random_strategy` 不套用快取，每次觸發均直接執行以反映最新市場狀態。
- 快取實作不得引入模組外部可存取的共享可變狀態；在單一 Python 程序的場景下需具備基本的執行緒安全性。
- `random_strategy` 的策略池必須從現有策略設定（`strategy_settings.json`）動態讀取，新增或移除可用策略不得要求修改應用程式碼。
- 現有 `action=get_macro_news` 與 `action=get_journal` 的 Postback 處理邏輯必須保持完全不變；此功能的重構範圍不得影響這兩個現有入口。
- 所有 Rich Menu 版面定義的修改必須通過現有 `sync_default_rich_menu_from_token()` 部署路徑可自動套用，不需人工操作 LINE Developer Console。
- 上游資料呼叫必須有明確的逾時設定；若呼叫超時，Bot 必須回覆友善說明訊息並讓 Webhook response 在 LINE 要求的時間內正常返回。

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 2×2 Rich Menu 必須重新定義為以下行為映射：左上（個股診斷）維持 MessageAction，預設觸發文字為「診斷 」；右上（總經與大盤）使用 PostbackAction，`data="action=market_summary"`；左下（籌碼動向）使用 PostbackAction，`data="action=chip_trend"`；右下（策略盲盒）使用 PostbackAction，`data="action=random_strategy"`。

- **FR-002**: Webhook PostbackEvent 路由器必須在現有 `get_macro_news` 與 `get_journal` 之外，額外識別並分派 `market_summary`、`chip_trend`、`random_strategy` 三個新 action 鍵到各自的獨立處理函式。

- **FR-003**: `market_summary` 處理函式必須組裝並回覆包含以下資訊的單一結構化 LINE 訊息：今日市場總成交量（以易讀單位呈現）、上漲家數與下跌家數；資料來源為上游大盤統計。

- **FR-004**: `chip_trend` 處理函式必須組裝並回覆資金流向摘要，至少包含外資當日淨買超／賣超金額；若上游同時提供投信與自營商資料，必須一併呈現。

- **FR-005**: `random_strategy` 處理函式必須從可設定的策略鍵清單（預設包含 v35、v36、v38）中隨機選取一個，執行其候選股篩選邏輯，並以帶有策略名稱標籤的格式回覆精選標的；若第一個隨機策略當日無標的，必須嘗試其他可用策略後才能宣告「今日無標的」。

- **FR-006**: 一個共用記憶體 TTL 快取必須被套用於 `market_summary` 與 `chip_trend` 的上游資料呼叫，TTL 為 1 小時；快取鍵必須包含 action 名稱與當日交易日期，以確保跨日資料不會被錯誤命中。

- **FR-007**: 所有三個新 postback 處理函式在上游資料不可用或回傳空集合時，必須回覆說明性的使用者友善訊息；原始例外訊息或 traceback 不得出現在 LINE 回覆內容中。

- **FR-008**: 友善錯誤訊息必須識別失敗的功能名稱（例如「大盤資料暫時無法取得，請稍後再試」），不得使用通用的「系統錯誤」訊息。

- **FR-009**: TTL 快取實作必須在同一 Python 程序範圍內確保讀寫的基本執行緒安全性，避免快取條目在並發請求下被不完整狀態覆寫。

- **FR-010**: Rich Menu 版面定義的更新必須通過現有部署機制（環境變數觸發的 `LINE_RICH_MENU_AUTO_SYNC` 流程）可自動套用，不需額外手動步驟。

- **FR-011**: `random_strategy` 的策略池清單必須從現有策略設定來源動態讀取，不得在此功能的程式碼中硬編碼策略鍵清單；基礎的 v35、v36、v38 為預設啟用策略。

### Key Entities *(include if feature involves data)*

- **Rich Menu Area Definition**: 定義 2×2 版面中單一區域的行為。關鍵屬性：座標邊界（x、y、width、height）、動作類型（MessageAction 或 PostbackAction）、顯示標籤、觸發文字或 postback data 字串。

- **Market Summary Payload**: 表示今日整體市場活動的聚合快照。關鍵屬性：交易日期、市場總成交量、上漲家數、下跌家數、資料取得時戳。

- **Chip Trend Payload**: 表示今日三大法人資金流向摘要。關鍵屬性：交易日期、外資淨買超金額、投信淨買超金額（選擇性）、自營商淨買超金額（選擇性）、資料取得時戳。

- **Postback Cache Entry**: 記憶體快取中的一筆上游市場資料快取紀錄。關鍵屬性：快取鍵（action 名稱 + 交易日期）、資料 Payload、取得時戳、TTL 到期時戳。

- **Strategy Blind Box Result**: 隨機策略執行結果。關鍵屬性：所用策略鍵、找到的標的數量、格式化標的清單、執行時戳。

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 4 個 Rich Menu 按鈕在手動及自動化測試中，100% 觸發與 FR-001 定義相符的正確 Action 類型，不落入「不支援的指令」預設回覆。

- **SC-002**: `market_summary` 與 `chip_trend` 在無快取（冷啟動）情況下，95% 的請求於 5 秒內傳遞使用者可讀的回覆訊息；在快取命中（暖快取）情況下，95% 的請求於 1 秒內完成回覆。

- **SC-003**: 同一交易日同一 action 在 1 小時快取窗口內重複觸發，上游資料源只被呼叫 1 次，可由測試 mock 或 log 驗證。

- **SC-004**: `random_strategy` 在至少 5 次連續觸發中出現超過 1 種不同策略名稱（隨機性可驗證性要求）；每次回覆均含策略識別標籤。

- **SC-005**: 三個新 postback action 在上游資料被模擬為不可用時，100% 的測試案例均回傳使用者可讀訊息（不含 traceback），且 Webhook response HTTP 狀態碼為 200。

- **SC-006**: 現有 `get_macro_news` 與 `get_journal` postback handler 的既有測試在此功能交付後無任何失敗。

---

## Assumptions

- `tool/mcp_client.py` 是本功能所有外部資料呼叫的唯一邊界；本規格不規定大盤統計或籌碼資料的具體 MCP 端點名稱，僅要求該邊界能提供所需資料欄位（成交量、漲跌家數、三大法人淨買超）。
- Rich Menu 圖片資產（`Richmenu/Richmenu.png`）已或將獨立更新以對應四按鈕版面；本規格不涵蓋圖片設計變更。
- 策略池預設為 `strategy_settings.json` 中標記為可用的策略清單；若設定中可用策略少於一個，`random_strategy` 回覆「目前無已設定的策略」訊息。
- LINE 的 Postback 至少一次遞送保證：重複遞送在同一快取窗口內為冪等，因此快取設計同時具備去重效果。
- 本功能在單一 Python 程序假設下運作；快取不需跨程序或跨機器共享，不需引入 Redis 等分散式快取。
- 交易日的日期邊界以台灣時區（Asia/Taipei）的日曆日期為準，用於快取鍵計算與跨日失效判斷。
- `scripts/setup_rich_menu.py` 為獨立的佈署輔助腳本（若尚未存在則新增），其職責僅為組裝並呼叫 `tool/richmenu.py` 的版面定義與上傳流程；核心定義邏輯不重複於兩處。
