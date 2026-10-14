# 2026-10-14 實作任務

## 1. Phase 0：基準、引用證據與阻擋條件

- [x] 1.1 重讀 proposal／design／specs／audit，保存 dirty tree、目標 checksum、環境版本、settings identity 與 fixtures；不 stage 使用者既有修改。
- [x] 1.2 以 verification.md 原命令重跑 suite；另行修復並記錄 LINE reply 的既有 correctness failure，未全綠不進 Phase 1，不忽略／xfail／排除失敗案例。
- [x] 1.3 建立並通過完整受影響 characterization：Daily selection／persistence／heartbeat／dry-run，Backtest seed／trade sequence，Web／LINE／CLI／scheduler／runtime isolation；補足 records 數值、訊息與 cache fixtures。
- [x] 1.4 盤點六個 hash helper 原始碼檔是否被凍結證據消費；分類可改／DEFER，保存 parity baseline，不更改既有 fingerprints 或 manifests。
- [x] 1.5 補齊 `_freeze_mapping` 無引用證據到 docs/refactor/deletion_candidates.md，包括 static／importlib／getattr、test／script／docs／deploy 消費者、替代、驗證與 rollback；不完整即保留。
- [ ] 1.6 以僅包含本 Phase 的提交保存基準與證據，記錄 commit ID／可獨立 revert 範圍。

## 2. Phase 1：標準庫替換與私有死碼清理

- [ ] 2.1 對 Phase 0 允許的四個 `_sha256`、兩個 `_file_sha256` 改用 hashlib.file_digest，保留 signatures、OSError／None，不建共用 framework。
- [ ] 2.2 驗證 empty／binary／超過兩個 chunk／missing／OSError 的 digest、例外與 handle closure 相同；執行受影響 factor validation／PIT／universe recovery／runtime tests，確認 frozen evidence 不漂移。
- [ ] 2.3 證據完整時才刪 `_freeze_mapping` 區塊，保留其他 freeze／validation；執行 strategy contract／dependency boundary／registry parity tests。
- [ ] 2.4 完成相關測試、diff check、actual diff 和驗證紀錄；僅提交本 Phase hunks，保存 commit ID／revert 證據後才進下一 Phase。

## 3. Phase 2：Dashboard／LINE 相同 records 算術

- [ ] 3.1 在既有 dashboard_payloads.py 增加最多兩個 private pure functions，集中 breadth／volume 和 chip totals；line_flows.py 共用，不改 MCP／日期／cache／錯誤分支。
- [ ] 3.2 驗證 missing／NaN／zero／negative／fractional records，保留逐筆 int 後加總與單位；Web 不同 DataFrame 算術保持原樣。
- [ ] 3.3 驗證兩 caller 的 cache key／hit／miss／TTL／Taipei 跨日／None／empty／MCPClientError／一般錯誤、API payload 與 LINE 字面訊息和 baseline 相同；不發送實際訊息。
- [ ] 3.4 通過相關 Dashboard／Rich Menu／Web／LINE parity 與 characterization，保存獨立 Phase commit、rollback 與驗證證據。

## 4. Phase 3：完整相容性驗收

- [ ] 4.1 重跑受影響 tests 與 Daily／Backtest／CLI／Web／LINE／scheduler／runtime gate parity；日期／代碼／方向／順序／數量精確一致，浮點 rtol=1e-9、atol=1e-12，undefined metrics 保留 None 加原因。
- [ ] 4.2 在隔離 DB／測試服務驗證受影響讀寫／atomicity／無重複 persistence／health smoke，不觸及正式環境；缺環境明確記為未驗證，不宣稱完整運行驗收。
- [ ] 4.3 更新 docs/cleanup_audit_report.md、docs/cleanup_change_summary.md、docs/cleanup_validation_report.md，區分完成／DEFER／UNKNOWN、actual diff 與保留契約；不用 benchmark 推算本 repo 節省。
- [ ] 4.4 執行 git diff --check 和本 change strict validation，確認沒有未授權依賴／settings／DB／runtime artifact 變更，保存最後驗證與獨立提交／rollback 證據。