# Interface Contracts: Rich Menu Postback Actions

**Feature Branch**: `002-richmenu-mcp-integration`
**Date**: 2026-04-02

This document defines the interface contracts for the LINE Webhook Postback system and the Rich Menu action mappings. These contracts govern what the `app.py` `PostbackEvent` handler accepts and what it returns.

---

## 1. Rich Menu Area → LINE Action Contract

The 2×2 Rich Menu is defined in `tool/richmenu.py: build_default_rich_menu_request()`.

```json
{
  "size": { "width": 2500, "height": 1686 },
  "selected": true,
  "name": "Stocke Rich Menu",
  "chatBarText": "開啟選單",
  "areas": [
    {
      "bounds": { "x": 0, "y": 0, "width": 1250, "height": 843 },
      "action": {
        "type": "message",
        "label": "個股診斷",
        "text": "診斷 "
      }
    },
    {
      "bounds": { "x": 1250, "y": 0, "width": 1250, "height": 843 },
      "action": {
        "type": "postback",
        "label": "總經與大盤",
        "data": "action=market_summary",
        "displayText": "總經與大盤"
      }
    },
    {
      "bounds": { "x": 0, "y": 843, "width": 1250, "height": 843 },
      "action": {
        "type": "postback",
        "label": "籌碼動向",
        "data": "action=chip_trend",
        "displayText": "籌碼動向"
      }
    },
    {
      "bounds": { "x": 1250, "y": 843, "width": 1250, "height": 843 },
      "action": {
        "type": "postback",
        "label": "策略盲盒",
        "data": "action=random_strategy",
        "displayText": "策略盲盒"
      }
    }
  ]
}
```

---

## 2. Postback Dispatch Map Contract

Defined in `app.py: _POSTBACK_HANDLERS`. Each entry maps an action key to a zero-argument callable returning `list[linebot.v3.messaging.Message]`.

| Action Key | Handler Function | Data Source | Cacheable |
|------------|-----------------|-------------|-----------|
| `get_macro_news` | `_build_macro_news_messages` | `tool.news_agent` | No (existing) |
| `get_journal` | `lambda: [V3TextMessage(...)]` | DB via `tool.db_helper` | No (existing) |
| `market_summary` | `_build_market_summary_messages` | MCPClient `stock_basic_snapshot` | Yes — TTL 1h |
| `chip_trend` | `_build_chip_trend_messages` | MCPClient `foreign_investor_flow` | Yes — TTL 1h |
| `random_strategy` | `_build_random_strategy_messages` | `StrategyManager` | No |

**Dispatch signature** (all handlers must conform):
```python
def handler() -> list[linebot.v3.messaging.Message]:
    ...
```

**Fallback**: Any unrecognised action key returns `[V3TextMessage(text="⚠️ 尚未支援的 Rich Menu 指令")]`.

---

## 3. `market_summary` Response Shape

The LINE message returned for `action=market_summary` must contain the following fields in readable text or as Flex Message:

```
📊 今日大盤概況（{trade_date}）

🔼 今日開收盤上漲：{rising_count} 檔
🔽 今日開收盤下跌：{falling_count} 檔
➡️ 平盤：{flat_count} 檔
💹 成交量合計：{total_volume_formatted}

⚡ 資料來源：TWSE MCP | 更新：{fetched_at}
```

**Constraints**:
- `total_volume_formatted` uses 億 (hundred-million) if > 1e8, else 萬 (ten-thousand).
- On upstream failure: `"📊 大盤資料暫時無法取得，請稍後再試。"`.
- On empty records: `"📊 今日大盤無資料，可能為休市日。"`.

---

## 4. `chip_trend` Response Shape

```
💰 三大法人籌碼動向（{trade_date}）

🌏 外資：{foreign_net_buy_formatted}（淨{買超/賣超}）
{if has_trust}🏦 投信：{trust_net_buy_formatted}（淨{買超/賣超}）
{if has_dealer}🏢 自營商：{dealer_net_buy_formatted}（淨{買超/賣超}）

⚡ 資料來源：TWSE MCP | 更新：{fetched_at}
```

**Constraints**:
- Format: 億元 if |value| > 1e8, else 萬元.
- Positive=買超, Negative=賣超.
- On upstream failure: `"💰 籌碼資料暫時無法取得，請稍後再試。"`.

---

## 5. `random_strategy` Response Shape

```
🎲 策略盲盒 — 今日抽到：{strategy_name}

{candidates formatted as stock list}

📅 {executed_at} | 策略：{strategy_key}
```

**On empty candidates** (all strategies exhausted):
```
🎲 策略盲盒 — {strategy_name}

今日此策略無符合條件標的。
```

**On strategy engine error**:
```
🎲 策略盲盒執行失敗，請稍後再試。
```

---

## 6. TTL Cache Key Contract

```
cache_key = f"{action}:{today_taipei}"

# Examples
"market_summary:2026-04-02"
"chip_trend:2026-04-02"
```

- `today_taipei` is derived via `ZoneInfo('Asia/Taipei')` to ensure consistent daily boundaries.
- Keys from prior dates are never overwritten; they simply remain dormant until evicted (no active eviction — entries expire on next read attempt).
- `random_strategy` is explicitly excluded from caching; its key is never written.

---

## 7. Strategy Pool Settings Contract

`strategy_settings.json` schema extension:

```json
{
  "random_strategy_pool": ["v35_innovation", "v36_chip_momentum", "v38_value_dividend"]
}
```

**Validation**:
- Entries not present in `StrategyManager.STRATEGY_REGISTRY` are silently skipped with a warning log.
- If resulting pool is empty after validation, `_build_random_strategy_messages()` returns the "目前無已設定策略" message.
- Default value (if key absent): `["v35_innovation", "v36_chip_momentum", "v38_value_dividend"]`.
