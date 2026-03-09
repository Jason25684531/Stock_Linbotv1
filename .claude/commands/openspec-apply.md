---
description: 實作已核准的 OpenSpec 變更，逐項完成 tasks.md 並保持勾選狀態同步
---

要實作的 Change ID：

<ChangeId>
$ARGUMENTS
</ChangeId>

**Guardrails**
- 優先選擇最簡單、最小範圍的實作方式，非必要不增加複雜度。
- 每次改動緊扣任務範圍，不順手重構無關的程式碼。
- 遵循 `openspec/AGENTS.md` 的編碼慣例（PEP 8、Type Hints、db_helper、config.py）。

**步驟**（逐一完成，勿跳過）
1. 閱讀 `openspec/changes/<id>/proposal.md` 確認變更目標與範圍。
2. 若存在，閱讀 `openspec/changes/<id>/design.md` 了解架構決策。
3. 閱讀 `openspec/changes/<id>/tasks.md` 確認所有待完成項目。
4. 依照 tasks.md 的 Phase 順序逐項實作，每完成一項立即將 `- [ ]` 改為 `- [x]`。
5. 每個 Phase 完成後執行對應的驗證指令（tasks.md 中的驗收標準）。
6. 全部任務完成後，確認 tasks.md 所有項目均已打勾。

**參考資源**
- `openspec/project.md`：整體架構與策略定義
- `openspec/AGENTS.md`：編碼規範
- `openspec/specs/frontend-design.md`：前端設計規範（若涉及 templates/static）
- `tool/db_helper.py`：資料庫操作工具
- `tool/strategies/base.py`：策略基礎類別
- `app.py`：Flask 主應用程式入口
