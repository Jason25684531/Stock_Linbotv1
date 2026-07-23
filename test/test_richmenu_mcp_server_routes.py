from __future__ import annotations

import logging
from unittest.mock import patch

import pandas as pd
import pytest

from scripts import twse_mcp_server


@pytest.fixture()
def client():
    twse_mcp_server.app.config['TESTING'] = True
    with twse_mcp_server.app.test_client() as test_client:
        yield test_client


def _market_payload() -> dict[str, object]:
    return {
        'trade_date': '2026-04-08',
        'market': 'ALL',
        'include_etfs': True,
        'correlation_id': 'cid-market',
    }


def _company_payload() -> dict[str, object]:
    payload = _market_payload()
    payload['stock_id'] = '2330'
    payload['correlation_id'] = 'cid-company'
    return payload


def _flow_payload() -> dict[str, object]:
    return {
        'trade_date': '2026-04-08',
        'market': 'ALL',
        'correlation_id': 'cid-flow',
    }


def _snapshot_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'trade_date': '2026-04-08',
                'open_price': 100.0,
                'high_price': 105.0,
                'low_price': 99.0,
                'close_price': 104.0,
                'volume': 1000000.0,
                'pe_ratio': 20.5,
                'stock_name': '台積電',
                'security_type': 'stock',
            },
            {
                'stock_id': '0050',
                'trade_date': '2026-04-08',
                'open_price': 150.0,
                'high_price': 151.0,
                'low_price': 149.0,
                'close_price': 150.5,
                'volume': 500000.0,
                'pe_ratio': 0.0,
                'stock_name': '元大台灣50',
                'security_type': 'etf',
            },
        ]
    )


def _flow_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'trade_date': '2026-04-08',
                'foreign_buy': 1000.0,
                'trust_buy': 200.0,
                'dealer_buy': -50.0,
            }
        ]
    )


def _history_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                'trade_date': '2026-04-07',
                'stock_id': '2330',
                'open_price': 820.0,
                'high_price': 835.0,
                'low_price': 818.0,
                'close_price': 832.0,
                'volume': 24500000,
                'ma5': 824.0,
                'ma20': 798.0,
                'ma60': 760.0,
            },
            {
                'trade_date': '2026-04-08',
                'stock_id': '2330',
                'open_price': 833.0,
                'high_price': 842.0,
                'low_price': 830.0,
                'close_price': 840.0,
                'volume': 26500000,
                'ma5': 829.0,
                'ma20': 801.0,
                'ma60': 763.0,
            },
        ]
    )


def _report_payload() -> dict[str, object]:
    return {
        'stock_id': '2330',
        'trade_date': '2026-04-08',
        'close_price': 840.0,
        'open_price': 833.0,
        'high_price': 842.0,
        'low_price': 830.0,
        'ma5': 829.0,
        'ma20': 801.0,
        'ma60': 763.0,
        'rsi': 63.5,
        'bias': 2.8,
        'chip_score': 0.74,
        'ai_score': 0.82,
        'strategy_name': 'v34_turbo',
        'revenue_yoy': 21.6,
        'op_margin': 0.42,
        'foreign_buy': 1000,
        'trust_buy': 200,
        'dealer_buy': -50,
    }


def _assert_error_envelope(
    body: dict[str, object],
    *,
    error_code: str,
    retryable: bool,
    correlation_id: str,
) -> None:
    assert body['error_code'] == error_code
    assert body['retryable'] is retryable
    assert body['correlation_id'] == correlation_id
    assert 'message' in body
    assert 'details' in body


def _assert_success_parity(left: dict[str, object], right: dict[str, object]) -> None:
    assert left['dataset'] == right['dataset']
    assert left['as_of_date'] == right['as_of_date']
    assert left['market'] == right['market']
    assert left['records'] == right['records']
    assert left['meta'] == right['meta']


def test_get_market_statistics_tool_route_returns_200(client) -> None:
    with patch.object(twse_mcp_server, 'fetch_stock_basic_snapshot', return_value=_snapshot_frame()):
        response = client.post('/v1/tools/get_market_statistics', json=_market_payload())

    assert response.status_code == 200
    body = response.get_json()
    assert body['dataset'] == 'stock_basic_snapshot'
    assert len(body['records']) == 2
    assert body['meta']['record_count'] == 2


def test_get_company_basic_info_tool_route_returns_single_record(client) -> None:
    with patch.object(twse_mcp_server, 'fetch_stock_basic_snapshot', return_value=_snapshot_frame()):
        response = client.post('/v1/tools/get_company_basic_info', json=_company_payload())

    assert response.status_code == 200
    body = response.get_json()
    assert body['dataset'] == 'company_basic_info'
    assert body['record']['stock_id'] == '2330'
    assert body['records'][0]['stock_id'] == '2330'
    assert body['meta']['record_count'] == 1


