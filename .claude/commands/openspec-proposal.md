---
description: 建立 OpenSpec 變更提案（proposal.md + tasks.md），提案階段不寫程式碼
---

使用者需求如下：

<UserRequest>
$ARGUMENTS
</UserRequest>

**Guardrails**
- 優先選擇最簡單、最小範圍的實作方式，非必要不增加複雜度。
- 變更範圍緊扣使用者請求，不超出需求。
- 提案階段**不寫任何程式碼**，只建立設計文件（proposal.md、tasks.md、design.md）。
- 若需求模糊，先提問釐清再建立文件。

**步驟**
1. 閱讀 `openspec/project.md` 了解整體專案目標與架構。
2. 閱讀 `openspec/AGENTS.md` 確認編碼慣例與前端規範。
3. 查看 `openspec/changes/` 目錄，以最近的 tasklist 為格式參考（如 `openspec/changes/v35-refactor-and-flex/tasklist.md`）。
4. 探索相關程式碼（`tool/strategies/`、`app.py`、`tool/db_helper.py` 等）確保提案符合現有實作。
5. 選擇一個**動詞開頭**的唯一 change-id（如 `add-v39-strategy`、`fix-revenue-crawler`）。
6. 建立 `openspec/changes/<id>/proposal.md`，包含：
   - **背景 (Context)**：為何需要此變更
   - **目標 (Objectives)**：預期達成的效果
   - **架構影響 (Architecture)**：影響哪些模組/檔案
7. 建立 `openspec/changes/<id>/tasks.md`，格式為分 Phase 的可勾選任務清單，每個任務需可獨立驗證。
8. 若變更涉及多系統或需要架構決策討論，另建 `openspec/changes/<id>/design.md`。

**注意事項**
- 前端（`templates/`、`static/`）的任何修改需參照 `openspec/specs/frontend-design.md`。
- 資料庫操作一律透過 `tool/db_helper.py`，不在 `app.py` 寫原始 SQL。
- 所有常數（手續費、稅率、滑價）集中在 `config.py`。
- tasks.md 的驗證項目需包含：執行指令（如 `python 4_run_backtest.py`）或具體驗收標準。
