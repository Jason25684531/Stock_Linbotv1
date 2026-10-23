# 2026-10-23 Design

## 0. 四軸狀態，彼此不互相推論
| 軸 | 值 | 意義 |
|---|---|---|
| ENGINE | PASS / FAIL | 程式在合成資料下的行為與契約正確（既有測試加本輪新增） |
| DATA | PASS / FAIL / BLOCKED（逐資料集、逐範圍） | 策略所需的每個輸入，在它使用的範圍內都經認證 |
| BACKTEST | DEVELOPMENT / DIAGNOSTIC / BLOCKED | DEVELOPMENT：ENGINE_PASS、所需資料 DATA_PASS、在開發期界線內、成交稽核通過；DIAGNOSTIC：資料或成交稽核未全部通過；BLOCKED：缺必要資料 |
| EVIDENCE | INSUFFICIENT / CANDIDATE_FOR_INDEPENDENT_VALIDATION | 只有 DEVELOPMENT 結果通過第 5 節的門檻才是 CANDIDATE。這不是「有效」，有效要靠各家族自己的 OOS 規則 |

## 1. 各家族既有界線（不可改）
| 家族 | 開發期上界 | 不得讀取 | OOS 規則（沿用） |
|---|---|---|---|
| G2/G3（3） | 2026-07-28（Frozen cutoff） | 2026-07-29 起的 Fresh OOS ledger（只有 Frozen Candidate 累積，本輪唯讀） | 180 交易日、9 個月、3 次調倉 |
| D5（1） | 既有研究期 | — | 沒有 Fresh OOS ledger；開發期切分不得改稱 OOS |
| Day3（11） | 2025-12-31（train 2023-01-03～2025-06-30、validation 2025-07～12） | **2026-01-01～2026-07-28 嚴格 OOS 未開封，本輪絕不讀取** | 沿用 Day3 規則 |
| Native7（7） | 2026-10-22（只用規則，沒有模型） | — | 新模型 OOS 為 `training_data_end` 之後滿 252 個交易日 |

程式對每個家族的輸入資料先截斷到上界；verify 確認沒有讀取上界之後的列。

## 2. 共同資料認證層
| 資料集 | 來源 | 動作 | DATA_PASS 條件 |
|---|---|---|---|
| 價格、成交量、交易日 | MI_INDEX（2021-12-30～2026-10-22 已快取） | 重新請求 2026-10-23（1 次） | 每個平日都有已判定的官方回應，收盤表欄位結構通過 |
| 法人（T86） | Stage 2：914 次 | 季批次、可續傳、記錄放行時間 | 範圍內每個交易日都 OK，或由 MI_INDEX 確認休市 |
| 融資（MI_MARGN） | Stage 2：958 次 | 同上 | 同上 |
| 財報 | MOPS PIT v3（FY2013～FY2025，原始回應已在本機） | 依固定種子抽樣 200 份原始回應重新解析，比對儲存值與公告時間；不需新請求 | 抽樣全部相符且公告時間都有；FY2026 為 BLOCKED |
| 月營收 | MySQL 數值 | 沒有官方公告時間 | 維持 DATA_BLOCKED；用到營收的規則（V34、V35）在回測中為 BLOCKED |
| 股票池與下市 | MI_INDEX 每日清單；TWSE OpenAPI 下市清單（1 次） | 以每日官方清單作為當日股票池（point-in-time），下市股在下市前都會出現 | 下市清單與 MI_INDEX 最後出現日一致 |
| 除權息、現金股利 | TWT49U（2023～2026-07 已有；2022 年與 2026-07-28 之後各 1 次範圍請求） | 「息」事件（4,151 筆）的 `權值+息值` 就是現金股利；「權息」221 筆無法拆分 | 價格報酬：PASS；總報酬：持有期間沒有碰到「權息」事件時才可計算，否則該策略的總報酬為 BLOCKED |
| 基準 | MI_INDEX 的「發行量加權股價指數」與「發行量加權股價報酬指數」 | 已快取 | 兩者都報告 |
| 交易成本 | `config/settings.py`：FEE 0.1425%、TAX 0.3%、SLIPPAGE 0.1% | 基準成本與 2 倍壓力成本 | 不修改常數 |
| 成交限制 | MI_INDEX OHLC 與前一日收盤 | 漲跌停價依前一日收盤 ±10% 與升降單位推得 | 用於第 4 節的成交稽核 |

MySQL 只當作對照，另外評等，不作為回測輸入。

下載重用 `scripts/official_data_recovery.py` 的 `acquire_chip`、`reserve` 與 ledger。新增「放行時間」：在 `RequestGate.wait()` 返回後立即記錄 `RELEASED`，做法是包一層 gate，不修改 `core/research/sources/twse.py`。預算上限 1,900 次，含重試。

