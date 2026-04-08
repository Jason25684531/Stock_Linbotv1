# 熔斷日改用最近安全推薦日

**Change ID**: `2026-03-13-fallback-recommendations-on-circuit-breaker`
**建立日期**: 2026-03-13

---

## 背景 (Context)

目前策略層在大盤未站回 MA60 時會觸發市場熔斷，直接停止當日選股。這個行為本身是合理的風控，但會連帶造成兩個使用體驗問題：

1. Line Bot 推播在熔斷日可能完全沒有推薦標的。
2. Dashboard 的即時選股區塊會顯示空白，看起來像系統故障，而不是風控生效。

使用者希望保留「不在熔斷日新開倉」的原則，但至少能顯示最近一個非熔斷交易日的推薦結果，並明確標示這是高風險的參考名單，而非今日新訊號。

---

## 目標 (Objectives)

1. 熔斷日若當日無推薦，系統自動回溯最近一個非熔斷日的推薦資料。
2. Dashboard 與 Line Bot 共用同一套 fallback 規則，避免行為不一致。
3. 所有 fallback 推薦都必須附上明確風險提示與來源日期。

---

## 架構影響 (Architecture)

| 檔案 | 修改範圍 | 說明 |
|------|---------|------|
| `tool/db_helper.py` | 新增共用 helper | 判斷是否熔斷、尋找最近安全推薦日、回傳 fallback metadata |
| `app.py` | `推薦` 與 `/api/daily-signals` | 優先使用共用 helper，並回傳/顯示風險提示 |
| `templates/dashboard.html` | Live Signals 區塊 | 顯示 fallback 警示與來源日期 |
| `tool/line_message_builder.py` | Flex Carousel | 支援顯示 fallback notice |
| `5_push_to_line.py` | 早晚推播 | 熔斷日改推最近安全推薦日，並提醒風險 |
| `test/test_recommendation_fallback.py` | 新增測試 | 驗證 helper 與 API fallback 行為 |

> 此變更不修改 DB schema，僅在現有推薦資料查詢流程上增加安全回溯邏輯。