---
description: 歸檔已完成的 OpenSpec 變更。用法：/openspec-archive <change-id>
---

要歸檔的 Change ID：

<ChangeId>
$ARGUMENTS
</ChangeId>

**步驟**
1. 若未提供 Change ID，列出 `openspec/changes/` 下的子目錄請使用者確認。
2. 確認 `openspec/changes/<id>/tasks.md` 所有項目均已標記 `- [x]`，若有未完成項目先停止回報。
3. 確認 `openspec/changes/archive/` 目錄存在，若不存在則建立。
4. 將 `openspec/changes/<id>/` 整個目錄移至 `openspec/changes/archive/<id>/`。
5. 回報歸檔完成。
