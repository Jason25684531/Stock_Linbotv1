# Quickstart: Rich Menu 數據驅動與 MCP 深度整合

**Feature Branch**: `002-richmenu-mcp-integration`

---

## Prerequisites

- MCP Server is running: `python scripts/twse_mcp_server.py` (local port 8080)
- LINE Channel Access Token and Secret are set in `.env`
- `myenv` virtual environment is activated

---

## Deploy Updated Rich Menu to LINE

After implementation is complete, push the new 4-button layout to LINE:

```powershell
# Option 1: Via environment variable at app startup
$env:LINE_RICH_MENU_AUTO_SYNC = "1"
python app.py

# Option 2: Standalone deployment script
python scripts/setup_rich_menu.py
```

---

## Test Postback Actions (Manual)

Use the LINE Developer console or a test harness to send PostbackEvents:

| Button | Postback Data | Expected Reply |
|--------|--------------|----------------|
| 總經與大盤 | `action=market_summary` | 大盤成交量 + 漲跌家數 |
| 籌碼動向 | `action=chip_trend` | 三大法人淨買超摘要 |
| 策略盲盒 | `action=random_strategy` | 隨機策略精選標的 |
| 個股診斷 | (MessageEvent: `診斷 `) | 診斷報告 |

---

## Test Cache Behaviour

```powershell
# Run app with debug logging
$env:LOG_LEVEL = "DEBUG"
python app.py

# Trigger market_summary twice within 1 hour
# → Second trigger must NOT produce "MCP request started" in logs
```

---

## Run Tests

```powershell
# Unit + integration tests for this feature
pytest test/test_richmenu_mcp_integration.py -v

# Regression: existing postback handlers still work
pytest test/ -k "postback or richmenu" -v
```

---

## Configure Strategy Pool

Edit `strategy_settings.json` to change which strategies appear in the blind box:

```json
{
  "random_strategy_pool": ["v35_innovation", "v36_chip_momentum", "v38_value_dividend"]
}
```

Changes take effect on next postback trigger; no restart required.

---

## Key Files Modified

| File | Change |
|------|--------|
| `tool/richmenu.py` | Updated `build_default_rich_menu_request()` — 4-button layout |
| `app.py` | Added `_PostbackCache`, 3 new handler functions, dict-dispatch map |
| `tool/strategy_manager.py` | Added `get_random_strategy_pool()` method + `random_strategy_pool` default |
| `strategy_settings.json` | New `random_strategy_pool` key (auto-added on first run) |
| `scripts/setup_rich_menu.py` | New deployment helper script |
| `test/test_richmenu_mcp_integration.py` | New test file |
