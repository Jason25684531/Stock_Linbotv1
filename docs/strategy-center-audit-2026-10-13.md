# 2026-10-13 策略中心用途與驗收檢驗

## 結論

策略中心是策略目錄、已落庫選股結果預覽、Daily 設定入口與回測入口。勾選策略讀取快照，不會立即計算選股。頁面原有股票列表 envelope 路徑錯誤、名稱沒有點選動作，以及 HTTP 200 應用錯誤被當成空結果，已在本次修正。

目前頁面上的 `PRODUCTION`、`AVAILABLE` 不代表策略績效通過驗收。舊策略以註冊／執行支援判定；基本面另有嚴格的上線證據門檻。此次檢驗保留既有策略條件、研究門檻、正式資料、設定與上線旗標。

## 操作用途

| 操作 | 實際行為 | 資料來源 |
| --- | --- | --- |
| 點策略名稱／勾選框 | 獨立讀取每個策略最多 5 檔股票、日期與快照狀態 | `/api/market/recommendations?strategy=<canonical_id>&top_n=5` |
| 套用到 Daily | 更新每日啟用策略，轉回 Dashboard；本身不觸發新一輪篩選 | `/update_strategy` → `set_active_strategies` |
| 執行組合回測 | 將所選策略帶入回測頁表單；需在該頁設定並提交回測 | `/backtest?strategies=...` |
| 點股票 | 進入對應個股分析 | `/stock-analysis?id=...` |

同時選擇不支援 Daily／回測的基本面策略時，整組相關操作會停用並列出原因。這是現有支援契約；基本面不寫入 legacy Daily 設定，也沒有七策略回測 registry 支援。

## 本機實際資料狀態

2026-10-13 以 Flask test client 讀取本機真實資料庫 API；沒有執行 pipeline 或改寫推薦歷史。

| 策略 | 別名 | 最後推薦 | 當前預覽 |
| --- | --- | --- | --- |
| hybrid_trend_rank | V31 | 2026-06-26 | stale，0 檔可用目前推薦 |
| defensive_low_volatility | V33 | 2026-06-26 | stale，0 檔可用目前推薦 |
| growth_momentum_breakout | V34 | 2026-06-26 | stale，0 檔可用目前推薦 |
| quality_growth | V35 | 2026-06-26 | stale，0 檔可用目前推薦 |
| institutional_flow_confirmation | V36 | 2026-06-26 | stale，0 檔可用目前推薦 |
| mean_reversion | V37 | 2026-06-26 | stale，0 檔可用目前推薦 |
| quality_value_low_volatility | V38 | 2026-06-26 | stale，0 檔可用目前推薦 |
| fundamental_g2g3_top5_reb60_score_weighted_v1 | 無 | 無正式快照 | blocked |

市場 anchor 為 2026-10-08。七策略的 0 檔是過期政策拒絕歷史推薦的結果，不能解讀為當天已執行篩選而無股票符合。

基本面 runtime manifest 的 CURRENT_RUN_DATE=2026-10-11、status=BLOCKED、reason=STALE_DATA。Fresh OOS 報告為 INSUFFICIENT_DATA，有效樣本 0 個交易日／0 個月／0 次再平衡；最低要求 180／9／3。Promotion 為 SHADOW_APPROVED、PRODUCTION_READY=NO。

## 策略驗收邏輯

### 舊策略目錄的狀態是執行能力

`core/strategy/adapters/legacy.py` 建立七策略 spec 時指定 `StrategyLifecycle.PRODUCTION`。`app/web_server.py::_build_strategy_catalog` 將 lifecycle 直接放入 eligibility；Daily 只檢查 strategy 可載入且存在於 manager 清單，Backtest 檢查回測 registry。

因此在這個頁面看到 AVAILABLE，只能證明此流程支援該策略。此路徑沒有檢查 Sharpe、成本壓力、樣本外績效或 Bootstrap，也不能據此認定這些策略已完成投資績效驗收。頁面已補充這個差別。

### 基本面歷史研究有效性

`core/research/fundamental_strategy_validation.py::default_strategy_search_spec` 與 `jobs/run_fundamental_strategy_validation.py::_gate` 依序檢查：

| 門檻 | 現行條件 | 判定範圍 |
| --- | --- | --- |
| 有效性 | 至少 60 筆報酬、1 筆交易，CAGR／Sharpe／Sortino／MDD／turnover 為有限值 | 樣本与指標可計算 |
| 執行 | execution audit pass，訊號先於成交、非再平衡日不可發出調整訂單 | 成交規則 |
| 報酬／風險 | CAGR >= -1.0、MDD >= -1.0 | 門檻允許非常差的績效 |
| 成本 | stress CAGR >= -1.0，且不得高於 base CAGR（允許 1e-12 誤差） | 成本方向合理 |
| 時間穩定性 | temporal PASS、正報酬 fold 比例至少 0.5 | 跨時段表現 |
| 參數穩定性 | 相鄰參數穩定性 PASS 後進 shortlist | 參數敏感度 |

