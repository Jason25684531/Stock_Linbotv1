from __future__ import annotations

from unittest.mock import patch

import httpx

from tool.mcp_client import TWSEMCPClient


def _http_500_error(url: str) -> httpx.HTTPStatusError:
    request = httpx.Request('POST', url)
    response = httpx.Response(
        500,
        request=request,
        json={'message': 'internal server error'},
    )
    return httpx.HTTPStatusError(
        'Internal Server Error',
        request=request,
        response=response,
    )


def test_market_statistics_http_500_returns_none() -> None:
    client = TWSEMCPClient(base_url='http://localhost:8000')
    with patch.object(
        TWSEMCPClient,
        '_request_compat_payload',
        side_effect=_http_500_error(
            'http://localhost:8000/v1/tools/get_market_statistics'
        ),
    ):
        result = client.get_market_statistics_sync('2026-04-08')

    assert result is None


def test_company_basic_info_http_500_returns_none() -> None:
    client = TWSEMCPClient(base_url='http://localhost:8000')
    with patch.object(
        TWSEMCPClient,
        '_request_compat_payload',
        side_effect=_http_500_error(
            'http://localhost:8000/v1/tools/get_company_basic_info'
        ),
    ):
        result = client.get_company_basic_info_sync(
            stock_id='2330',
            trade_date='2026-04-08',
        )

    assert result is None