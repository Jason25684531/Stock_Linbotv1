# 2026-10-13 策略中心驗證

## 修正結果

股票預覽依正確 API envelope 路徑渲染。桌面與手機名稱關聯原生 checkbox；結果放在策略目錄上方。HTTP 200 的應用錯誤顯示可重試錯誤，並保留其他策略結果。

## 證據

- 修改前相關測試：42 passed。
- 新增模板渲染回歸測試修改前失敗：2330 在 API envelope 中，但模板 x-for 求值得到 []。
- 最終相關測試：43 passed，6.29 秒。包含 strategy center、Web UI、recommendation API 與基本面研究／Fresh OOS／production gate。
- 真實 Chrome 155.0.8059.40、Alpine 3.17.4：1440×1000 桌面與 390×844 手機實際點選名稱可更新選取。股票 2330 台積電與正確連結可見，同股兩策略分別呈現；單策略 HTTP 200/status=error 顯示診斷、保留另一策略，重試恢復。沒有 Alpine 或 Uncaught Console 錯誤。使用本機隔離 Flask 與受控 API；CDN 憑證除錯參數不代表部署憑證驗證通過。
- 本機真實推薦 API 逐一讀取八策略：七個 legacy 策略皆 stale，最後推薦 2026-06-26，market anchor 2026-10-08；基本面 blocked。
- 門檻隔離探針：歷史 `_gate` 可接受 CAGR=-0.5、MDD=-0.99 的合成輸入；`fresh_oos_verdict` 在缺少 audit、其他傳入條件满足時仍 PASS。探針没有落庫或重新執行研究。
- `git diff --check` 通過。
- `openspec validate 2026-10-13-fix-strategy-center-preview --strict --no-interactive` 通過。
- `127.0.0.1:1688` 原服務仍快取舊模板；確認原啟動腳本與程序身分後，沿用腳本、隱藏視窗重新啟動。HTTP 200 模板確認股票 envelope、名稱 label、應用錯誤修正均已生效。Chrome 連入真實本機服務，點選 Defensive Low Volatility 後兩策略皆顯示 stale／2026-06-26，沒有變更 Daily 設定。

測試命令：

```powershell
python -m pytest test/test_strategy_center.py test/test_web_ui_integration.py test/test_web_strategy_chart_integration.py test/unit/runtime/test_fresh_oos_promotion.py test/unit/runtime/test_fundamental_production.py test/unit/research/test_fundamental_strategy_validation.py -q
```

## 驗收範圍與剩餘問題

完整檢驗見 `docs/strategy-center-audit-2026-10-13.md`。此單一修正 Phase 包含模板、回歸測試與驗證文件，可一起 revert。

前端修正不會產生新推薦。Daily pipeline 產出停滯、漂移證據缺失誤標、audit 缺失的判定及凍結績效門檻仍列為後續事項；沒有啟用基本面 production、修改策略或回測核心、重跑正式 pipeline 或改寫歷史資料。外部部署環境尚未驗證。
