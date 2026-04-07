# Rich Menu / LineBot / Web 清理與驗證報告

建立日期：2026-04-07

## 1. 本次目標

依照目前已完成的 Rich Menu MCP 整合內容，進行以下工作：

- 清理重複定義的函式、重複或過時的測試檔與結構
- 保留既有功能行為，避免因清理造成 Rich Menu、Line Bot、Web Dashboard 回歸
- 補齊 README，讓專案架構、啟動、停止與測試方式與現況一致
- 在專案虛擬環境 `myenv` 中完成驗證

## 2. 實際修改內容

### 2.1 重複函式與路由清理

檔案：`app.py`

- 清理 Postback action dispatch 的重複定義來源
- 保留 `_register_postback_handlers()` 作為唯一 action 對 handler 的定義入口
- `_build_postback_reply_messages()` 改為直接從 `_register_postback_handlers().get(action)` 取得 handler
- 移除重複的 module-level dispatch cache，避免未來新增 action 時出現兩份定義不同步

### 2.2 重複/過時測試檔清理

刪除檔案：`test/test_richmenu.py`

原因：

- 該檔案仍驗證舊版 Rich Menu action，如 `action=get_macro_news`、`action=get_journal`
- 內容與新版主測試檔 `test/test_richmenu_mcp_integration.py` 有重疊
- 若保留，容易形成雙重維護與舊行為誤導

整併到主測試檔：`test/test_richmenu_mcp_integration.py`

- 新增 `TestRichMenuSync`
- 新增 `test_extract_action_with_extra_query_params`
- 新增 `test_postback_handler_routes_reply_message`
- 保留並驗證新版 Rich Menu / MCP / Postback / Strategy blind box 主流程

### 2.3 README 與架構文件更新

檔案：`README.md`

更新內容：

- 補上 Rich Menu 2×2 功能說明
- 補上 Rich Menu 目前架構與 4 個按鈕對應行為
- 補上 `scripts/setup_rich_menu.py` 與 `scripts/twse_mcp_server.py` 的角色
- 補上 Web + Line Bot 啟動方式
- 補上關閉方式與測試方式
- 補上 `/health` 驗證與 Rich Menu / MCP 驗證流程
- 更新專案目錄結構，讓目前 MCP / Rich Menu / tests 結構與文件一致

### 2.4 Ignore 規則整理

檔案：`.gitignore`

處理內容：

- 移除重複規則
- 移除過度忽略規則，例如對 `.github`、`specs`、`openspec`、`*.png` 的不合理忽略
- 保留 Python / env / build / cache / editor 類必要規則

### 2.5 全鏈路驗證中追加修正

檔案：`app.py`

- 修正 `_build_market_summary_messages()` 使用舊欄位 `Change`、`TradeVolume` 的問題
- 改為讀取 MCP 真實契約欄位 `open_price`、`close_price`、`volume`
- 修正後，大盤快照可正確計算漲跌家數與總成交量

檔案：`scripts/twse_mcp_server.py`

- 修正 snapshot / flow 正規化時，數值欄位未先移除逗號就 `to_numeric()` 的問題
- 原本會使像 `80,449,813` 這類數字被轉成 `NaN -> 0`
- 修正後，成交量與法人買賣超數值可正確保留

檔案：`test/test_richmenu_mcp_integration.py`

- 將 market summary / chip trend 測試資料改為符合 MCP 真實 payload 欄位
- 避免測試只對舊格式通過，卻無法反映真實 runtime

## 3. 驗證結果

### 3.1 Python 環境

- 使用專案虛擬環境：`myenv`
- Python 版本：3.11.9

### 3.2 靜態檢查

已確認以下檔案沒有新的語法或編輯錯誤：

- `app.py`
- `test/test_richmenu_mcp_integration.py`
- `README.md`
- `.gitignore`

### 3.3 Rich Menu / Line Bot 定向測試

執行：

`pytest test/test_richmenu_mcp_integration.py -v`

結果：

- `43 passed`

涵蓋：

- `_PostbackCache`
- `market_summary`
- `chip_trend`
- `random_strategy`
- Postback router
- StrategyManager blind-box pool
- Rich Menu layout
- Postback handler reply flow