def test_get_foreign_investment_tool_route_returns_200(client) -> None:
    with patch.object(twse_mcp_server, 'fetch_foreign_investor_flow', return_value=_flow_frame()):
        response = client.post('/v1/tools/get_foreign_investment', json=_flow_payload())

    assert response.status_code == 200
    body = response.get_json()
    assert body['dataset'] == 'foreign_investor_flow'
    assert body['records'][0]['foreign_buy'] == 1000.0


def test_twse_stock_trend_tool_route_returns_chart_ready_payload(client) -> None:
    payload = {
        'stock_id': '2330',
        'trade_date': '2026-04-08',
        'market': 'ALL',
        'history_limit': 120,
        'correlation_id': 'cid-trend',
    }
    with patch.object(twse_mcp_server, 'get_stock_history', return_value=_history_frame()), patch.object(
        twse_mcp_server,
        'get_stock_report',
        return_value=_report_payload(),
    ), patch.object(twse_mcp_server, 'get_stock_sector', return_value='半導體'), patch.object(
        twse_mcp_server,
        'fetch_foreign_investor_flow',
        return_value=_flow_frame(),
    ):
        response = client.post('/v1/tools/twse_stock_trend', json=payload)

    assert response.status_code == 200
    body = response.get_json()
    assert body['dataset'] == 'twse_stock_trend'
    assert body['stock_id'] == '2330'
    assert body['status'] == 'ok'
    assert len(body['series']['candles']) == 2
    assert body['records'][0]['stock_id'] == '2330'
    assert body['institutional']['foreign_buy'] == 1000


def test_twse_stock_trend_tool_route_degrades_when_flow_fails(client) -> None:
    payload = {
        'stock_id': '2330',
        'trade_date': '2026-04-08',
        'market': 'ALL',
        'history_limit': 120,
        'correlation_id': 'cid-trend-degraded',
    }
    with patch.object(twse_mcp_server, 'get_stock_history', return_value=_history_frame()), patch.object(
        twse_mcp_server,
        'get_stock_report',
        return_value=_report_payload(),
    ), patch.object(twse_mcp_server, 'get_stock_sector', return_value='半導體'), patch.object(
        twse_mcp_server,
        'fetch_foreign_investor_flow',
        side_effect=RuntimeError('flow unavailable'),
    ):
        response = client.post('/v1/tools/twse_stock_trend', json=payload)

    assert response.status_code == 200
    body = response.get_json()
    assert body['status'] == 'partial'
    assert 'institutional' in body['degraded_fields']
    assert body['records'][0]['stock_id'] == '2330'


def test_investment_screening_tool_route_returns_report_sections(client) -> None:
    payload = {
        'stock_id': '2330',
        'trade_date': '2026-04-08',
        'market': 'ALL',
        'correlation_id': 'cid-screen',
    }
    with patch.object(twse_mcp_server, 'get_stock_report', return_value=_report_payload()), patch.object(
        twse_mcp_server,
        'get_stock_sector',
        return_value='半導體',
    ), patch.object(twse_mcp_server, 'fetch_foreign_investor_flow', return_value=_flow_frame()):
        response = client.post('/v1/tools/investment_screening', json=payload)

    assert response.status_code == 200
    body = response.get_json()
    assert body['dataset'] == 'investment_screening'
    assert body['screening']['score'] >= 1
    assert len(body['report_sections']['technical']) == 2
    assert body['records'][0]['stock_id'] == '2330'


def test_market_hotspot_tool_route_returns_breadth_and_hotspots(client) -> None:
    payload = {
        'trade_date': '2026-04-08',
        'market': 'ALL',
        'correlation_id': 'cid-hotspot',
    }
    with patch.object(twse_mcp_server, 'fetch_stock_basic_snapshot', return_value=_snapshot_frame()), patch.object(
        twse_mcp_server,
        'fetch_foreign_investor_flow',
        return_value=_flow_frame(),
    ):
        response = client.post('/v1/tools/market_hotspot', json=payload)

    assert response.status_code == 200
    body = response.get_json()
    assert body['dataset'] == 'market_hotspot'
    assert body['breadth']['universe_count'] == 1
    assert body['hotspots']['top_gainers'][0]['stock_id'] == '2330'
    assert body['records'][0]['stock_id'] == '2330'


def test_twse_stock_trend_validation_error_requires_stock_id(client) -> None:
    response = client.post(
        '/v1/tools/twse_stock_trend',
        json={
            'trade_date': '2026-04-08',
            'market': 'ALL',
            'correlation_id': 'cid-trend-invalid',
        },
    )

    assert response.status_code == 400
    _assert_error_envelope(
        response.get_json(),
        error_code='INVALID_REQUEST',
        retryable=False,
        correlation_id='cid-trend-invalid',
    )


