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

Phase 2：80 passed、4 個既有失敗、3 xfailed；21 個瀏覽器案例無溢位、console 錯誤與過小互動目標。主按鈕白字／#0b6fd6 對比 4.93:1。中文徽章由 Jinja 對照輸出到 JS，無重複維護來源。

Phase 3：85 passed、1 個既有失敗、3 xfailed；補股名、當日查詢與排名保留測試通過。截圖發現 Tailwind flex 覆蓋手機隱藏規則，已提高選擇器優先序；手機狀態列改兩欄。最終驗收會重測側欄隱藏。

Phase 4：90 passed、1 個既有失敗、3 xfailed；名稱完全／包含／歧義／零筆解析與代號相容通過。Playwright 確認台積電查詢更新 ?id=2330、空白無圖表與缺值清單、五個區間正常。

Phase 5：90 passed、1 個既有失敗、3 xfailed。Playwright 驗證品質／技術分類正確、390px 最後卡片可捲至面板上方；預覽請求統一 top_n=6。

Phase 6：WEBTEST 91 passed、3 xfailed；修正最後一個既有英文文字斷言。回測摘要、結果指標列與單一 tab stop、系統中文徽章完成。
