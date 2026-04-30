import pandas as pd


def test_line_web_and_push_share_same_fallback_metadata(monkeypatch):
    import app as app_module
    from app import app as flask_app
    from jobs import push_to_line

    market_df = pd.DataFrame([
        {
            'stock_id': '2330',
            'close_price': 952.0,
            'rsi': 59.0,
            'volume': 200000,
            'ma20': 910.0,
            'ma60': 870.0,
        }
    ])
    persisted_df = pd.DataFrame([
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
    ])
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
        display_name = '📊 籌碼動能 (V36)'
        stop_loss = 0.08
        take_profit = 0.15
        max_hold_days = 12
        features = []

        def filter_candidates(self, df):
            return df

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
    monkeypatch.setattr(app_module, '_resolve_signal_news_info', lambda row, date_str, stock_mentions_map: {'raw': '', 'items': [], 'title': '', 'is_bearish': False})
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '半導體')
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (persisted_df.copy(), fallback_meta.copy()),
    )

    line_reply = app_module.get_strategy_recommendation(as_flex=False, strategy_key='v36_chip_momentum')
    assert '2026-04-24' in line_reply
    assert '2026-04-29' in line_reply

    client = flask_app.test_client()
    response = client.get('/api/daily-signals?strategy=v36&top_n=3')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['requested_date'] == '2026-04-29'
    assert payload['market_anchor_date'] == '2026-04-28'
    assert payload['recommendation_date'] == '2026-04-24'
    assert payload['resolution_source'] == 'strategy_fallback'
    assert payload['fallback_used'] is True

    monkeypatch.setattr(
        push_to_line,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (persisted_df.copy(), fallback_meta.copy()),
    )

    picks, title, notice = push_to_line._pick_featured_stocks(
        engine=object(),
        requested_date='2026-04-29',
        strategy_names=['v36_chip_momentum'],
        n=5,
    )

    assert picks[0][1] == '2330'
    assert title == '📊 籌碼動能 (V36)'
    assert '2026-04-29' in notice
    assert '2026-04-24' in notice


def test_line_web_and_push_share_same_day_heartbeat_state(monkeypatch):
    import app as app_module
    from app import app as flask_app
    from jobs import push_to_line

    market_df = pd.DataFrame([
        {
            'stock_id': '2881',
            'close_price': 52.0,
            'rsi': 48.0,
            'volume': 140000,
            'ma20': 50.0,
            'ma60': 47.0,
        }
    ])
    heartbeat_meta = {
        'requested_date': '2026-04-29',
        'market_anchor_date': '2026-04-28',
        'recommendation_date': '2026-04-28',
        'resolution_source': 'heartbeat',
        'fallback_used': False,
        'has_persisted_snapshot': True,
        'market_circuit_breaker_active': False,
        'current_day_recommendations_used': False,
        'fallback_too_old': False,
        'fallback_age_days': 0,
        'last_available_recommendation_date': None,
    }

    class FakeStrategy:
        name = 'v38_value_dividend'
        display_name = '💰 高殖利率 (V38)'
        stop_loss = 0.06
        take_profit = 0.12
        max_hold_days = 15
        features = []

        def filter_candidates(self, df):
            return df

    class FakeManager:
        def get_strategy(self, key):
            return FakeStrategy() if key == 'v38_value_dividend' else None

        def get_active_strategy(self):
            return FakeStrategy()

        def get_active_strategy_names(self):
            return ['v38_value_dividend']

    monkeypatch.setattr(app_module, 'StrategyManager', FakeManager)
    monkeypatch.setattr(app_module, 'get_stock_data', lambda date_str=None: (market_df.copy(), '2026-04-28'))
    monkeypatch.setattr(app_module, 'supplement_financial_data', lambda df: df)
    monkeypatch.setattr(app_module, '_current_line_date', lambda: '2026-04-29')
    monkeypatch.setattr(app_module, '_resolve_ui_baseline_date', lambda: '2026-04-28')
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (pd.DataFrame(), heartbeat_meta.copy()),
    )

    line_reply = app_module.get_strategy_recommendation(as_flex=False, strategy_key='v38_value_dividend')
    assert '2026-04-28' in line_reply
    assert '無符合條件的股票' in line_reply

    client = flask_app.test_client()
    response = client.get('/api/daily-signals?strategy=v38&top_n=3')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['signals'] == []
    assert payload['resolution_source'] == 'heartbeat'
    assert payload['has_persisted_snapshot'] is True

    monkeypatch.setattr(
        push_to_line,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (pd.DataFrame(), heartbeat_meta.copy()),
    )

    picks, _, notice = push_to_line._pick_featured_stocks(
        engine=object(),
        requested_date='2026-04-29',
        strategy_names=['v38_value_dividend'],
        n=5,
    )

    assert picks == []
    assert '零候選' in notice