排序公式為 CAGR 與 Sharpe 的標準化分數，加上負值 MDD 的標準化分數、扣除成本拖累分數；最多列出 5 個 shortlist，再凍結第一個候選。

Bootstrap 與因子冗餘報告會生成，但沒有作為拒絕 shortlist 的硬門檻。實際 FrozenStrategySpec 候選的歷史 CAGR 約 39.84%、Sharpe 約 1.148、MDD 約 -36.19%、stress CAGR 約 38.59%；這些是已保存的歷史研究結果，不能替代 Fresh OOS。

以隔離輸入呼叫 `_gate` 證實：CAGR=-0.5、MDD=-0.99、stress CAGR=-0.6，在其他有效性與穩定性條件滿足時仍回傳 `(True, [])`。這符合目前門檻，但說明 `strategy_valid` 不是投資建議或嚴格績效驗收。

### Fresh OOS 與正式上線

60 日再平衡的基本面候選要求 cutoff 2026-07-28 之後至少 180 個交易日、9 個月、3 次再平衡。`fresh_oos_verdict` 再檢查指紋相同、有限報酬、有成交、總報酬 > 0、MDD >= -1.0。

`fundamental_production.check_production_eligibility` 先檢查 evidence 檔、strategy identity／fingerprint／artifact hash、歷史選股漂移=0，以及 PIT、資料新鮮度、因子健康、research/runtime parity、runtime replay 五項 PASS。接著檢查 Fresh OOS、promotion 的 PRODUCTION_READY=YES 與可接受階段，最後檢查 ENABLE_FUNDAMENTAL_PRODUCTION。任一證據不足會阻擋。

目前 `_promotion` 最多自動產生 LIMITED_CAPITAL_CANDIDATE，仍設定 PRODUCTION_READY=NO；通過 Fresh OOS 不會自動上線。Broker submission 始終 DISABLED。

## 尚待處理的驗收證據問題

1. **缺少漂移證據被誤標為已漂移。** `_evidence` 使用 `validation.get('HISTORICAL_SELECTION_DRIFT') not in (0, '0')`；欄位缺失也回報 HISTORICAL_DRIFT。當前 manifest 正是缺少該欄位，不能從此錯誤碼推論實際選股有改變。安全阻擋仍有效，但原因應區分 PROMOTION_EVIDENCE_INVALID 與非零漂移。
2. **Fresh OOS audit 缺失仍可 PASS。** `fresh_oos_verdict` 只在 audit 欄位明確為 False 時 invalidated，缺少 audit 時將 implementation_valid／no_leakage 寫成 True。以相同指紋、正報酬、有限報酬、有交易且未傳入 audit 的隔離呼叫可重現 PASS。現有 production gate 另有證據檢查，這個現象本身不能證明正式上線可被繞過；但單独 verdict 不足以作為完整驗收證明。
3. **績效政策需要明確決策。** -100% 的 CAGR／MDD 下限與沒有 Bootstrap 硬門檻是目前凍結的規則。若要增加最大回撤、Sharpe、統計顯著性或成本壓力門檻，應以新研究契約與指紋驗證，不直接修改既有凍結產物。
4. **推薦產出已停滯。** 修正 UI 只能呈現 stale／missing；恢復新推薦須追查每日排程與落庫流程。此次只做檢验與前端修正，沒有重跑或覆寫正式推薦。

## 本次修正與驗證

- 股票列表讀取完整 envelope 的 `results[id].data.data.recommendations`，並使用原生 label 讓桌面／手機名稱可勾選。
- 結果區移到目錄上方；中文說明用途與狀態限制。
- HTTP 200 的 status=error 顯示診斷與重試；其他策略的成功結果保留。
- 修改前既有測試 42 passed；新增渲染回歸測試修改前失敗（API 有 2330，但實際 x-for 得到空列表）。
- 真實 Chrome 155.0.8059.40、Alpine 3.17.4、隔離 Flask API：桌面 1440×1000 與手機 390×844 均以實際名稱點選確認狀態更新；2330 台積電連結可見；兩策略可同時顯示相同股票；單策略應用錯誤與重試不影響另一策略。沒有 Alpine／Uncaught Console 錯誤。瀏覽器使用忽略憑證錯誤的除錯參數，驗證範圍為本機頁面功能，未驗證部署環境的 CDN 憑證信任。
- 自動回歸、diff check、OpenSpec validation 的最終結果見同 change 的 verification.md。
- 本機 `http://127.0.0.1:1688/strategies` 原先快取舊模板，已以相同啟動腳本重新啟動本機 Web。真實 Chrome 連入該服務、點選 Defensive Low Volatility 後，兩策略均顯示「推薦已過期 · 最後紀錄 2026-06-26」；目前本機頁面已載入修正。
