import pandas as pd

from core.db_helper import merge_recommendations_with_market_data


def test_merge_recommendations_with_market_data_prefers_market_trade_date_over_created_at():
    recommendations = pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'trade_date': '2026-04-24',
                'close_price': 950.0,
                'rsi': 58.5,
                'volume': 180000,
                'created_at': '2026-04-29 09:30:00',
            }
        ]
    )
    market_df = pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'trade_date': '2026-04-28',
                'close_price': 952.0,
                'rsi': 59.0,
                'volume': 200000,
                'created_at': '2026-04-27 15:00:00',
            }
        ]
    )

    merged = merge_recommendations_with_market_data(recommendations, market_df)

    assert merged.iloc[0]['close_price'] == 952.0
    assert merged.iloc[0]['price_trade_date'] == '2026-04-28'
    assert merged.iloc[0]['price_basis'] == 'latest_actual_close'
    assert merged.iloc[0]['recommendation_trade_date'] == '2026-04-24'


def test_api_daily_signals_exposes_market_and_recommendation_price_provenance(monkeypatch):
    import app as app_module
    from app import app as flask_app

    market_df = pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'trade_date': '2026-04-28',
                'close_price': 952.0,
                'rsi': 59.0,
                'volume': 200000,
                'ma20': 910.0,
                'ma60': 870.0,
                'bias': 4.8,
                'foreign_buy': 3200,
            }
        ]
    )
    persisted_df = pd.DataFrame(
        [
            {
                'stock_id': '2330',
                'trade_date': '2026-04-24',
                'strategy': 'v36_chip_momentum',
                'close_price': 950.0,
                'ai_score': 0.81,
                'rsi': 58.5,
                'volume': 180000,
                'news_boost_reason': '',
            }
        ]
    )
    fallback_meta = {
        'requested_date': '2026-04-29',
        'market_anchor_date': '2026-04-28',
        'recommendation_date': '2026-04-24',
        'resolution_source': 'strategy_fallback',
        'fallback_used': True,
        'has_persisted_snapshot': True,
        'market_circuit_breaker_active': False,
        'current_day_recommendations_used': False,
        'fallback_too_old': False,
        'fallback_age_days': 4,
        'last_available_recommendation_date': '2026-04-24',
    }

    class FakeStrategy:
        name = 'v36_chip_momentum'
        display_name = 'Chip Momentum'
        stop_loss = 0.08
        take_profit = 0.15
        max_hold_days = 12
        features = []

    class FakeManager:
        def get_strategy(self, key):
            return FakeStrategy() if key == 'v36_chip_momentum' else None

        def get_active_strategy(self):
            return FakeStrategy()

        def get_active_strategy_names(self):
            return ['v36_chip_momentum']

    monkeypatch.setattr(app_module, 'StrategyManager', FakeManager)
    monkeypatch.setattr(app_module, 'get_stock_data', lambda date_str=None: (market_df.copy(), '2026-04-28'))
    monkeypatch.setattr(app_module, 'supplement_financial_data', lambda df: df)
    monkeypatch.setattr(app_module, '_current_line_date', lambda: '2026-04-29')
    monkeypatch.setattr(app_module, '_resolve_ui_baseline_date', lambda: '2026-04-28')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(
        app_module,
        '_resolve_signal_news_info',
        lambda row, date_str, stock_mentions_map: {
            'raw': '',
            'items': [],
            'title': '',
            'is_bearish': False,
        },
    )
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: 'Semiconductor')
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (persisted_df.copy(), fallback_meta.copy()),
    )

    client = flask_app.test_client()
    response = client.get('/api/daily-signals?strategy=v36&top_n=3')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['requested_date'] == '2026-04-29'
    assert payload['market_anchor_date'] == '2026-04-28'
    assert payload['recommendation_date'] == '2026-04-24'
    assert payload['fallback_used'] is True

    signal = payload['signals'][0]
    assert signal['close_price'] == 952.0
    assert signal['price_trade_date'] == '2026-04-28'
    assert signal['price_basis'] == 'latest_actual_close'
    assert signal['price_data_source'] == 'daily_market_data'
    assert signal['price_is_stale'] is False
    assert signal['recommendation_close_price'] == 950.0
    assert signal['recommendation_trade_date'] == '2026-04-24'
    assert signal['recommendation_price_basis'] == 'raw_close'
    assert signal['recommendation_data_source'] == 'daily_recommendations'
    assert signal['recommendation_is_stale'] is True


def test_dashboard_health_check_quote_exposes_price_provenance(monkeypatch):
    import app as app_module
    from app import app as flask_app

    history_df = pd.DataFrame(
        [
            {
                'trade_date': '2026-04-28',
                'stock_id': '2330',
                'open_price': 742.0,
                'high_price': 752.0,
                'low_price': 736.0,
                'close_price': 748.0,
                'volume': 3200,
                'ma5': 739.0,
                'ma20': 710.0,
                'ma60': 666.0,
                'rsi': 58.0,
                'bias': 3.2,
                'chip_score': 0.72,
                'foreign_buy': 2200,
                'trust_buy': 400,
                'dealer_buy': 80,
            }
        ]
    )

    monkeypatch.setattr(app_module, 'get_stock_history', lambda stock_id, limit=120, end_date=None: history_df.copy())
    monkeypatch.setattr(
        app_module,
        'get_stock_report',
        lambda stock_id, as_of_date=None: {
            'stock_id': '2330',
            'trade_date': '2026-04-28',
            'close_price': 748.0,
            'ma5': 739.0,
            'ma20': 710.0,
            'ma60': 666.0,
            'rsi': 58.0,
            'strategy_name': 'v34_turbo',
        },
    )
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: 'Semiconductor')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(app_module, '_get_sector_news_summary', lambda sector, date_str=None: {'raw': '', 'items': [], 'is_bearish': False, 'title': ''})
    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-28', 'summary': 'ok'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-28', 'summary': 'ok'})
    monkeypatch.setattr(
        app_module,
        '_build_dashboard_rule_report',
        lambda *args, **kwargs: {'confidence': 70, 'summary': 'ok', 'signal_lights': [], 'action_scripts': []},
    )
    monkeypatch.setattr(
        app_module,
        '_build_dashboard_llm_report',
        lambda *args, **kwargs: {
            'provider': 'gemini',
            'status': 'fallback',
            'available': False,
            'used_fallback': True,
            'message': 'fallback',
            'trend': '',
            'chips': '',
            'macro': '',
            'risk': '',
        },
    )
    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', lambda *args, **kwargs: kwargs['refresh_fn']())
    monkeypatch.setattr(app_module, 'MCPClient', lambda: type('FakeMCPClient', (), {
        'get_twse_stock_trend_sync': lambda self, stock_id, trade_date=None: None,
        'get_investment_screening_sync': lambda self, stock_id, trade_date=None: None,
    })())

    client = flask_app.test_client()
    response = client.get('/api/dashboard/health-check?symbol=2330&date=2026-04-29')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['requested_date'] == '2026-04-29'
    assert payload['as_of_date'] == '2026-04-28'
    assert payload['fallback_used'] is True
    assert payload['quote']['close_price'] == 748.0
    assert payload['quote']['trade_date'] == '2026-04-28'
    assert payload['quote']['source_date'] == '2026-04-28'
    assert payload['quote']['price_basis'] == 'latest_actual_close'
    assert payload['quote']['data_source'] == 'daily_market_data'
    assert payload['quote']['is_stale'] is True