def test_market_statistics_tool_and_legacy_routes_share_payload_shape(client) -> None:
    with patch.object(twse_mcp_server, 'fetch_stock_basic_snapshot', return_value=_snapshot_frame()):
        tool_response = client.post('/v1/tools/get_market_statistics', json=_market_payload())
        legacy_response = client.post('/v1/stock-basic-snapshot', json=_market_payload())

    assert tool_response.status_code == 200
    assert legacy_response.status_code == 200
    _assert_success_parity(tool_response.get_json(), legacy_response.get_json())


def test_foreign_investment_tool_and_legacy_routes_share_payload_shape(client) -> None:
    with patch.object(twse_mcp_server, 'fetch_foreign_investor_flow', return_value=_flow_frame()):
        tool_response = client.post('/v1/tools/get_foreign_investment', json=_flow_payload())
        legacy_response = client.post('/v1/foreign-investor-flow', json=_flow_payload())

    assert tool_response.status_code == 200
    assert legacy_response.status_code == 200
    _assert_success_parity(tool_response.get_json(), legacy_response.get_json())


def test_tool_route_validation_error_uses_consistent_error_envelope(client) -> None:
    response = client.post(
        '/v1/tools/get_market_statistics',
        json={'market': 'ALL', 'correlation_id': 'cid-invalid'},
    )

    assert response.status_code == 400
    _assert_error_envelope(
        response.get_json(),
        error_code='INVALID_REQUEST',
        retryable=False,
        correlation_id='cid-invalid',
    )


def test_market_statistics_tool_and_legacy_validation_errors_match(client) -> None:
    payload = {'market': 'ALL', 'correlation_id': 'cid-invalid'}

    tool_response = client.post('/v1/tools/get_market_statistics', json=payload)
    legacy_response = client.post('/v1/stock-basic-snapshot', json=payload)

    assert tool_response.status_code == 400
    assert legacy_response.status_code == 400
    _assert_error_envelope(
        tool_response.get_json(),
        error_code='INVALID_REQUEST',
        retryable=False,
        correlation_id='cid-invalid',
    )
    assert tool_response.get_json()['error_code'] == legacy_response.get_json()['error_code']
    assert tool_response.get_json()['retryable'] == legacy_response.get_json()['retryable']


def test_tool_route_upstream_failure_is_retryable(client) -> None:
    with patch.object(
        twse_mcp_server,
        'fetch_foreign_investor_flow',
        side_effect=RuntimeError('provider unavailable'),
    ):
        response = client.post('/v1/tools/get_foreign_investment', json=_flow_payload())

    assert response.status_code == 502
    _assert_error_envelope(
        response.get_json(),
        error_code='UPSTREAM_FAILURE',
        retryable=True,
        correlation_id='cid-flow',
    )


def test_foreign_investment_tool_and_legacy_upstream_failures_match(client) -> None:
    with patch.object(
        twse_mcp_server,
        'fetch_foreign_investor_flow',
        side_effect=RuntimeError('provider unavailable'),
    ):
        tool_response = client.post('/v1/tools/get_foreign_investment', json=_flow_payload())
        legacy_response = client.post('/v1/foreign-investor-flow', json=_flow_payload())

    assert tool_response.status_code == 502
    assert legacy_response.status_code == 502
    _assert_error_envelope(
        tool_response.get_json(),
        error_code='UPSTREAM_FAILURE',
        retryable=True,
        correlation_id='cid-flow',
    )
    assert tool_response.get_json()['error_code'] == legacy_response.get_json()['error_code']
    assert tool_response.get_json()['retryable'] == legacy_response.get_json()['retryable']


def test_company_basic_info_returns_not_found_for_missing_stock(client) -> None:
    with patch.object(twse_mcp_server, 'fetch_stock_basic_snapshot', return_value=_snapshot_frame()):
        response = client.post(
            '/v1/tools/get_company_basic_info',
            json={
                'stock_id': '9999',
                'trade_date': '2026-04-08',
                'market': 'ALL',
                'correlation_id': 'cid-missing',
                'include_etfs': True,
            },
        )

    assert response.status_code == 404
    _assert_error_envelope(
        response.get_json(),
        error_code='NOT_FOUND',
        retryable=False,
        correlation_id='cid-missing',
    )


def test_unknown_tool_returns_explicit_error(client) -> None:
    response = client.post(
        '/v1/tools/not-a-real-tool',
        json={'correlation_id': 'cid-unknown'},
    )

    assert response.status_code == 404
    _assert_error_envelope(
        response.get_json(),
        error_code='UNKNOWN_TOOL',
        retryable=False,
        correlation_id='cid-unknown',
    )


def test_tool_dispatch_logs_route_intent_and_success(
    client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch.object(twse_mcp_server, 'fetch_stock_basic_snapshot', return_value=_snapshot_frame()):
        with caplog.at_level(logging.INFO):
            response = client.post('/v1/tools/get_market_statistics', json=_market_payload())

    assert response.status_code == 200
    assert any('tool=get_market_statistics' in message and 'event=received' in message for message in caplog.messages)
    assert any('tool=get_market_statistics' in message and 'event=success' in message for message in caplog.messages)