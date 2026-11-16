# Stock_Linbotv1 — Strategy Research V4 delivery

RESEARCH_ONLY / NOT_CERTIFIED。工程完成與報酬門檻分開判定；最終任務與驗收狀態以保存報告為準。

- Web：[策略優化研究](http://127.0.0.1:18784/platform/research/v4)
- [最終驗收報告](outputs/strategy_v4/V4_FINAL_ACCEPTANCE_REPORT.md)
- [完整交付索引](outputs/strategy_v4/V4_DELIVERY_INDEX.md)
- [回測修正前後比較](outputs/strategy_v4/STRATEGY_BEFORE_AFTER_COMPARISON.md)
- [OpenSpec 任務](openspec/changes/strategy-research-optimization-v4/tasks.md)
- [封包驗讀與 SHA256](outputs/strategy_v4/acceptance/package_verification.json)

可攜封包位於：`D:/01_Project/Stocke/Stock_Linbotv1_delivery/strategy_v4_2026-11-15_20261115T031944540368Z.zip`；旁邊的 `.zip.sha256` 與封包驗讀 JSON 提供最終雜湊。包含研究結果、原版 Run、快照、來源、OpenSpec、失敗/中斷證據與驗收報告；已逐檔解壓讀回雜湊驗證，沒有覆寫解壓到工作區。

公平比較：MAD 2019–2023；波動動能 2017–2023。MAD CAGR 改善但 MDD 與兩倍成本惡化；波動動能績效惡化。兩策略均未通過 10% 與成本壓力門檻。2024–2025 資料限制、第三策略排序及共享成本計數器後續工作保留於報告，不改原始 V2/V3 證據。

沒有 commit、push、正式部署、正式交易或解除治理。此索引未受既有 outputs/OpenSpec 忽略規則排除；未修改 `.gitignore`。