## 3. Track A：6 項修改與一致性
1. `build_feature_history`：`dealer_consec_days` 依該股歷史，用 `calculate_consec_days` 計算，dealer 缺值則為 NaN。`build_multi_factor_matrix` 在這個欄位已存在時不重算，`institutional_consec_buy_days` 因此正確。
2. z-score 母體：`NativeResearchData.frame` 改為先以「模型 17 個特徵 ＋ 規則輸入」判定當天的完整案例，在這個全市場母體上算 z-score，然後才交給策略規則篩選。recovered 每日選股與研究走同一個函式。
3. 只納入 TWSE 上市代號：recovered 的歷史以當日 MI_INDEX 上市清單過濾；研究快照本來就是上市。
4. `load_model_contract`：讀取 `training_data_end` 與 `spec_sha256`。有 `training_data_end` 時以它作為視窗上界；有規格雜湊時比對 `retraining_spec.json`。舊模型仍會因缺 `training_data_end` 而 BLOCKED。
5. `recovered_strategy_frame`：模型帶有規格雜湊時，不加 `CHIP_SCORE_TRAINING_DEFINITION_UNPROVEN`。
6. `core/research/model_training.py`：
   - `build_training_frame(provider, sessions, spec)`：逐交易日呼叫與推論相同的 frame 函式，加上規格定義的標籤；沒有完整預測期的列刪除。
   - `train(...)`：沒有 `owner_approval.json`（內含規格 SHA-256 與簽核人）就直接 raise。本輪只用合成資料測試。

一致性測試：對同一份合成資料與同一天，`build_training_frame` 的特徵欄位與 `run_daily.recovered_strategy_frame` 的輸出逐值相等（rtol 1e-9、atol 1e-12），欄位順序相同。

影響：recovered 與研究的排序會改變（z-score 母體不同）；legacy 正式路徑與舊封存結果不變。

## 4. Track B 重跑與共用成交稽核
- **重跑**：用各家族自己的引擎與既有參數（G2/G3 36 組、D5 48 組、Day3 300 組，共 384 組），只把輸入換成認證過的官方面板，並截斷到開發期上界。先做計時探測；若全部重跑預估超過 4 小時，就先跑各家族已登記與 Frozen 的設定，其餘列為待辦並說明。
- **公式檢查**：
  - Day3 的 4 個因子：以參考公式在合成資料上逐值比對。
  - A191_132：在相同面板上比較「20 日平均成交金額」與「VWAP × volume」的值、排名相關與前 N 名重疊；結果標記 IMPLEMENTATION_APPROXIMATE 並附差異數據。
  - D5 的 5 個因子：視窗長度與歷史是否足夠。
  - G2/G3：公告時間早於訊號日、修訂版本、YoY 基期。
- **成交稽核**（與引擎無關，對每筆成交或目標權重變動事後檢查）：
  - 成交日是訊號日的下一個交易日，成交價為當天開盤價；
  - 買進遇到開盤即漲停鎖死、賣出遇到開盤即跌停鎖死，視為不可成交；
  - 停牌或成交量為 0；
  - 下市後仍持有；
  - 成交金額占當日成交金額比例超過上限（提案 5%，OD-23）。

  被旗標的成交占比 > 1% 時，該結果最高只能是 DIAGNOSTIC。不修改引擎，也不刪除交易。

## 5. 統計檢定（只用於 DEVELOPMENT 結果）
- **報告**：CAGR、Sharpe、MDD、勝率、交易次數、年化換手率、扣成本後報酬（基準與 2 倍成本）、相對 TAIEX 價格指數與報酬指數的超額報酬、樣本期間、認證狀態。
- **穩定性**：
  - 以 TAIEX 報酬指數 200 日均線把期間分成多頭與空頭，另做逐年分段；
  - 收益集中度：貢獻最大的 5 檔股票、最好的 5% 交易日分別占總報酬的比例；
  - moving block bootstrap 的 Sharpe 95% 信賴區間（沿用 `moving_block_bootstrap`）。
- **選擇偏差**：Deflated Sharpe Ratio（Bailey & López de Prado），試驗次數為該家族研究時試過的設定數（G2/G3 36、D5 48、Day3 300，不跨家族合併）。
- **進入獨立驗證的門檻**（提案值，OD-24），全部成立才標 CANDIDATE_FOR_INDEPENDENT_VALIDATION：
  - 2 倍成本下 Sharpe > 0；
  - bootstrap 信賴區間下界 > 0；
  - DSR > 0.95；
  - 前 5 檔股票、前 5% 交易日的貢獻都 < 50%；
  - 多頭與空頭兩段的超額報酬不同時為負。
- **多因子與單因子**：相同期間、成本與持股數下比較，報告差異的 bootstrap 信賴區間；區間包含 0 就寫「無法區分」，不宣稱多因子較好。

