# Quickstart: TWSE MCP Server 路由標準化

This document describes the verification flow for the standardized `/v1/tools/*` MCP routes.

## Prerequisites

- The project virtual environment is available.
- `scripts/twse_mcp_server.py` is runnable on the host or inside Docker.
- `tool/mcp_client.py` has been updated so `TWSEMCPClient` no longer relies on fallback as the normal success path.

## 1. Start the MCP server

```powershell
python scripts/twse_mcp_server.py
```

Expected result:
- The Flask MCP service starts normally.
- `/health` remains reachable.

## 2. Verify the new tool routes directly

```powershell
python -c "import requests, json; payload={'stock_id':'2330','trade_date':'2026-04-08','market':'ALL','correlation_id':'check-company'}; r=requests.post('http://localhost:8080/v1/tools/get_company_basic_info', json=payload, timeout=30); print(r.status_code); print(r.text[:400])"
python -c "import requests, json; payload={'trade_date':'2026-04-08','market':'ALL','include_etfs':True,'correlation_id':'check-market'}; r=requests.post('http://localhost:8080/v1/tools/get_market_statistics', json=payload, timeout=30); print(r.status_code); print(r.text[:400])"
python -c "import requests, json; payload={'trade_date':'2026-04-08','market':'ALL','correlation_id':'check-flow'}; r=requests.post('http://localhost:8080/v1/tools/get_foreign_investment', json=payload, timeout=30); print(r.status_code); print(r.text[:400])"
```

Expected result:
- All three routes return `200` for valid payloads.
- Response payloads use the same `dataset`/`records`/`meta` structure expected by current client normalization.

## 3. Verify validation errors remain consistent

```powershell
python -c "import requests; r=requests.post('http://localhost:8080/v1/tools/get_market_statistics', json={'market':'ALL'}, timeout=30); print(r.status_code); print(r.json())"
```

Expected result:
- The route returns `400`.
- Error payload includes `error_code`, `message`, `retryable`, and `correlation_id`.

## 4. Verify direct client hits without fallback

```powershell
python -c "from tool.mcp_client import TWSEMCPClient; client=TWSEMCPClient(base_url='http://localhost:8080'); payload=client.get_company_basic_info_sync('2330', trade_date='2026-04-08'); print(type(payload).__name__ if payload is not None else None); print(payload.get('dataset') if payload else None)"
python -c "from tool.mcp_client import TWSEMCPClient; client=TWSEMCPClient(base_url='http://localhost:8080'); payload=client.get_market_statistics_sync('2026-04-08'); print(type(payload).__name__ if payload is not None else None); print(payload.get('dataset') if payload else None)"
python -c "from tool.mcp_client import TWSEMCPClient; client=TWSEMCPClient(base_url='http://localhost:8080'); payload=client.get_foreign_investment_sync('2026-04-08'); print(type(payload).__name__ if payload is not None else None); print(payload.get('dataset') if payload else None)"
```

Expected result:
- The client succeeds via the `/v1/tools/*` routes directly.
- No fallback-specific branch is required for the success path.

## 5. Run focused test coverage

```powershell
python -m pytest test/test_mcp_integration.py -q
python -m pytest test/test_richmenu_mcp_server_routes.py -q
python -m pytest test/test_richmenu_mcp_integration.py -q
```

Expected result:
- Client contract tests pass.
- Existing integration tests continue to pass with the standardized route contract.