# Tasks: 新聞情緒加分機制 + 模型重訓

## Phase 0: 模型重訓（前置作業）

- [x] **0.1** 確認 `strategy_settings.json` 包含要重訓的策略（至少 V36、V38）
- [x] **0.2** 執行 `python 3_train_model.py`，用清洗後數據重訓所有啟用策略的模型
- [x] **0.3** 驗證：模型檔案更新時間 > 執行時間，且 `ML_Data/pkl/` 下對應 `.pkl` 存在
- [x] **0.4** 執行 `python 2_rundaily.py` 確認新模型的 AI Score 分佈合理（V36 Top1: 58.54%）

**驗收**：`ls -la ML_Data/pkl/` 確認模型更新；`2_rundaily.py` 輸出 Top5 的 AI Score

---

## Phase 1: 產業對照表

- [x] **1.1** 確認 DB 中是否已有產業分類欄位 → 無，改用 JSON 對照檔
- [x] **1.2** 從 TWSE/TPEX OpenAPI 爬取 1,957 檔產業分類，存為 `tool/stock_sector_map.json`
- [x] **1.3** 在 `tool/db_helper.py` 新增 `get_stock_sector()` + `get_stocks_by_sectors()` 函式

**驗收**：`python -c "from tool.db_helper import get_stock_sector; print(get_stock_sector('2330'))"`  輸出產業名稱

---

## Phase 2: Gemini 族群萃取

- [x] **2.1** 在 `tool/news_agent.py` 新增 `get_news_sector_boost()` 函式（含 JSON 解析 + 標籤驗證）
- [x] **2.2** 在 `config.py` 新增 `NEWS_BOOST_ENABLED` / `NEWS_BOOST_FACTOR` / `NEWS_BOOST_MAX`

**驗收**：`python -c "from tool.news_agent import get_news_sector_boost; print(get_news_sector_boost())"` 輸出含 sectors 的 dict

---

## Phase 3: 選股加分整合

- [x] **3.1** 修改 `2_rundaily.py`，在 AI 評分後加入新聞族群加分邏輯（含 global cache）
- [x] **3.2** 在推薦結果輸出中標注受新聞加分的個股（`📰 新聞加分: 2 檔...`）
- [x] **3.3** 驗證：8926 從 39.13% 加分至 43.04%，排名從 #3 升至 #1（+10%生效）

**驗收**：`2_rundaily.py` 日誌顯示 `📰 新聞加分: xxx (+10%)`，推薦結果合理

---

## Phase 4: 端到端驗證

- [x] **4.1** 完整流程測試：爬蟲→選股→新聞加分 全流程正常
- [x] **4.2** 推薦 8926（台汽電/油電燃氣）與新聞利多族群一致，消息面已連結選股
- [x] **4.3** 確認 `NEWS_BOOST_ENABLED = False` 時，不執行新聞分析（開關正常）
