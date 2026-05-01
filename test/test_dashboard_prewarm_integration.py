from __future__ import annotations

import pandas as pd


class _PrewarmMCPClient:
    def get_market_hotspot_sync(self, trade_date=None):
        return {
            'dataset': 'market_hotspot',
            'requested_date': trade_date,
            'as_of_date': trade_date,
            'records': [{'stock_id': '2330', 'trade_date': trade_date}],
        }

    def get_twse_stock_trend_sync(self, stock_id, trade_date=None):
        return {
            'dataset': 'twse_stock_trend',
            'requested_date': trade_date,
            'as_of_date': trade_date,
            'stock_id': stock_id,
            'records': [{'stock_id': stock_id, 'trade_date': trade_date}],
        }

    def get_investment_screening_sync(self, stock_id, trade_date=None):
        return {
            'dataset': 'investment_screening',
            'requested_date': trade_date,
            'as_of_date': trade_date,
            'stock_id': stock_id,
            'records': [{'stock_id': stock_id, 'trade_date': trade_date}],
        }


class _NoRefreshMCPClient:
    def get_twse_stock_trend_sync(self, stock_id, trade_date=None):  # pragma: no cover - should never be called
        raise AssertionError('refresh should not be called for prewarmed trend cache')

    def get_investment_screening_sync(self, stock_id, trade_date=None):  # pragma: no cover - should never be called
        raise AssertionError('refresh should not be called for prewarmed screening cache')

    def get_market_hotspot_sync(self, trade_date=None):  # pragma: no cover - should never be called
        raise AssertionError('refresh should not be called for prewarmed hotspot cache')


def test_prewarm_dashboard_aggregation_cache_saves_hotspot_and_stock_payloads(monkeypatch):
    from jobs import update_database

    saved_calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(update_database, 'save_dashboard_aggregation_cache', lambda intent_name, payload, **kwargs: saved_calls.append((intent_name, kwargs.get('stock_id'))) or True)

    summary = update_database.prewarm_dashboard_aggregation_cache(
        trade_date='2026-04-29',
        tracked_stock_ids=['2330'],
        mcp_client=_PrewarmMCPClient(),
    )

    assert summary['market_hotspot_cached'] is True
    assert ('market_hotspot', None) in saved_calls
    assert ('twse_stock_trend', '2330') in saved_calls
    assert ('investment_screening', '2330') in saved_calls


def test_dashboard_health_check_api_uses_prewarmed_cache_without_refresh(monkeypatch):
    import app as app_module
    from app import app as flask_app

    history_df = pd.DataFrame(
        [
            {
                'trade_date': '2026-04-29',
                'stock_id': '2330',
                'open_price': 750.0,
                'high_price': 772.0,
                'low_price': 745.0,
                'close_price': 768.0,
                'volume': 3949,
                'ma5': 744.0,
                'ma20': 718.0,
                'ma60': 670.0,
                'rsi': 62.0,
                'bias': 4.1,
                'chip_score': 0.81,
                'foreign_buy': 3200,
                'trust_buy': 600,
                'dealer_buy': -120,
            },
        ]
    )

    def _resolve_cache(intent_name, **kwargs):
        if intent_name == 'twse_stock_trend':
            return {
                'dataset': 'twse_stock_trend',
                'requested_date': '2026-04-29',
                'as_of_date': '2026-04-29',
                'stock_id': '2330',
                'status': 'ok',
                'cache_status': 'fresh',
                'payload_version': 'v1',
                'record': {'stock_id': '2330', 'trade_date': '2026-04-29', 'sector': '半導體'},
                'records': [{'stock_id': '2330', 'trade_date': '2026-04-29'}],
            }
        if intent_name == 'investment_screening':
            return {
                'dataset': 'investment_screening',
                'requested_date': '2026-04-29',
                'as_of_date': '2026-04-29',
                'stock_id': '2330',
                'status': 'ok',
                'cache_status': 'fresh',
                'payload_version': 'v1',
                'screening': {'score': 4},
                'report_sections': {'technical': ['cached section']},
                'record': {'stock_id': '2330', 'trade_date': '2026-04-29', 'sector': '半導體'},
                'records': [{'stock_id': '2330', 'trade_date': '2026-04-29'}],
            }
        raise AssertionError(f'unexpected intent_name: {intent_name}')

    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', _resolve_cache)
    monkeypatch.setattr(app_module, 'MCPClient', lambda: _NoRefreshMCPClient())
    monkeypatch.setattr(app_module, 'get_stock_history', lambda stock_id, limit=120, end_date=None: history_df.copy())
    monkeypatch.setattr(app_module, 'get_stock_report', lambda stock_id, as_of_date=None: {'stock_id': '2330', 'trade_date': '2026-04-29', 'close_price': 768.0, 'ma5': 744.0, 'ma20': 718.0, 'ma60': 670.0, 'rsi': 62.0, 'strategy_name': 'v34_turbo'})
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '半導體')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(app_module, '_get_sector_news_summary', lambda sector, date_str=None: {'raw': '', 'items': [], 'is_bearish': False, 'title': ''})
    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '盤勢中性。'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '法人分歧。'})
    monkeypatch.setattr(app_module, '_build_dashboard_llm_report', lambda *args, **kwargs: {'provider': 'gemini', 'status': 'fallback', 'available': False, 'used_fallback': True, 'message': 'fallback', 'trend': '', 'chips': '', 'macro': '', 'risk': ''})

    client = flask_app.test_client()
    response = client.get('/api/dashboard/health-check?symbol=2330&date=2026-04-29')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['cache_status'] == 'fresh'
    assert payload['payload_version'] == 'v1'
    assert payload['screening']['score'] == 4
    assert payload['cache_keys']['trend'].startswith('twse_stock_trend:ALL:2026-04-29:2330')