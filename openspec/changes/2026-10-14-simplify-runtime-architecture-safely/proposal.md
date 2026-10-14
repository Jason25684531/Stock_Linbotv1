# 2026-10-14 系統架構邏輯清理提案

## Why

目前架構已完成策略平台與 runtime 隔離，但 Web／LINE 市場統計有重複計算，研究／shadow 證據雜湊有多份相同實作，另有無呼叫的私有 helper。先消除有證據的局部重複，再評估責任集中的模組，可以降低維護成本並維持現行服務契約。

## What Changes

- 先記錄目前工作樹、離線 characterization baseline、跨入口契約與刪除證據；保留所有既有未提交修改。
- 將六個完全相同或僅差 OSError 行為的串流 SHA-256 實作改用 Python 3.11 `hashlib.file_digest`；保留各自函式名稱、例外／None 行為與輸出。**不新增共用雜湊框架。**
- 僅在補齊 `docs/refactor/deletion_candidates.md` 的完整無引用證據後，移除 `core/strategy/spec.py::_freeze_mapping`；不移除 `_freeze` 或不可變驗證。
- 將 Dashboard／LINE 的兩組相同市場與法人 records 統計集中為少量純函式。取資料、日期解析、cache key／TTL、訊息格式、API schema 和錯誤分支仍由原呼叫者負責。
- 建立 KEEP／DEFER／UNKNOWN 清單。DB helper、Daily、Dashboard 的大型拆檔、依賴裁減與泛用框架均不納入本次實作。
- 每個 Phase 提供獨立提交、可 revert 證據與驗證結果；相關契約未通過時不進入下一 Phase。

## Capabilities

### New Capabilities

- `runtime-architecture-simplification`: 定義這次局部清理的範圍、證據與等價驗收門檻。

### Modified Capabilities

無。現有公開功能、相容性、策略與交易語意不變；本 change 增加清理驗收契約，不改既有產品需求。

## Impact

預定影響 `jobs/run_factor_validation.py`、`core/research/fundamental_factor_validation.py`、`core/runtime/fresh_oos_promotion.py`、`core/runtime/fundamental_advancement.py`、`core/research/universe_recovery.py`、`core/research/fundamentals/pit.py`、`core/strategy/spec.py`、`app/dashboard_payloads.py`、`app/line_flows.py`，以及對應測試與清理證據文件。不新增依賴、不刪依賴，不重算研究產物，不連正式 DB 或發送 LINE 訊息。

本回合只建立提案；完整稽核見 `audit.md`，提案驗證見 `verification.md`。實作時須重新建立 baseline，這份提案不代表線上服務已被全面驗證。
