# 2026-10-19 任務

## 0. 基線

- [x] 0.1 執行 `python -m pytest test -q` 並記錄通過數。保存 `python 4_run_backtest.py` 對原 7 支策略的交易明細作為 characterization baseline（日期、代碼、方向、數量），並記錄一次 `run_daily` 的耗時。

## 1. 基礎設施（一支試點策略）

- [ ] 1.1 `core/strategy_manager.py`：`StrategyMetadata` 新增 `source="native"`；新增 `category` 值 `fundamental`。驗收：原 7 支的 metadata 與解析結果不變。
- [ ] 1.2 建立 `core/strategies/macd_cross.py`（來源：`TQ_MACD交易策略.ipynb`，`category=trend`，`source=tquant`），登錄進 `CANONICAL_REGISTRY` 與 `STRATEGY_METADATA`。
- [ ] 1.3 `app/web_server.py`：`category_labels` 加入 `'fundamental': 'Fundamental'`；catalog 項目輸出 `source` 與來源 notebook。`/api/strategies` 只新增欄位，不改既有欄位。
- [ ] 1.4 `app/line_flows.py`：`_list_strategy_picker_options()` 只列出 `source == "native"`。驗收：LINE 選單仍是原 7 支。
- [ ] 1.5 `templates/strategy_center.html`：`source=tquant` 的卡片顯示來源 notebook 與「依本專案引擎，績效與原 notebook 不同」的說明。需遵守 `openspec/specs/frontend-design.md`。
- [ ] 1.6 `test/test_tquant_strategies.py`：驗證登錄（id 無衝突、原 7 支 alias 解析不變、LINE 選單只含 native），並以固定 DataFrame fixture 驗證試點策略的 `filter_candidates()` 輸出。驗收：`python -m pytest test/test_tquant_strategies.py -q` 通過。
- [ ] 1.7 驗收：`python 4_run_backtest.py --strategy macd_cross`（參數依 CLI 實際介面）可以完成；`python -m jobs.run_daily --dry-run`（依實際介面）包含 `macd_cross`；在瀏覽器的 `/strategies` 點「技術」篩選可看到 MACD，預覽、Daily 與回測按鈕都依後端能力啟用。

## 2. 技術面批次（14 份，僅用 daily_market_data）

- [ ] 2.1 依 `design.md` 對照表的 P2 列，逐一建立 `core/strategies/*.py` 並登錄（`category=trend` 或 `reversion`）。每支策略都補一個 fixture 測試。
- [ ] 2.2 驗收：每支策略都能用 `python 4_run_backtest.py` 跑完；`design.md` 對照表的狀態更新為「已移植」或「不納入（原因）」。

## 3. 籌碼與營收批次（4 份）

- [ ] 3.1 逐一列出 notebook 所需欄位，對照本地 table 的實際欄位（以 `PRAGMA table_info` 或 schema 為證）。缺少必要欄位就標為不納入，不得用代用欄位。
- [ ] 3.2 移植通過稽核的策略（跟隨大戶、融資維持率歸 `flow`；月營收、尋找 Alpha 歸 `fundamental`），補上 fixture 測試，並以 `python 4_run_backtest.py` 驗收。

## 4. 基本面批次（12 份，先稽核）

- [ ] 4.1 對 P4 每份 notebook 執行 3.1 的欄位稽核，把結果與分類（`fundamental`，或以品質因子為主時用 `quality` / `value`）寫回 `design.md` 對照表。
- [ ] 4.2 只移植通過稽核的策略，補上 fixture 測試與回測驗收。若全部未通過，記錄結論後結束本 Phase。

## 5. 收尾驗證

- [ ] 5.1 重跑 0.1 的原 7 支回測，交易明細需與基線完全一致（浮點比較 `rtol=1e-9`、`atol=1e-12`）。
- [ ] 5.2 重新量測 `run_daily` 耗時，與基線一起記錄在 `design.md`。若超出排程容許時間，向使用者回報，不自行修改 settings。
- [ ] 5.3 執行 `python -m pytest test -q`（通過數不少於基線並加上新測試數）、`git diff --check`，以及 `openspec validate 2026-10-19-add-tquant-strategies --strict`。
- [ ] 5.4 確認 `strategy_settings.json` 沒有被寫入，LINE 選單仍是原 7 支。
