from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx

from core.mcp_client import TWSEMCPClient


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


def test_market_statistics_hits_canonical_tool_route() -> None:
    client = TWSEMCPClient(base_url='http://localhost:8000')
    raw_payload = {
        'dataset': 'stock_basic_snapshot',
        'as_of_date': '2026-04-08',
        'market': 'ALL',
        'records': [
            {
                'stock_id': '2330',
                'trade_date': '2026-04-08',
                'open_price': 100.0,
                'high_price': 105.0,
                'low_price': 99.0,
                'close_price': 104.0,
                'volume': 1000000,
            }
        ],
    }

    with patch.object(
        TWSEMCPClient,
        '_post_json',
        new=AsyncMock(return_value=raw_payload),
    ) as mock_post_json:
        result = client.get_market_statistics_sync('2026-04-08')

    assert result is not None
    assert result['dataset'] == 'stock_basic_snapshot'
    assert result['records'][0]['stock_id'] == '2330'
    mock_post_json.assert_awaited_once()
    assert mock_post_json.await_args.kwargs['endpoint'] == '/v1/tools/get_market_statistics'


def test_company_basic_info_hits_canonical_tool_route() -> None:
    client = TWSEMCPClient(base_url='http://localhost:8000')
    raw_payload = {
        'dataset': 'company_basic_info',
        'as_of_date': '2026-04-08',
        'market': 'ALL',
        'record': {
            'stock_id': '2330',
            'stock_name': '台積電',
            'trade_date': '2026-04-08',
            'open_price': 100.0,
            'high_price': 105.0,
            'low_price': 99.0,
            'close_price': 104.0,
            'volume': 1000000,
        },
        'records': [
            {
                'stock_id': '2330',
                'stock_name': '台積電',
                'trade_date': '2026-04-08',
                'open_price': 100.0,
                'high_price': 105.0,
                'low_price': 99.0,
                'close_price': 104.0,
                'volume': 1000000,
            }
        ],
    }

    with patch.object(
        TWSEMCPClient,
        '_post_json',
        new=AsyncMock(return_value=raw_payload),
    ) as mock_post_json:
        result = client.get_company_basic_info_sync(
            stock_id='2330',
            trade_date='2026-04-08',
        )

    assert result is not None
    assert result['dataset'] == 'company_basic_info'
    assert result['record']['stock_id'] == '2330'
    mock_post_json.assert_awaited_once()
    assert mock_post_json.await_args.kwargs['endpoint'] == '/v1/tools/get_company_basic_info'


def test_market_statistics_http_500_returns_none() -> None:
    client = TWSEMCPClient(base_url='http://localhost:8000')
    with patch.object(
        TWSEMCPClient,
        '_post_json',
        new=AsyncMock(
            side_effect=_http_500_error(
                'http://localhost:8000/v1/tools/get_market_statistics'
            )
        ),
    ):
        result = client.get_market_statistics_sync('2026-04-08')

    assert result is None


def test_company_basic_info_http_500_returns_none() -> None:
    client = TWSEMCPClient(base_url='http://localhost:8000')
    with patch.object(
        TWSEMCPClient,
        '_post_json',
        new=AsyncMock(
            side_effect=_http_500_error(
                'http://localhost:8000/v1/tools/get_company_basic_info'
            )
        ),
    ):
        result = client.get_company_basic_info_sync(
            stock_id='2330',
            trade_date='2026-04-08',
        )

    assert result is None


def test_market_statistics_http_504_uses_longer_backoff_and_returns_none() -> None:
    client = TWSEMCPClient(
        base_url='http://localhost:8000',
        max_retries=3,
        backoff_base_seconds=1.0,
        max_backoff_seconds=8.0,
    )
    response = httpx.Response(
        504,
        request=httpx.Request('POST', 'http://localhost:8000/v1/tools/get_market_statistics'),
        json={'message': 'gateway timeout'},
    )

    class FakeAsyncClient:
        async def post(self, endpoint, json):
            return response

        async def aclose(self):
            return None

    with patch.object(
        TWSEMCPClient,
        '_ensure_client',
        new=AsyncMock(return_value=FakeAsyncClient()),
    ), patch('core.mcp_client.asyncio.sleep', new=AsyncMock()) as mock_sleep:
        result = client.get_market_statistics_sync('2026-04-08')

    assert result is None
    assert [call.args[0] for call in mock_sleep.await_args_list] == [2.0, 4.0]