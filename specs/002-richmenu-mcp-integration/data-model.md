# Data Model: Rich Menu 數據驅動與 MCP 深度整合

**Feature Branch**: `002-richmenu-mcp-integration`
**Date**: 2026-04-02

---

## Entities

### 1. `PostbackCacheEntry`

In-memory record stored by `_PostbackCache`. Lives within the single Flask process; not persisted to DB.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `key` | `str` | Yes | Format: `"{action}:{YYYY-MM-DD}"` (Asia/Taipei date). E.g. `"market_summary:2026-04-02"` |
| `payload` | `Any` | Yes | The upstream response data (dict or structured object) stored verbatim |
| `expires_at` | `float` | Yes | `time.monotonic()` timestamp; entry is stale when `monotonic() > expires_at` |

**Validation rules**:
- `key` must be non-empty.
- `payload` must not be `None`; for MCP responses, `records` list must have `len > 0`.
- `expires_at` is set to `time.monotonic() + TTL_SECONDS` at write time; TTL_SECONDS = 3600.

**State transitions**: MISS → STORE (on first successful fetch) → HIT (within TTL) → MISS (after TTL expiry or date rollover implied by key change).

---

### 2. `MarketSummaryPayload`

Aggregated snapshot derived from `stock_basic_snapshot` MCP response. Not persisted.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `trade_date` | `str` | Yes | Top-level `as_of_date` from MCP response |
| `total_volume` | `int` | Yes | `sum(record["volume"] for record in records)` |
| `rising_count` | `int` | Yes | `count(r for r in records if r["close_price"] > r["open_price"])` |
| `falling_count` | `int` | Yes | `count(r for r in records if r["close_price"] < r["open_price"])` |
| `flat_count` | `int` | Yes | `count(r for r in records if r["close_price"] == r["open_price"])` |
| `stock_count` | `int` | Yes | `len(records)` |
| `fetched_at` | `str` | Yes | ISO timestamp at aggregation time |

**Derivation note**: `rising_count` / `falling_count` use open-to-close comparison (same-day). Message to users is labelled "今日開收盤漲跌概況" to clearly indicate the derivation basis.

---

### 3. `ChipTrendPayload`

Aggregated fund-flow totals derived from `foreign_investor_flow` MCP response. Not persisted.

| Field | Type | Required | Source |
|-------|------|----------|--------|
| `trade_date` | `str` | Yes | Top-level `as_of_date` from MCP response |
| `foreign_net_buy` | `int` | Yes | `sum(r["foreign_buy"] for r in records)` (signed) |
| `trust_net_buy` | `int` | No | `sum(r.get("trust_buy", 0) for r in records)` — `None` if all records missing field |
| `dealer_net_buy` | `int` | No | `sum(r.get("dealer_buy", 0) for r in records)` — `None` if all records missing field |
| `has_trust` | `bool` | Yes | `True` if any record had a non-null `trust_buy` |
| `has_dealer` | `bool` | Yes | `True` if any record had a non-null `dealer_buy` |
| `fetched_at` | `str` | Yes | ISO timestamp at aggregation time |

---

### 4. `RichMenuAreaDefinition`

Defines a single cell in the 2×2 Rich Menu grid. Passed to `linebot.v3.messaging.RichMenuArea`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `label` | `str` | Yes | Button display text visible in LINE |
| `action_type` | `Literal["message", "postback"]` | Yes | Determines which LINE Action class is used |
| `trigger` | `str` | Yes | For `message`: the text sent; for `postback`: the `data` string |
| `display_text` | `str` | No | Postback only — the text shown in chat bubble |
| `x`, `y`, `width`, `height` | `int` | Yes | Pixel coordinates within 2500×1686 canvas |

**2×2 grid layout** (each cell 1250×843 px):

| Position | label | action_type | trigger | display_text |
|----------|-------|-------------|---------|--------------|
| Top-left (0,0) | 個股診斷 | message | `診斷 ` | — |
| Top-right (1250,0) | 總經與大盤 | postback | `action=market_summary` | `總經與大盤` |
| Bottom-left (0,843) | 籌碼動向 | postback | `action=chip_trend` | `籌碼動向` |
| Bottom-right (1250,843) | 策略盲盒 | postback | `action=random_strategy` | `策略盲盒` |

---

### 5. `StrategyBlindBoxResult`

Returned by `_build_random_strategy_messages()` before formatting into LINE messages.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `strategy_key` | `str` | Yes | E.g. `"v36_chip_momentum"` |
| `strategy_name` | `str` | Yes | Human-readable display name from `strategy.display_name` |
| `candidates` | `pd.DataFrame` | Yes | Output of `strategy.filter_candidates(market_df)` |
| `executed_at` | `str` | Yes | ISO timestamp |

**Validation rules**:
- If `candidates` is empty, message indicates "今日無推薦標的" with `strategy_name` label.
- If all strategies in pool return empty candidates, a single final message is returned.

---

## Relationships

```
_PostbackCache
    └── keyed by: action + trade_date
    └── stores: MarketSummaryPayload | ChipTrendPayload

app.py: _build_market_summary_messages()
    └── reads from: _PostbackCache
    └── fetches via: MCPClient.fetch_stock_basic_snapshot_sync()
    └── produces: MarketSummaryPayload

app.py: _build_chip_trend_messages()
    └── reads from: _PostbackCache
    └── fetches via: MCPClient.fetch_foreign_investor_flow_sync()
    └── produces: ChipTrendPayload

app.py: _build_random_strategy_messages()
    └── reads pool from: StrategyManager.get_random_strategy_pool()
    └── loads strategy via: StrategyManager.get_strategy(key)
    └── produces: StrategyBlindBoxResult

tool/richmenu.py: build_default_rich_menu_request()
    └── produces: RichMenuRequest [ 4 × RichMenuAreaDefinition ]

StrategyManager.get_random_strategy_pool()
    └── reads: strategy_settings.json["random_strategy_pool"]
    └── validates against: STRATEGY_REGISTRY.keys()
```

---

## Schema Changes

### `strategy_settings.json`

New optional key added:

```json
{
  "random_strategy_pool": [
    "v35_innovation",
    "v36_chip_momentum",
    "v38_value_dividend"
  ]
}
```

If key is absent, `StrategyManager.get_random_strategy_pool()` returns the default list above. No migration of existing records required; `_load_settings()` uses `dict.get()` with default.

### No database schema changes

This feature introduces no new tables and modifies no existing `tool/db_helper.py` functions.
