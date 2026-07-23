# Strategy Inventory

> 證據來源：`core/strategies/v*.py`，行號以 2026-07-22 工作樹為準。此文件只描述事實，命名決策留待 Phase 6。

| key | 選股／排序因子 | 出場參數 | 必要資料欄位 | 證據 |
|---|---|---|---|---|
| `v31_hybrid` | 收盤 > MA20 > MA60、量能、RSI 40–70；可選 KD／布林；ML 特徵含籌碼比率 | 停損 7%、停利 15%、最長 10 日 | `close_price, ma20, ma60, volume, rsi` | `v31_hybrid.py:125,151-158,73-85` |
| `v33_low_vol` | 趨勢、量能、NATR、RSI、MACD、BIAS；依 `std_20`／NATR 低者排序 | 停損 6%、停利 12%、最長 12 日 | `close_price, ma60, volume`；其餘可選 | `v33_low_vol.py:124-125,165-230,82-94` |
| `v34_turbo` | 營收年增、接近 60 日高、量能；依 `revenue_yoy` 降冪 | 停損 10%、停利 20%、最長 7 日 | `close_price, high_price, ma20, volume`；`revenue_yoy` 可選 | `v34_turbo.py:104-125,178-263,82-94` |
| `v35_innovation` | 營業利益率、營收年增、EPS、收盤 > MA60、量比 | 停損 10%、停利 20%、最長 60 日 | `op_profit_margin, revenue_yoy, eps, close_price, ma60, volume_ratio` | `v35_innovation.py:127-169,209-220,92-104` |
| `v36_chip_momentum` | 外資／投信連買或籌碼分數、趨勢、量能；依 `chip_score`／外資連買排序 | 停損 8%、停利 15%、最長 12 日 | `close_price, ma60, volume`；籌碼欄位可選 | `v36_chip_momentum.py:51-63,113-168,208-210,79-91` |
| `v37_mean_reversion` | 收盤 > MA60、KD 低值排序，並含 RSI／MACD／ATR | 停損 7%、停利 12%、最長 10 日 | `close_price, ma60, volume` | `v37_mean_reversion.py:113-138,194,76-88` |
| `v38_value_dividend` | 趨勢、低波動、營業利益率、EPS；依營益率或 NATR 排序 | 停損 8%、停利 15%、最長 20 日 | `close_price, ma60, volume`；`op_profit_margin, eps` 可選 | `v38_value_dividend.py:125-165,210-212,87-99` |

## 稽核結論

- V35 的程式碼直接使用營業利益率、營收年增與 EPS；「品質／成長」描述有證據。
- V38 在 `core/`、`app/`、`jobs/` 與 `strategy_settings.json` 搜尋 `dividend_yield`／`yield` 未找到殖利率因子；不得以「高殖利率」作為事實宣稱。
- V37 在 `v37_mean_reversion.py:138` 有 `close_price > ma60` 條件。因此原 design 的「無長期趨勢過濾證據」敘述不正確；但暫定名稱 `mean_reversion` 並未宣稱存在 Trend-Filtered 條件，無需改變策略行為。
