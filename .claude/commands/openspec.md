---
description: 套用 OpenSpec 工作流程：自動提案、確認後實作。用法：/openspec <需求描述>
---

使用者需求：

<UserRequest>
$ARGUMENTS
</UserRequest>

**完整流程（Proposal → 確認 → Apply）**

## 階段一：提案 (Proposal)

1. 閱讀 `openspec/project.md` 與 `openspec/AGENTS.md` 了解架構與慣例。
2. 查看 `openspec/changes/0212_Finish/tasklist.md` 作為 tasks.md 格式參考。
3. 探索相關程式碼，確保提案符合現有實作。
4. 選擇動詞開頭的 change-id（如 `add-v39-strategy`、`fix-revenue-crawler`）。
5. 建立以下文件（**此階段不寫程式碼**）：
   - `openspec/changes/<id>/proposal.md`（背景 / 目標 / 影響的模組）
   - `openspec/changes/<id>/tasks.md`（分 Phase、可勾選的任務清單，含驗收指令）
6. 展示提案內容給使用者確認，**等待使用者同意後再進入實作**。

## 階段二：實作 (Apply)

使用者確認後：

1. 依照 `tasks.md` 的 Phase 順序逐項實作。
2. 每完成一項，立即將 `- [ ]` 改為 `- [x]`。
3. 遵循 `openspec/AGENTS.md` 的編碼慣例：
   - Python: PEP 8 + Type Hints
   - 資料庫操作只透過 `tool/db_helper.py`
   - 常數集中在 `config.py`
   - 前端修改需參照 `openspec/specs/frontend-design.md`
4. 全部任務完成後，確認 `tasks.md` 所有項目均已打勾，回報完成。

**注意**
- 需求模糊時，先提問再建立提案。
- 每次改動最小化，不超出任務範圍。
