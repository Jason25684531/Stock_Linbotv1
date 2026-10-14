# 2026-10-14 系統架構清理設計

## Context

現行入口為 app.py → app/、jobs/、services/mcp/server.py。Compose 已以 requirements.runtime.txt／requirements.mcp.txt 分別部署應用與 MCP。jobs/scheduler.py 為排程唯一 owner。StrategyManager 管 settings／cache；Registry／Runner／LegacyStrategyAdapter 管身份解析與選股委派；Daily 管 enrichment／persistence；Backtest 管成交與會計；Fundamental runtime 管 shadow／OOS／promotion。

以當前 dirty tree 為依據，保留使用者修改。2026-10-07 已做 composition 與依賴 ownership 整理，不重複建立框架。初步測試 125 passed／1 failed，失敗可獨立重現；未達全綠 baseline，尚不能開始清理 source。

## Goals / Non-Goals

**Goals:** 消除有證據的重複，保持所有外部與交易契約，以逐 Phase 驗證及獨立提交控制風險。

**Non-Goals:** 不新增 DI container／repository interface／泛用 serializer／cache framework，不拆微服務，不拆解 DB helper 或 Daily，不大搬 Dashboard，不裁減 UNKNOWN 依賴，不修改策略、成交、成本、滑價、production gate，不重算正式產物。

## Decisions

### 1. 現有 owner 保留，先消除局部重複

Production Python 共 173 檔／44,007 行。db_helper.py 2,665 行、dashboard_payloads.py 1,642 行、run_daily.py 1,075 行顯示責任集中，但不是無用程式證據。大型拆檔只會搬移複雜度並放大 import／patch 風險，列為 DEFER。

### 2. 雜湊直接使用標準庫

四個 6 行 `_sha256` body AST 相同：jobs/run_factor_validation.py:67、core/research/fundamental_factor_validation.py:115、core/runtime/fresh_oos_promotion.py:49、core/runtime/fundamental_advancement.py:42。兩個 9 行 `_file_sha256` 也相同：core/research/universe_recovery.py:237、core/research/fundamentals/pit.py:601。

Python 3.11 的替代為：

```python
with path.open("rb") as handle:
    return hashlib.file_digest(handle, "sha256").hexdigest()
```

保留 signature、handle closure 和原本的 try／except OSError：兩個函式回 None，其餘拋出。限定這六個，不建立跨 research／runtime 共用 hash service。其他 canonical JSON／bytes／digest 函式不混改。

原始碼 hash 可能被凍結 evidence 消費。Phase 0 必須逐檔確認；不得重寫既有 manifests、fingerprints 或 expected hashes 使 gate 通過。來源 hash 漂移需另走 evidence-update change；本 change 暫緩該候選。

### 3. 死碼先證明才刪

core/strategy/spec.py:35-43 的 `_freeze_mapping` 9 行，hidden repo search 只有定義。apply 前須在 docs/refactor/deletion_candidates.md 補齊靜態、importlib／getattr 動態字串、tests、scripts、docs、deployment 引用證據，記錄 replacement=nothing、風險、驗證與 rollback。證據不足保留。不刪任何非快取檔案，不移除 `_freeze` 或 immutable validation。

### 4. Dashboard／LINE 僅共用相同 records 算術

dashboard_payloads.py:107-110／line_flows.py:620-624 的 rising、falling、flat、volume 相同；兩邊法人總和也是逐筆 `int(safe_float(value) or 0)` 後相加。在現有 dashboard_payloads 加最多兩個 private pure functions，輸出少量 tuple／dict，LINE 呼叫。不新增 package／class。

取資料、MCP 呼叫次數、日期來源、snapshot／message cache keys、TTL、Taipei 跨日、None／empty／MCPClientError／一般錯誤與字面訊息仍由各 caller 負責。LINE 不直接改用 snapshot builder，以免改 cache 與錯誤格式。

Web `_build_institutional_totals` 為加總後 int，與逐筆 int 後加總有小數差異；market light 的缺值和分母亦不同，兩者不接 shared records helpers。

### 5. 相容面保留

保留 app.py／config.py／4_run_backtest.py／5_push_to_line.py、舊 key alias、研究 re-export、Config.V30_PARAMS lazy proxy、StrategyManager／Registry／Runner／adapter、現有 services patch seam。這些有實際 caller／test／公共契約。多份 `_freeze` 的容器語意不同；多份 JSON writers 的 newline、sort_keys、numpy default 與 digest return 不同，不建萬用替代。

## Risks / Trade-offs

- [現有基準失敗] → 另做 LINE patch／SDK correctness 修復；不可用 ignore／xfail／deselect 製造綠色 baseline。
- [凍結 source hash] → 先盤點 consumer，若會漂移則 DEFER，不回填 frozen hash。
- [相似算法有差異] → missing／NaN／負值／小數 parity，Web DataFrame 路徑保持原樣。
- [dirty tree 與 rollback] → 每 Phase 只 stage 本 change hunks，不包含使用者修改，不 reset／checkout。
- [行數收益有限] → 成功指標是重複 owner 減少與契約相同；不把技能 benchmark 當本 repo 實測。

## Migration Plan

Phase 0：保存工作樹 checksum、settings／fixture identity、環境与全綠 characterization；補完刪除與 source-hash 證據。Phase 1：逐候選 file_digest parity、允許時刪私有死碼。Phase 2：shared records 算術與兩 caller output／cache parity。Phase 3：相關測試、隔離 DB／service smoke、diff check、OpenSpec strict validation 與三份 cleanup reports。

每個 Phase 必須有獨立提交、commit ID、驗證與 revert 證據，未通過不進下一 Phase。Rollback 只 revert 該 Phase，不改 DB 紀錄或凍結產物。正式服務不在本回合操作範圍。

## Open Questions

六個目標檔是否是當前 frozen evidence 的 source-hash input，以及 apply 時隔離 DB／container smoke 環境是否可用，均須在 Phase 0 確認。依賴裁減與大型模組拆解留待独立變更。