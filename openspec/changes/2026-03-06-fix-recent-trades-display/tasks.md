# Tasks: fix-recent-trades-display

## Phase 1：修復 JavaScript null 安全問題（前端）

- [x] **1.1 修復 `buy_price.toFixed(2)` null 崩潰**
  - 檔案：`templates/dashboard.html` 第 375 行
  - 將 `x-text="trade.buy_price.toFixed(2)"` 改為 `x-text="trade.buy_price != null ? trade.buy_price.toFixed(2) : '-'"`
  - 驗收：啟動 `python app.py`，開啟 Dashboard，確認表格所有列正常渲染，無 JS console error。

- [x] **1.2 修復 `sell_price.toFixed(2)` null 崩潰**
  - 檔案：`templates/dashboard.html` 第 376 行
  - 將 `x-text="trade.sell_price.toFixed(2)"` 改為 `x-text="trade.sell_price != null ? trade.sell_price.toFixed(2) : '-'"`
  - 驗收：同上，`sell_price` 為 null 的列應顯示「-」而非崩潰。

---

## Phase 2：修復後端 API CSV fallback（`app.py`）

- [x] **2.1 清除 CSV fallback 的 NaN 值**
  - 檔案：`app.py` `/api/trades` 路由
  - 在 `trades = df_recent.to_dict('records')` 前加入 `df_recent = df_recent.where(pd.notnull(df_recent), None)`
  - 驗收：執行 `curl http://localhost:5000/api/trades`，回傳合法 JSON（不含 NaN）。

- [x] **2.2 補齊 CSV fallback 的 `strategy` 欄位**
  - 檔案：`app.py` `/api/trades` 路由
  - 若 `df` 不含 `strategy` 欄位，補入 `df['strategy'] = 'unknown'`
  - 驗收：CSV 中無 `strategy` 欄時，API 回傳每筆 `strategy: 'unknown'`，策略篩選選「全部」可見全部資料。

---

## Phase 3：修復 DB 回測資料保留策略（`db_helper.py`）

- [x] **3.1 改為按 strategy 清空而非全表刪除**
  - 檔案：`tool/db_helper.py` `save_backtest_results()` 函式
  - 在 `trades_df` 不為空時，改為：先從 `trades_df` 提取所有出現的 `strategy` 值，僅 `DELETE FROM backtest_trades WHERE strategy IN (...)` 清除對應策略的舊資料，再插入新資料。
  - 如果 `trades_df` 為空，維持原本清空全表行為。
  - 驗收：執行 `python 4_run_backtest.py --v33_low_vol`，確認 V33 資料被更新；再執行 `python 4_run_backtest.py --v31_hybrid`，確認 V31 寫入後 V33 舊資料仍在。可用 `curl http://localhost:5000/api/trades | python -m json.tool | head -100` 驗證兩種 strategy 均存在。

---

## Phase 4：整合驗收

- [x] **4.1 完整端對端測試**
  - 啟動 `python app.py`
  - 開啟 Dashboard，確認：
    - [x] 「近期交易」表格顯示至少 1 列（有回測資料前提下）
    - [x] 策略篩選下拉選單切換後，對應資料正確篩選出現
    - [x] 買入價、賣出價欄無空白或 JS 錯誤（含 null 值情況）
    - [x] 瀏覽器 DevTools Console 無 `TypeError: Cannot read properties of null (reading 'toFixed')` 錯誤

- [x] **4.2 回歸測試**
  - 執行 `pytest` 確認原有測試仍全數通過：**117/117 passed** ✅
