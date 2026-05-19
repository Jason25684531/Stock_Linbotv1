# 專案規格：Stock_Linbotv1

> 最後更新：2026-04-30

## 1. 專案目標

Stock_Linbotv1 是一套面向台股的自動化選股與推播系統，結合多策略技術分析、財報與籌碼資料、XGBoost 排名，以及 Web Dashboard / LINE Bot 兩個使用者入口。

目前專案的核心目標是：
- 透過多策略框架維持不同市場環境下的可切換選股能力。
- 以 `jobs/` 為營運入口，將資料更新、每日落庫、推播與回補流程標準化。
- 以 `app/` + `core/` 為 canonical 應用層與業務層，降低 legacy facade 的耦合。
- 讓 Web、LINE、scheduled push 對同一組推薦資料具有一致的 resolved snapshot 語意。

## 2. 核心技術棧

| 層次 | 技術 |
|------|------|
| 語言 | Python 3.10+ |
| Web | Flask + Jinja2 |
| LINE | Line Bot SDK v3 + Flex Message |
| 資料庫 | MySQL 8.0+ + SQLAlchemy |
| 模型 | XGBoost（每策略獨立模型） |
| 視覺化 | Plotly |
| 最佳化 | Optuna |
| Transport | MCP HTTP service (`services/mcp/server.py`) |

## 3. 現行架構總覽

目前實際架構採用 `app/ + jobs/ + core/ + services/` 分層：

```
Application
  app/                 Web / LINE canonical package
  app.py               legacy facade -> app.main()
  jobs/                canonical batch jobs
  4_run_backtest.py + 5_push_to_line.py   remaining legacy wrappers

Domain / Core
  core/db_helper.py            唯一資料庫存取入口
  core/strategy_manager.py     策略註冊 / active / persistence 管理
  core/strategies/*            V31 ~ V38 策略實作
  core/calc_indicators.py      技術與籌碼指標
  core/line_message_builder.py Flex builders
  core/report_helper.py        診斷/報表組裝
  core/news_agent.py           新聞摘要與 AI 分析

Transport / Integration
  core/mcp_client.py           服務端 MCP client facade
  services/mcp/server.py       canonical MCP HTTP service
  scripts/twse_mcp_server.py   legacy launcher

Presentation
  templates/                   dashboard / login / backtest
```

### 3.1 Canonical 入口

- `app/web_server.py`：Web routes 與 API。
- `app/line_bot.py`：LINE webhook 與訊息處理。
- `jobs/update_database.py`：資料更新。
- `jobs/run_daily.py`：每日選股與推薦落庫。
- `jobs/push_to_line.py`：早晚推播。
- `jobs/backfill_pipeline.py`：市場資料與推薦缺口回補。

### 3.2 Legacy facade 狀態

以下檔案仍保留，但只作為相容入口：
- `app.py`
- `4_run_backtest.py`
- `5_push_to_line.py`

新功能或重構應優先落在 `app/`、`jobs/`、`core/`。

## 4. 主要運作流程

### 4.1 市場資料更新

`jobs/update_database.py` 透過 `core/mcp_client.py` 呼叫 `services/mcp/server.py`，抓取：
- 市場快照
- 外資/投信/自營商資料
- 季度財報資料

另外整合：
- `core/update_monthly_revenue.py` 月營收
- `core/crawlers/` 內的籌碼補充來源

更新後資料寫入 `daily_market_data`、`monthly_revenue`、`financial_statements` 等表。

### 4.2 每日選股與推薦落庫

`jobs/run_daily.py` 的流程：
- 讀取最新交易日或指定日期
- 載入近 150 天資料並計算技術 / 籌碼指標
- 合併財報與月營收資料
- 依 `StrategyManager.get_persistence_strategies()` 遍歷所有需落庫策略
- 每個策略寫入：候選股 rows 或 heartbeat row

關鍵規則：
- `daily_recommendations` 以「策略 × 日期」為完整性單位。
- 零候選時會寫入 heartbeat（`stock_id = NONE`）。
- heartbeat 代表該策略當日已完成計算，不代表資料缺漏。

### 4.3 推薦查詢與 fallback contract

所有使用者可見的推薦查詢都應透過 `core.db_helper.get_recommendations_with_market_fallback()`：
- 同日有已落庫推薦：直接使用
- 同日只有 heartbeat：回傳零候選完成狀態
- 同日缺該策略快照：只向該策略自己的歷史回推

