# 2026-10-13 補檔驗證證據

## Source phase

- 修改前相關基線 42 passed；新增來源契約後 48 passed。
- 驗證官方日期不符、ALL 單一市場、兩市均空、法人缺資料阻擋，以及 SQLite 實際保存 NULL／真實零值差異。
- ALL 完整性檢查共用同一個合併 helper，移除原本兩段重複收尾。
- 移除無呼叫的舊直連抓取器與本地 requests import／HEADERS；引用證據見 docs/refactor/deletion_candidates.md。
- git diff --check 通過；openspec validate 2026-10-13-backfill-market-data --strict --no-interactive 通過。
- OpenSpec 1.3.0 的 new/status/instructions 不支援日期開頭 ID。依專案日期目錄規則直接建立與讀取全部 artifacts；validate 支援該格式。

## Operational phase

- 第一次補檔被來源日期檢查阻擋，行情未寫入。TPEx 舊收盤端點請求 d=115/10/12，payload date=20261013；modern dailyQuotes 使用 date=2026/10/12 正確回 date=20261012。
- 新 dailyQuotes 已含 ETF，移除非 JSON／重複 ETF 請求；TPEx 24 欄法人表自營商合計改為 22（16 為自行買賣）。新增對應回歸後 50 passed。
- 新增 dealer_buy、margin_balance、short_balance BIGINT NULL DEFAULT NULL，保留舊欄位與歷史資料。補檔前月營收最新為 2026-09，財報最新為 2026Q2。
- 待寫入與實際 Web 驗證後補充。