### 3.4 全專案回歸測試

執行：

`pytest test/ -v --tb=short`

結果：

- `172 passed, 3 warnings in 362.75s`

代表本次清理未造成既有功能回歸失敗。

### 3.5 Web smoke test

以 Flask test client 驗證：

- `/health` → HTTP 200，且 JSON 正常
- `/login` → HTTP 200，且頁面含登入表單

結論：

- Web 基本入口正常
- Flask app 可成功載入目前策略管理器與主要路由

### 3.6 本機 + Docker + ngrok 全鏈路驗證

#### Docker / 對外服務狀態

- `db`：healthy
- `twse_mcp_server`：healthy
- `stock_bot`：healthy

#### Rich Menu 同步腳本實測

執行：

`python scripts/setup_rich_menu.py`

結果：

- 成功建立並設定預設 Rich Menu
- LINE API 回傳 rich menu id：`richmenu-72809b226ed0a40a6f6507e5198fa39b`

#### Web 本機 / 對外 API 驗證

本機 `http://localhost:1688` 驗證成功：

- `/health` → 200
- `/api/summary` → 200
- `/api/performance` → 200
- `/api/daily-signals?strategy=v38_value_dividend&top_n=5` → 200
- `/login` → 200

ngrok 公網 `https://643c-59-124-112-186.ngrok-free.app` 驗證成功：

- `/health` → 200
- `/api/summary` → 200
- `/api/performance` → 200

#### LINE webhook 實測

以合法 `X-Line-Signature` 模擬 PostbackEvent，測試：

- 本機 `/callback`：
	- `action=market_summary` → 200 OK
	- `action=chip_trend` → 200 OK
- ngrok 公網 `/callback`：
	- `action=market_summary` → 200 OK

容器日誌確認：

- `stock_bot` 實際收到 `/callback` 請求
- `twse_mcp_server` 實際收到 `/v1/stock-basic-snapshot` 與 `/v1/foreign-investor-flow` 請求
- `stock_bot` 實際收到 `/api/daily-signals` 請求並回 200

#### Callback 內容驗證

以 Flask test client 攔截 reply payload，確認：

- `market_summary` 回覆已能產生非 0 成交量與正常漲跌家數
- `chip_trend` 回覆已能產生外資 / 投信 / 自營商加總結果
- `random_strategy` 回覆可正常走到策略盲盒邏輯

## 4. 限制與說明

本次已完成：

- Rich Menu / Postback / MCP / Strategy blind box 的本地與自動化驗證
- Web 基本呈現與健康檢查驗證

本次未直接執行：

- 真實 LINE 官方帳號由手機端實際點擊 Rich Menu 按鈕的人工驗證

原因：

- 這需要真人從 LINE 客戶端發出真實 replyToken，才能完整驗證「平台實際送達使用者訊息」

已完成的替代驗證：

- 已實際部署 Rich Menu 到 LINE
- 已實際通過本機與 ngrok 公網 callback 入口
- 已以合法簽章模擬 LINE webhook event
- 已驗證 callback 會進入 MCP 與策略邏輯

因此目前可確認的是：

- 本地程式邏輯、Docker 對外服務、Webhook 路由、MCP 整合均正常
- 真正唯一未由代理直接代替的是「真人 LINE 客戶端按按鈕」這一步

## 5. 本次涉及檔案

修改：

- `app.py`
- `scripts/twse_mcp_server.py`
- `test/test_richmenu_mcp_integration.py`
- `README.md`
- `.gitignore`
- `specs/002-richmenu-mcp-integration/tasks.md`

新增：

- `doc/RICHMENU_CLEANUP_REPORT_2026-04-07.md`

刪除：

- `test/test_richmenu.py`

## 6. 結論

本次已完成 Rich Menu / Line Bot / Web 相關的重複結構清理、過時測試移除、README 現況同步與自動化驗證。

在不回退既有使用者變更的前提下，已確認：

- Rich Menu Postback 路由來源已統一
- 過時測試檔已移除且覆蓋率保留
- README 與目前架構一致
- Rich Menu / Line Bot / Web 的本地功能驗證通過
- 全專案測試通過