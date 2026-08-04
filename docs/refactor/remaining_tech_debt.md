# Remaining technical debt

- The extracted runner retains the characterized algorithm while future work can replace its internal transaction loop with smaller collaborators.
- Deprecated strategy IDs and root CLI wrappers are retained for compatibility; remove only in a major-version migration after downstream consumers migrate.

## TD-01 執行測試會覆寫正式的 `strategy_settings.json`

> 發現日期：2026-07-30
> 嚴重性：**高**——「跑一次測試」即改變正式啟用策略，且無任何提示

### 症狀

執行 `python -m pytest test/ -q` 後，repo 根目錄的 `strategy_settings.json` 被改寫：

```diff
- "last_updated": "2026-07-24T16:33:38.206782"      - "quality_value_low_volatility"
+ "last_updated": "2026-07-29T09:54:32.006263"      + "mean_reversion"
```

### 根因

| 位置 | 事實 |
|---|---|
| `test/test_v37_v38_strategies.py:22`、`:161` | 呼叫 `manager.set_active_strategy('v37_mean_reversion')`，未使用任何隔離的設定檔 |
| `core/strategy_manager.py:54` | `SETTINGS_FILE = Path(__file__).resolve().parents[1] / 'strategy_settings.json'`——**指向 repo 根目錄的絕對路徑**，非測試副本 |
| `core/strategy_manager.py:395` | `set_active_strategy()` 直接呼叫 `_save_settings()` 落檔 |

`v37_mean_reversion` 經 alias 解析為 canonical 的 `mean_reversion`，即觀察到的殘留值。

### 為何這是缺陷而非可接受行為

`test/conftest.py:26-46` 已存在一個 autouse fixture `_forbid_real_backtest_persistence`，專門攔截真實的 `save_backtest_results()`。其 docstring 記載：

> 2026-07-24: 一次互動式驗證意外觸發真實回測落庫，整表覆寫了 `backtest_trades`/`backtest_equity_curve`。

**同一類「測試污染正式狀態」的缺陷在 `strategy_settings.json` 上完全沒有防護。** 已有前例證明此類問題會造成實際損失，但防護只做了回測落庫這一半。

### 建議修法（擇一）

1. **比照既有防護**：在 `test/conftest.py` 增加 autouse fixture 攔截 `StrategyManager._save_settings`，需要真實落檔語意的測試以 marker 明確 opt-in（比照 `allow_real_backtest_persistence`）。
2. **注入路徑**：`StrategyManager.__init__` 已支援 `settings_path`（`core/strategy_manager.py:128`）。在 conftest 統一注入 `tmp_path`，使測試預設不碰正式檔。

方案 2 較根本，方案 1 較貼近既有慣例。**兩者皆屬測試基礎設施，應為獨立 change，不併入功能性變更。**

### 影響範圍

`test/test_v37_v38_strategies.py` 為已知案例。修法前應先掃描全部呼叫 `set_active_strategy`／`update_setting`／`_save_settings` 的測試，確認完整清單。

---

## TD-02 資料層的三項既有問題（詳見 OpenSpec change）

2026-07-30 的資料來源查證另外發現三項問題，完整證據與建議記於
`openspec/changes/2026-07-30-factor-research-mvp/design.md`（§1 現況資料流、§17 被拒絕方案 A7）。
**注意：`openspec/` 已被 `.gitignore:63` 排除，該文件不在版控中**，故在此保留摘要：

| # | 問題 | 證據 |
|---|---|---|
| TD-02a | **官方成交金額被丟棄**。`MI_INDEX` 回應含「成交金額」「成交筆數」，但 rename/select 只保留 7 欄 | `jobs/update_database.py:139-155`、`services/mcp/server.py:930-940` |
| TD-02b | **`filter_common_stock_universe` 行為與 docstring 相反**。docstring 宣稱「排除權證、ETF」，但正則 `^\d{4}$` 且僅排除 `03`/`08` 開頭 → `0050`、`0056` 等 4 碼 ETF **實際被保留**，只有 5–6 碼 ETF 被排除 | `core/db_helper.py:33-35, 766-780` |
| TD-02c | **三套互相矛盾的股票池規則**：寫入端 `^([1-9]\d{3}\|00)`（含全部 ETF）、策略端同上、`db_helper` 如 TD-02b | `jobs/update_database.py:620`、`core/strategies/base.py:271`、`core/db_helper.py:772` |

TD-02b 修改會影響 OpenSpec capability `common-stock-universe-filter` 與其既有測試，需獨立 change 處理。

## Rollback

This worktree intentionally has no per-phase commits. Before merging, commit each
phase separately in task order. Roll back in reverse order with `git revert <phase
commit>`. Never rewrite historical strategy keys or generated baseline fixtures during rollback.
