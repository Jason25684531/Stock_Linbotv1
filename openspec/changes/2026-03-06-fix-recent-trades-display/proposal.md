# 修復近期交易表格顯示不完整問題

**Change ID**: `fix-recent-trades-display`
**建立日期**: 2026-03-06

---

## 背景 (Context)

Dashboard 首頁的「📋 近期交易 Recent Trades」表格存在顯示不完整的問題——部分列無法顯示，或整個表格在篩選後變為空白。

### 已確認的根本原因（依嚴重度排序）

#### 🔴 原因一：JavaScript TypeError 導致渲染中斷（最主要）
**位置**：`templates/dashboard.html` 第 375–376 行

```javascript
x-text="trade.buy_price.toFixed(2)"   // 若 buy_price 為 null → TypeError
x-text="trade.sell_price.toFixed(2)"  // 若 sell_price 為 null → TypeError
```

當資料庫中 `buy_price` 或 `sell_price` 為 `null`（`safe_float()` 回傳 `None` 序列化成 JSON `null`），在 Alpine.js `x-for` 迴圈中呼叫 `null.toFixed(2)` 拋出 `TypeError`，導致**該列及後續列全部停止渲染**，造成表格截斷。

#### 🟡 原因二：CSV fallback 缺少 `strategy` 欄位
**位置**：`app.py` `/api/trades` 路由（第 709–734 行）

當 DB 查詢結果為空（`get_recent_backtest_trades()` 回傳 `[]`），API 改讀 `ML_Data/backtest_result.csv`。舊版或其他策略產生的 CSV 不一定包含 `strategy` 欄位：
- `trade.strategy` 為 `undefined`
- 策略篩選下拉選單選非「全部」時，`t.strategy === strategyFilter` 恆為 `false` → 顯示 0 筆
- 策略標籤全部顯示「未知」

#### 🟡 原因三：CSV fallback 的 NaN 序列化問題
**位置**：`app.py` 第 732 行

```python
trades = df_recent.to_dict('records')  # 空值欄位為 NaN（非 JSON 合法值）
return jsonify(trades)                 # 可能引發 ValueError 或回傳 null
```

pandas `to_dict('records')` 對空欄位產生 `float('nan')`，前端收到 `null` 後再被 `toFixed(2)` 呼叫即觸發原因一。

#### 🟠 原因四：`save_backtest_results` 每次執行回測時清空全部歷史資料
**位置**：`tool/db_helper.py` 第 629 行

```python
conn.execute(text("DELETE FROM backtest_trades"))
```

每次執行任一策略的回測均刪除所有歷史交易記錄，導致 DB 中只保有最近一次回測的策略資料；其他策略篩選後顯示 0 筆。

---

## 目標 (Objectives)

1. **修復 JS null 安全問題**：表格所有列均能正常渲染，不因 null 值中斷。
2. **修復 CSV fallback**：回傳前填補缺失的 `strategy` 欄位，並清除 NaN。
3. **保留多策略交易歷史**：DB 改為累積模式（或分策略清空），確保策略篩選有效。

---

## 架構影響 (Architecture)

| 檔案 | 修改範圍 | 說明 |
|------|---------|------|
| `templates/dashboard.html` | 第 375–376 行 | `buy_price` / `sell_price` null 安全寫法 |
| `app.py` | `/api/trades` 路由（~730 行） | CSV fallback 補 `strategy` 欄位、清除 NaN |
| `tool/db_helper.py` | `save_backtest_results()` (~629 行) | 改為按 strategy 清空而非全表刪除 |

> 所有修改均在現有檔案內小幅調整，不新增模組，不更動 DB schema。