這個 contract 目前被以下入口共用：
- Web `/api/daily-signals`
- LINE 手動輸入「推薦」
- `jobs/push_to_line.py` 早晚排程推播

### 4.4 早晚推播

`jobs/push_to_line.py` 目前有兩種模式：

- `morning`
  使用固定三卡 `FlexCarousel`：市場概況 / 新聞摘要 / 精選標的。
  若當日尚未有新資料，會顯示最近交易日 baseline 與退化訊息。

- `evening`
  在資料更新與選股完成後，輸出晚間摘要與明日關注卡片。

### 4.5 Rich Menu 對話流

Rich Menu 目前四個主要入口為：
- 個股診斷：先提示輸入 4 碼股票代號
- 總經摘要：回傳 Flex summary
- 日誌反思：回傳策略與績效快照
- 策略選股：先讓使用者明確選策略，再回應當日推薦

## 5. 策略與設定模型

目前已註冊的策略為：
- `v31_hybrid`
- `v33_low_vol`
- `v34_turbo`
- `v35_innovation`
- `v36_chip_momentum`
- `v37_mean_reversion`
- `v38_value_dividend`

`core/strategy_manager.py` 目前負責：
- active strategies
- persistence strategies
- per-strategy overrides
- backtest defaults

`strategy_settings.json` 是策略啟用與持久化範圍的單一設定來源。

## 6. 資料表與資料契約

核心表如下：

| 資料表 | 用途 |
|--------|------|
| `daily_market_data` | 每日行情、技術指標、籌碼資訊 |
| `monthly_revenue` | 月營收與 YoY |
| `financial_statements` | 季度財報 |
| `daily_recommendations` | 每日推薦落庫與 heartbeat |
| `user_settings` | 使用者參數設定 |
| `user_simulation_trades` | 模擬交易資料 |
| `backtest_trades` | 回測交易明細 |
| `backtest_equity_curve` | 回測權益曲線 |

對 `daily_recommendations` 的約束：
- 使用者可見推薦不得依賴臨時重算作為主要資料來源。
- heartbeat 與候選股 row 同樣屬於完整輸出。
- 缺口檢查需使用「交易日 × 策略」矩陣，而不是只有日期存在與否。

## 7. 架構限制

1. 所有資料庫操作都必須經由 `core/db_helper.py`。
2. 推薦查詢必須優先使用已落庫快照，不得在使用者可見路徑上靜默重算後冒充同日結果。
3. 新策略必須繼承 `BaseStrategy`，並註冊進 `StrategyManager.STRATEGY_REGISTRY`。
4. 對外資料抓取應集中於 `core/mcp_client.py` 與 `services/mcp/server.py`，不要在應用層直接散落 HTTP 呼叫。
5. Web 前端維持無 build-tool 路線，使用 Flask template + CDN。
6. 涉及多模組變更時，應先走 OpenSpec proposal / design / tasks 流程。

## 8. 操作命令

### 8.1 啟動

```powershell
.\myenv\Scripts\Activate.ps1
python services\mcp\server.py
python app.py
```

### 8.2 批次流程

```powershell
python jobs/update_database.py
python jobs/run_daily.py
python jobs/push_to_line.py --time morning
python jobs/push_to_line.py --time evening
python jobs/backfill_pipeline.py --start-date 2026-04-01 --dry-run
```

### 8.3 測試

```powershell
python -m pytest test/ -v --tb=short
python -m pytest test/test_recommendation_fallback.py -q
python -m pytest test/test_run_daily_persistence.py -q
python -m pytest test/test_recommendation_channel_sync.py -q
python -m pytest test/test_backfill_pipeline.py -q
```

## 9. 當前狀態

截至 2026-04-30，專案目前已完成下列穩定化方向：
- canonical `app/` + `jobs/` + `core/` 架構已成形
- Web / LINE / scheduled push 的推薦 resolved snapshot 語意已同步
- 推薦落庫已從 active-only 改為 persistence-set 全覆蓋
- backfill 已改為策略矩陣級別的缺口檢查
- morning push 已統一為三卡 carousel
- Rich Menu 已具備引導式對話與 Flex summary/strategy picker 流程
