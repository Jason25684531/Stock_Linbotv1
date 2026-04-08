# Tasks: 2026-03-13-fallback-recommendations-on-circuit-breaker

## Phase 1：共用 fallback 邏輯

- [x] **1.1 新增推薦資料 fallback helper**
  - 檔案：`tool/db_helper.py`
  - 新增共用函式，當 `get_market_trend(date) != 'BULL'` 時，自動回溯最近一個有推薦且市場非熔斷的交易日。
  - 驗收：helper 可回傳 `DataFrame + metadata`，metadata 至少包含 `requested_date`、`recommendation_date`、`fallback_used`、`market_circuit_breaker_active`。

- [x] **1.2 統一風險提示文字**
  - 檔案：`tool/db_helper.py`
  - 新增共用 notice formatter，供 Dashboard 與 Line Bot 共用。
  - 驗收：fallback 與無 fallback 兩種熔斷情境都能產生清楚中文提示。

---

## Phase 2：顯示與 API 串接

- [x] **2.1 更新 Line Bot「推薦」查詢流程**
  - 檔案：`app.py`
  - `get_strategy_recommendation()` 改用 fallback helper；若為 fallback 推薦，需在文字與 Flex 訊息中顯示風險提示。
  - 驗收：熔斷日輸入「推薦」時仍可看到最近安全推薦日的標的與警示。

- [x] **2.2 更新 `/api/daily-signals`**
  - 檔案：`app.py`
  - API 回傳 `fallback_used`、`requested_date`、`market_warning` 等 metadata。
  - 驗收：API JSON 能明確標示是否為 fallback 與來源日期。

- [x] **2.3 更新 Dashboard Live Signals 顯示**
  - 檔案：`templates/dashboard.html`
  - 顯示 fallback 警示文字，空資料時也優先顯示風險說明而非通用訊息。
  - 驗收：熔斷日頁面可看到「非今日新訊號」警示。

---

## Phase 3：推播與驗證

- [x] **3.1 更新 `5_push_to_line.py` 早晚推播**
  - 檔案：`5_push_to_line.py`
  - V36 精選與各策略摘要改用 fallback helper，避免熔斷日推播空白。
  - 驗收：熔斷日推播仍可發送推薦清單，且文案含風險提示。

- [x] **3.2 補上測試**
  - 檔案：`test/test_recommendation_fallback.py`
  - 驗收：至少覆蓋 helper 找到 fallback、安全日不存在、API 回傳 fallback metadata 三個情境。

- [x] **3.3 完成 checklist 更新**
  - 檔案：`openspec/changes/2026-03-13-fallback-recommendations-on-circuit-breaker/tasks.md`
  - 驗收：實作與測試完成後，所有項目改為 `- [x]`，且與實際狀態一致。