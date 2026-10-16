# 2026-10-16 Web UI 一致性驗證

交易台改版 Phase 0：WEBTEST 為 67 passed、4 failed、3 xfailed。既有失敗為前次中文化後殘留的 Unavailable、Active Strategies、Technical／Margin、Local fallback 文字斷言。基線與三種寬度共 21 張截圖保存在本機 `openspec/changes/2026-10-16-redesign-web-ui-trading-desk/`。

變更：`2026-10-16-improve-web-ui-consistency`，Phase 0～5。

| 階段 | 測試結果 |
| --- | --- |
| Phase 0 | 修正過時的 local adapter mock 後，34 passed |
| Phase 1 | 42 passed |
| Phase 2 | 43 passed |
| Phase 3 | 44 passed |
| Phase 4 | 51 passed |
| Phase 5 | 56 passed、959 deselected、6 warnings |

最終執行：

```powershell
myenv/Scripts/python.exe -m pytest test -q -k web
git diff --check
openspec validate 2026-10-16-improve-web-ui-consistency --strict
```

三項檢查通過。涵蓋閏年的 365 天日期預設、儲存預設不勾選、正負報酬色、缺值原因、可執行 JavaScript 漲跌符號檢查，以及既有 Web／LINE 相容性與 characterization 測試。

Chrome headless／Playwright 驗證六頁 × 1440／900／390px 共 18 個案例：唯一非空 h1、無頁面水平溢位、手機抽屜、推薦分組、個股 K 線與 44px 區間按鈕、表單停用回饋、結果分頁 ARIA 與 Plotly resize 均通過。另查六頁 console，無錯誤。次要文字對比為 6.33～7.52:1。

截圖與瀏覽器驗證使用固定模擬資料，沒有執行真實回測或資料庫寫入；此證據不代表正式行情或績效資料的正確性。未更動回測核心、策略、API、資料庫或 LINE 配色。

完整前後截圖、原始瀏覽器 JSON 與重現腳本，依既有忽略規則留在本機 `openspec/changes/2026-10-16-improve-web-ui-consistency/verification.md` 及同目錄內。

環境差異：使用專案 myenv（系統 Python 缺 pandas），RTK 未安裝。OpenSpec 1.3.0 的 status/apply 不接受日期開頭名稱；保留專案命名規則，以套件內建 `generateApplyInstructions` 讀取相同 context，strict validation 正常通過。

Phase 0：21 張改前截圖與 console JSON 完成，無 console 錯誤。

Phase 1：79 passed、4 個既有失敗、3 xfailed。Playwright 確認套用對話框取消無請求、權重錯誤聚焦、不定進度與停用回饋。
