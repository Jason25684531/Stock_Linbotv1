# 2026-10-14 提案驗證證據

## 環境與限制

Windows；repo-local myenv；Python 3.11.9、pytest 9.0.2、pandas 2.2.3、numpy 1.26.4、line-bot-sdk 3.5.0、pydantic 2.11.10。

RTK 不在 PATH，where.exe 與常見安裝位置亦未找到，採原生命令。OpenSpec 1.3.0 的 new／status／instructions 要求名稱以字母起首，與日期目錄規則衝突：先 scaffold 字母名稱、依 CLI instructions 建立四項 artifacts 並確認全 done，再移至最終日期目錄。移動前確認 source／destination 均在 repo openspec/changes 且 destination 不存在。不改 CLI 或既有 changes。

僅新增本 change 文件；未修改 runtime source／tests／依賴、未執行正式 jobs、訊息、DB 寫入或容器操作。使用者既有 dirty tree 保留；未提交。

## 初步相關 suite

```powershell
.\myenv\Scripts\python.exe -m pytest test/unit/strategy test/unit/runtime/test_strategy_runtime_contracts.py test/unit/recommendation test/test_richmenu_mcp_integration.py test/characterization/test_dashboard_payload_baseline.py test/characterization/test_scheduler_baseline.py test/characterization/test_trade_sequence_regression.py -q -o addopts= --tb=short
```

**125 passed, 1 failed, 1 warning in 52.74s**。覆蓋 strategy contracts／registry／runner／adapter／boundary／extensibility、runtime immutable／recommendation contracts、Rich Menu 和 Dashboard／scheduler／seeded trade sequence characterization。只是初步部分 suite，不是完整 post-C4／運行 baseline。

失敗：`test/test_richmenu_mcp_integration.py::TestPostbackFlow::test_postback_handler_routes_reply_message`。

```text
app/line_bot.py:29 -> ReplyMessageRequest
pydantic.v1.error_wrappers.ValidationError:
messages -> 0: value is not a valid dict
```

test:621-623 patch app.ApiClient／MessagingApi／ReplyMessageRequest，但 line_bot.reply_message 使用直接 imports；只有 configuration 走 services。因此 facade patch 沒攔住 SDK 型別驗證。尚未決定應改測試或 composition，留給另行 correctness 修復。

獨立重跑：

```powershell
.\myenv\Scripts\python.exe -m pytest test/test_richmenu_mcp_integration.py::TestPostbackFlow::test_postback_handler_routes_reply_message -q -o addopts= --tb=short
```

**1 failed in 0.91s**，相同錯誤，不是只在 suite 順序下發生；未 xfail／刪除／排除案例。

Warning：seeded v31 trade fixture 載入舊 XGBoost serialized model 時出現版本相容提示；該 characterization 通過。分類 THIRD_PARTY_DEPRECATION／模型格式 fixture，1 次；本次未升級／改模型／壓制警告。

## 靜態與提案檢查

- Production AST：173 files／44,007 lines；追讀相同 hash／serializer／MCP DTO 與 caller 後縮小候選。
- `_freeze_mapping` hidden repo search 僅見定義；apply 仍須补完完整刪除證據。
- Debt：2 個文字 markers／0 無 trigger；其中 1 個在 docstring。
- git diff --check：PASS，僅既有 CRLF 提示，無 whitespace error。
- 四項 artifact status：proposal／design／specs／tasks done；implementation checkboxes 全未勾選。
- 最終日期目錄：`openspec validate 2026-10-14-simplify-runtime-architecture-safely --strict --no-interactive` PASS。七個文件 UTF-8／whitespace 檢查 PASS；18 個 implementation tasks，完成 0 個。最後 git status 的既有 tracked dirty paths 與起始一致，新增本 change 目錄。

## 尚未通過的實作 gates

清理前仍須處理 LINE 基準失敗、保存 settings／fixture identity、建立完整受影響 Daily／Backtest／caller parity、盤點 frozen source-hash consumer 與刪除證據。正式 DB／容器／full suite 尚未全面驗證。提案完成不等於運行驗收完成。