## 6. AI 與非 AI 比較
- **本輪可做**：7 個原生策略「只用規則」的 DEVELOPMENT 回測。
  - runner 加 `rules_only`：研究契約下不載入模型，依策略自己的順序排序。
  - growth_momentum_breakout、quality_growth 需要月營收，為 BLOCKED。
  - quality_value_low_volatility 只在 FY2025 財報可得的期間（到 2026-05-15）回測。
- **只提計畫**：
  - 「規則 ＋ AI 排名」與「規則」：同一獨立期間（新模型 `training_data_end` 之後）、同一股票池、同一成本。
  - 與 D5／Day3 因子、TAIEX 報酬指數比較：先預先登記指標與門檻；樣本不足 252 日時不比較。

## 7. Owner 決策（2026-10-23 核准，取代原提案預設）
| ID | 決定 | 實作影響 |
|---|---|---|
| OD-22 | 核准 Stage 2 | 請求上限 1,900（含失敗與重試）；季批次、可續傳；每次放行時記錄時間；間隔 ≥ 3.2 秒；單日最多嘗試 3 次；連續 5 次失敗停止該批次；每批完成後驗證日期、欄位、筆數與雜湊；不覆寫原始快取；官方與 MySQL 分開評等 |
| OD-23 | 流動性占比以 5% 為主，同時報告 1%、2%、5% | 各門檻分別統計超過的筆數與金額；只是事後旗標，沒有模擬未成交或部分成交，所以報告不得寫「已完成嚴格可成交性驗證」；超額成交影響顯著時降低證據等級；當日成交金額只用於事後稽核 |
| OD-24 | 採用五項門檻並加補充條件 | Bootstrap 的指標固定為「扣成本後的日超額報酬」（相對 TAIEX 報酬指數），並寫明區塊長度、抽樣次數、樣本數、信賴區間算法；DSR 的試驗次數用保守值（含全部 384 組與已知先前搜尋）；多空任一段樣本過短（< 60 個交易日）視為 UNDETERMINED，不算穩定；任一環境超額為負要另標風險；總報酬、成交限制或存活者偏差缺任一項認證，統計門檻通過也不得成為候選。通過只能標 `CANDIDATE_FOR_INDEPENDENT_VALIDATION` |
| OD-25 | 先計時探測，再決定重跑範圍 | 探測用各家族「依識別碼排序的第一組」固定設定，不看績效；預估全部 ≤ 4 小時才跑 384 組，否則先跑登記與 Frozen 設定，其餘 PENDING；384 組身分保留；Native7 只做規則版診斷回測 |
| OD-26 | 下市後持股不得預設強制出場 | 確認官方最後交易日、最後可成交資訊，並查證公司行動；沒有可證實的退出價格就標 `DELISTING_EXIT_UNVERIFIED`，該回測不得取得完整績效認證；分別報告「保守估值」與「有證據的處置」；不刪除下市股 |
| OD-27 | 「權息」混合事件維持 TOTAL_RETURN_BLOCKED | 只有可驗證的純現金股利（「息」）事件納入總報酬；「權值＋息值」不能當現金股利；資料缺漏時另報價格報酬並清楚標示；價格報酬不與含息基準比較；記錄事件對策略的影響與阻擋原因 |
| OD-28 | 規格維持 DRAFT_PENDING_OWNER_APPROVAL；核准 6 項工程修復，不核准訓練 | z-score 母體 = 當日 17 個模型特徵都完整的上市普通股，先算特徵與 z-score，再套策略規則；規則版回測不得因缺 AI 特徵排除股票；`dealer_consec_days` 與 `institutional_consec_buy_days` 依歷史；訓練與 recovered 逐值一致；訓練函式無簽核檔不得擬合；不產生新模型權重；研究端結果因此改變時，以新版本與新 manifest 保存 |
| OD-29 | 研究模式的持股估值修復（2026-10-24 核准） | `BacktestEngine(valuation_policy='legacy' \| 'finite_positive_stale')`，預設 legacy 且逐值不變（修改前產生的 characterization 與同一滑價種子的真實資料重播都相同）；研究政策只讓有限且 > 0 的收盤價更新估值，否則沿用最後有效收盤價並記錄 `STALE_VALUATION`，沒有有效價格時 `VALUATION_BLOCKED`；下單、成交、出場、成本、滑價、部位大小不變；Native7 重跑固定滑價種子 20261023，結果放在 `native7_od29/`，只標 DIAGNOSTIC 或 BLOCKED |

## 8. 完整性與安全
- 正式環境狀態檔被改寫是獨立完整性事件，在 Phase 0 追查並記錄；`verify` 的雜湊例外清單固定為「V34 一項已核准修改」，**不再新增任何例外**。動態狀態檔若再變動，報告為完整性事件，而不是視為通過。
- Git 歷史中的資料庫明文密碼維持 `SECURITY_ACTION_REQUIRED`，不輸出明文、不更換帳密。
- 本輪不產生任何 Fresh OOS PASS、`OOS_PASS`、`PROFITABLE_PROVEN`、`PRODUCTION_READY`。
