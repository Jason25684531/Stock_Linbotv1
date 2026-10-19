# 2026-10-19 將 Strategy_example 策略併入策略中心

## Why

`Strategy_example/` 有 41 份 TQuant Lab 範例 notebook（技術面、基本面、籌碼、期貨、ETF、機器學習）。使用者希望這些策略與現有策略一起放在策略中心，依 技術／籌碼／基本面／品質 分類，並具備與現有策略相同的預覽、Daily 與回測能力。

這些 notebook 不能直接接入，原因有三：

1. **執行環境不同**：notebook 依賴 `zipline`（TQuant Lab bundle `tquant` / `tquant_future`）、`TejToolAPI`、`tejapi`（付費 API）及 `pyfolio`。本 repo 的 requirements 與程式碼都沒有用到這些套件。
2. **回測模型不同**：notebook 以 `order_target_percent` 定期再平衡。本專案的 `BacktestEngine`（`core/backtest/runner.py:408`）採每日選股：BULL 時才進場，最多 3 檔、每檔 20%，並以移動停損出場。期貨與放空不支援。
3. **資料不同**：本地有 `daily_market_data`（OHLCV、技術指標、三大法人、融資、PER）、`monthly_revenue`（營收 YoY）、`financial_statements`（revenue、operating_profit、eps 等少數欄位）。TEJ 的現金流量、資產負債表、市值等欄位多數沒有對應。

## What Changes

- 把可移植 notebook 的**選股條件**改寫成 `BaseStrategy` 子類別，放在 `core/strategies/`，與現有策略相同，一個 notebook 對應一個檔案。不引入 zipline、TEJ、pyfolio。
- 登錄進既有的 `CANONICAL_REGISTRY` 與 `STRATEGY_METADATA`，成為一般 canonical 策略。它們沒有 V 開頭的 alias，依內容歸入既有分類：技術（`Technical`）、籌碼（`Institutional`）、基本面（`Fundamental`）、品質（`Quality / *`）。
- 能力與現有策略相同：策略中心預覽、Daily 套用、`/backtest`、`/api/strategies`、CLI `all`。每日落庫預設涵蓋所有已登錄策略，因此預覽快照會自動產生。
- `StrategyMetadata` 新增 `source` 欄位（`native` / `tquant`）。策略卡片顯示來源 notebook，並註明「持股數、再平衡與出場規則依本專案引擎，績效與原 notebook 不同」。
- **LINE 策略選單**只列出 `source=native` 的原 7 支策略，因為 Flex 選單最多 13 項（`core/line_message_builder.py:1357`）。新策略在 Web 使用；LINE 的文字切換指令以 canonical id 仍可使用。
- 無法移植或缺少必要欄位的 notebook，記錄在 `design.md` 對照表並註明原因，不以代用欄位假裝移植。

## Capabilities

### Modified Capabilities

- `strategy-registry`：公開清單從固定 7 支改為「所有已登錄的 canonical 策略」；LINE 選單限定 native 策略。
- `strategy-center-ui`：目錄包含新策略，依既有四個分類篩選。

## Impact

- **新增**：`core/strategies/<name>.py`（預估 Phase 2 有 14 檔、Phase 3 有 4 檔；Phase 4 有 12 檔待資料稽核）；`test/test_tquant_strategies.py`。
- **修改**：`core/strategy_manager.py`（登錄與 `source` 欄位）；`app/web_server.py`（`category_labels` 加入 `fundamental`，catalog 輸出 `source`）；`app/line_flows.py`（選單只列 native）；`templates/strategy_center.html`（卡片顯示來源與差異說明）。
- **不修改**：回測核心（`core/backtest/*`）、成本與滑價、`4_run_backtest.py`、`5_push_to_line.py`、`jobs/scheduler.py`、`strategy_settings.json` 內容、歷史資料庫紀錄。
- **營運影響**：每日 `run_daily` 落庫的策略數由 7 支增為 7＋N 支，執行時間會增加，需實測並記錄。CLI `all` 也會展開成全部策略。
- **相容性**：原 7 支的 id、alias、行為與 `/api/strategies` 既有欄位不變。
