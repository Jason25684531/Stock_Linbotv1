## Context

2026-10-13 基線：相關 UI、API、基本面驗收測試 42 passed。現有測試只驗證 JavaScript 儲存的資料，沒有確認模板 x-for 真正渲染股票，因此漏掉 envelope 路徑錯誤。

讀取現有 strategy-center-ui、strategy-stock-results 規格及前次串接 change 的 proposal、design、tasks、verification 後確認：勾選是唯讀快照預覽，Daily 才寫設定，回測按鈕帶入既有回測表單。

## Decisions

1. 股票模板直接讀取完整 envelope 的 data.recommendations；不改 API 契約。
2. 名稱用 label 與 checkbox 的 id 關聯，桌面與手機使用不同前綴，沿用 Alpine x-model watcher。
3. 預覽移到目錄上方，保留逐策略 loading、timeout、retry、取消請求與選取隔離。
4. loadStrategy 同時檢查 HTTP 狀態與 payload.status，應用錯誤須能重試。
5. 以中文說明可用狀態是執行能力，不能當成績效驗收。

## Audit Scope

七個 legacy 策略的 lifecycle 在 adapter 建立時指定 PRODUCTION；catalog eligibility 直接使用該值，Daily／回測能力取自 manager 與 registry。基本面另外讀取 production gate。檢驗須區分註冊、歷史研究有效性、Fresh OOS、執行健康與正式上線。

目前七策略快照均 stale（最後推薦 2026-06-26，市場 anchor 2026-10-08）；基本面 gate 為 HISTORICAL_DRIFT，但當前 manifest 缺少該欄位且 reason=STALE_DATA，不能宣稱已證實漂移。Fresh OOS 0 天／0 月／0 次，最低要求 180／9／3。

## Verification

擴充既有 Node/UI 回歸檢查，並以真實 Chrome、Alpine、隔離 Flask 頁面確認股票連結、名稱選取、跨策略隔離、錯誤重試與手機操作。讀取本機真實 API 狀態，不執行策略或寫入正式資料。最後執行相关 pytest、git diff --check 與本 change OpenSpec strict validation。
