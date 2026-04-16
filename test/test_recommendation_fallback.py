import pandas as pd

from tool import db_helper


def test_get_recommendations_with_market_fallback_uses_latest_safe_day(monkeypatch):
    fallback_rows = pd.DataFrame([
        {
            'stock_id': '2330',
            'trade_date': '2026-03-11',
            'strategy': 'v36_chip_momentum',
            'close_price': 950.0,
            'ai_score': 0.82,
            'rsi': 58.0,
            'volume': 120000,
            'news_boost_reason': '',
        }
    ])

    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: object())
    monkeypatch.setattr(db_helper, 'get_latest_trade_date', lambda: '2026-03-13')
    monkeypatch.setattr(
        db_helper,
        'get_market_trend',
        lambda date_str: {
            '2026-03-13': 'BEAR',
            '2026-03-12': 'BEAR',
            '2026-03-11': 'BULL',
        }[date_str],
    )

    def fake_read_sql(sql, engine, params=None):
        sql_text = str(sql)
        if 'SELECT DISTINCT trade_date' in sql_text:
            return pd.DataFrame({'trade_date': ['2026-03-12', '2026-03-11']})
        if params['date'] == '2026-03-11':
            return fallback_rows.copy()
        return pd.DataFrame(columns=fallback_rows.columns)

    monkeypatch.setattr(db_helper.pd, 'read_sql', fake_read_sql)

    result, meta = db_helper.get_recommendations_with_market_fallback(
        date_str='2026-03-13',
        strategy='v36_chip_momentum',
        limit=5,
    )

    assert not result.empty
    assert result.iloc[0]['stock_id'] == '2330'
    assert meta['fallback_used'] is True
    assert meta['market_circuit_breaker_active'] is True
    assert meta['recommendation_date'] == '2026-03-11'


def test_get_recommendations_with_market_fallback_keeps_current_day_data(monkeypatch):
    current_rows = pd.DataFrame([
        {
            'stock_id': '6223',
            'trade_date': '2026-03-12',
            'strategy': 'v35_innovation',
            'close_price': 88.0,
            'ai_score': 0.55,
            'rsi': 57.0,
            'volume': 80000,
            'news_boost_reason': '',
        }
    ])
    fallback_rows = pd.DataFrame([
        {
            'stock_id': '2330',
            'trade_date': '2026-02-11',
            'strategy': 'v35_innovation',
            'close_price': 950.0,
            'ai_score': 0.82,
            'rsi': 58.0,
            'volume': 120000,
            'news_boost_reason': '',
        }
    ])

    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: object())
    monkeypatch.setattr(
        db_helper,
        'get_market_trend',
        lambda date_str: {
            '2026-03-12': 'BEAR',
            '2026-02-11': 'BULL',
        }[date_str],
    )

    def fake_read_sql(sql, engine, params=None):
        if params['date'] == '2026-03-12':
            return current_rows.copy()
        if params['date'] == '2026-02-11':
            return fallback_rows.copy()
        return pd.DataFrame({'trade_date': ['2026-02-11']})

    monkeypatch.setattr(db_helper.pd, 'read_sql', fake_read_sql)

    result, meta = db_helper.get_recommendations_with_market_fallback(
        date_str='2026-03-12',
        strategy='v35_innovation',
        limit=5,
    )

    assert not result.empty
    assert result.iloc[0]['stock_id'] == '6223'
    assert meta['fallback_used'] is False
    assert meta['market_circuit_breaker_active'] is True
    assert meta['current_day_recommendations_used'] is True
    assert meta['recommendation_date'] == '2026-03-12'


def test_get_recommendations_with_market_fallback_blocks_stale_data(monkeypatch):
    fallback_rows = pd.DataFrame([
        {
            'stock_id': '2330',
            'trade_date': '2026-02-11',
            'strategy': 'v31_hybrid',
            'close_price': 950.0,
            'ai_score': 0.82,
            'rsi': 58.0,
            'volume': 120000,
            'news_boost_reason': '',
        }
    ])

    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: object())
    monkeypatch.setattr(
        db_helper,
        'get_market_trend',
        lambda date_str: {
            '2026-03-12': 'BEAR',
            '2026-02-11': 'BULL',
        }[date_str],
    )

    def fake_read_sql(sql, engine, params=None):
        sql_text = str(sql)
        if 'SELECT DISTINCT trade_date' in sql_text:
            return pd.DataFrame({'trade_date': ['2026-02-11']})
        if params['date'] == '2026-03-12':
            return pd.DataFrame(columns=fallback_rows.columns)
        if params['date'] == '2026-02-11':
            return fallback_rows.copy()
        return pd.DataFrame(columns=fallback_rows.columns)

    monkeypatch.setattr(db_helper.pd, 'read_sql', fake_read_sql)

    result, meta = db_helper.get_recommendations_with_market_fallback(
        date_str='2026-03-12',
        strategy='v31_hybrid',
        limit=5,
        max_fallback_age_days=7,
    )

    assert result.empty
    assert meta['fallback_used'] is False
    assert meta['fallback_too_old'] is True
    assert meta['fallback_age_days'] == 29
    assert meta['last_available_recommendation_date'] == '2026-02-11'


def test_get_actual_latest_date_uses_intersection_and_skips_invalid_days(monkeypatch):
    market_dates = pd.DataFrame([
        {'trade_date': '2026-04-15', 'row_count': 6800, 'market_symbol_rows': 1},
        {'trade_date': '2026-04-14', 'row_count': 50, 'market_symbol_rows': 1},
        {'trade_date': '2026-04-12', 'row_count': 5400, 'market_symbol_rows': 1},
        {'trade_date': '2026-04-11', 'row_count': 5400, 'market_symbol_rows': 1},
        {'trade_date': '2026-04-10', 'row_count': 6700, 'market_symbol_rows': 1},
    ])
    recommendation_dates = pd.DataFrame({'trade_date': ['2026-04-15', '2026-04-10']})

    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: object())

    def fake_read_sql(sql, engine, params=None):
        sql_text = str(sql)
        if 'FROM daily_market_data' in sql_text:
            return market_dates.copy()
        if 'FROM daily_recommendations' in sql_text:
            return recommendation_dates.copy()
        raise AssertionError(sql_text)

    monkeypatch.setattr(db_helper.pd, 'read_sql', fake_read_sql)

    assert db_helper.get_actual_latest_date() == '2026-04-15'


def test_get_daily_recommendations_filters_heartbeat_rows(monkeypatch):
    rows = pd.DataFrame([
        {
            'stock_id': 'NONE',
            'trade_date': '2026-04-15',
            'strategy': 'v38_value_dividend',
            'close_price': 0.0,
            'ai_score': None,
            'rsi': None,
            'volume': None,
            'news_boost_reason': 'NO_CANDIDATES',
        },
        {
            'stock_id': '2330',
            'trade_date': '2026-04-15',
            'strategy': 'v38_value_dividend',
            'close_price': 952.0,
            'ai_score': 0.88,
            'rsi': 58.0,
            'volume': 180000,
            'news_boost_reason': '',
        },
    ])

    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: object())
    monkeypatch.setattr(db_helper, 'get_actual_latest_date', lambda: '2026-04-15')
    monkeypatch.setattr(db_helper.pd, 'read_sql', lambda sql, engine, params=None: rows.copy())

    result = db_helper.get_daily_recommendations(strategy='v38_value_dividend')

    assert result['stock_id'].tolist() == ['2330']


def test_format_market_fallback_notice_handles_no_safe_day():
    notice = db_helper.format_market_fallback_notice({
        'requested_date': '2026-03-13',
        'recommendation_date': '2026-03-13',
        'fallback_used': False,
        'market_circuit_breaker_active': True,
    }, '📊 籌碼動能 (V36)')

    assert '高風險區間' in notice
    assert '建議暫時觀望' in notice


def test_format_market_fallback_notice_handles_current_day_data():
    notice = db_helper.format_market_fallback_notice({
        'requested_date': '2026-03-12',
        'recommendation_date': '2026-03-12',
        'fallback_used': False,
        'market_circuit_breaker_active': True,
        'current_day_recommendations_used': True,
    }, '🚀 創新成長 (V35)')

    assert '當日既有的🚀 創新成長 (V35)推薦紀錄' in notice
    assert '降低部位並嚴設停損' in notice


def test_format_market_fallback_notice_handles_stale_data():
    notice = db_helper.format_market_fallback_notice({
        'requested_date': '2026-03-12',
        'recommendation_date': '2026-03-12',
        'fallback_used': False,
        'market_circuit_breaker_active': True,
        'fallback_too_old': True,
        'fallback_age_days': 29,
        'last_available_recommendation_date': '2026-02-11',
    }, '🔹 均衡型 (V31)')

    assert '2026-02-11' in notice
    assert '距今 29 天' in notice
    assert '不回推舊名單' in notice


def test_get_sector_news_summary_only_returns_matching_sector(monkeypatch):
    import app as app_module

    monkeypatch.setattr(
        app_module,
        'get_news_sentiment',
        lambda date_str=None: {
            'bull_sectors': ['電子零組件', '半導體'],
            'bear_sectors': ['航運'],
            'bull_reasons': ['PCB廠營收蟬聯全球第一', 'AI狂潮推升晶圓代工產值'],
            'bear_reasons': ['荷姆茲海峽封鎖物流受阻'],
            'bull_theme_map': {'電子零組件': 'PCB產業黃金十年啟動'},
            'bear_theme_map': {'航運': '荷姆茲海峽封鎖物流受阻'},
        },
    )

    matched = app_module._get_sector_news_summary('電子零組件', '2026-03-13')
    assert matched['items']
    assert matched['title'] == '🟢 電子零組件 消息面'
    assert 'PCB產業黃金十年啟動' in matched['raw']

    unmatched = app_module._get_sector_news_summary('金融保險', '2026-03-13')
    assert unmatched['items'] == []


def test_get_stock_specific_news_summary_returns_stock_level_reason():
    import app as app_module

    summary = app_module._get_stock_specific_news_summary('2881', {
        '2881': {'score': 1, 'reason': '殖利率題材升溫'},
    })

    assert summary['title'] == '🟢 個股新聞'
    assert summary['items'] == ['利多: 殖利率題材升溫']


def test_api_daily_signals_returns_fallback_metadata(monkeypatch):
    from app import app as flask_app
    import app as app_module

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
            'trade_date': '2026-03-12',
            'strategy': 'v36_chip_momentum',
            'close_price': 950.0,
            'ai_score': 0.81,
            'rsi': 58.5,
            'volume': 180000,
            'news_boost_reason': '',
        }
    ])

    class FakeStrategy:
        name = 'v36_chip_momentum'
        display_name = '📊 籌碼動能 (V36)'
        stop_loss = 0.08
        take_profit = 0.15
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

    monkeypatch.setattr(app_module, 'get_actual_latest_date', lambda: '2026-03-13')
    monkeypatch.setattr(app_module, 'get_stock_data', lambda *args, **kwargs: (market_df.copy(), kwargs.get('date_str') or '2026-03-13'))
    monkeypatch.setattr(app_module, 'supplement_financial_data', lambda df: df)
    monkeypatch.setattr(app_module, 'StrategyManager', FakeManager)
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '半導體')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(
        app_module,
        'get_news_sentiment',
        lambda date_str=None: {
            'bull_sectors': ['半導體'],
            'bear_sectors': [],
            'bull_reasons': ['AI狂潮推升晶圓代工產值'],
            'bear_reasons': [],
            'bull_theme_map': {'半導體': 'AI狂潮帶動晶圓代工產值'},
            'bear_theme_map': {},
        },
    )
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (
            persisted_df.copy(),
            {
                'requested_date': '2026-03-13',
                'recommendation_date': '2026-03-12',
                'fallback_used': True,
                'market_circuit_breaker_active': True,
            },
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/daily-signals?strategy=v36&top_n=3')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['fallback_used'] is True
    assert payload['date'] == '2026-03-12'
    assert payload['requested_date'] == '2026-03-13'
    assert 'MA60' in payload['market_warning']
    assert payload['signals'][0]['stock_id'] == '2330'
    assert payload['signals'][0]['news_reason_items']


def test_api_daily_signals_uses_stock_news_when_sector_not_matched(monkeypatch):
    from app import app as flask_app
    import app as app_module

    market_df = pd.DataFrame([
        {
            'stock_id': '2881',
            'close_price': 50.0,
            'rsi': 48.0,
            'volume': 150000,
            'ma20': 49.0,
            'ma60': 47.0,
        }
    ])
    persisted_df = pd.DataFrame([
        {
            'stock_id': '2881',
            'trade_date': '2026-03-12',
            'strategy': 'v36_chip_momentum',
            'close_price': 50.0,
            'ai_score': 0.61,
            'rsi': 48.0,
            'volume': 150000,
            'news_boost_reason': '',
        }
    ])

    class FakeStrategy:
        name = 'v36_chip_momentum'
        display_name = '📊 籌碼動能 (V36)'
        stop_loss = 0.08
        take_profit = 0.15
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

    monkeypatch.setattr(app_module, 'get_actual_latest_date', lambda: '2026-03-13')
    monkeypatch.setattr(app_module, 'get_stock_data', lambda *args, **kwargs: (market_df.copy(), kwargs.get('date_str') or '2026-03-13'))
    monkeypatch.setattr(app_module, 'supplement_financial_data', lambda df: df)
    monkeypatch.setattr(app_module, 'StrategyManager', FakeManager)
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '金融保險')
    monkeypatch.setattr(
        app_module,
        'get_news_sentiment',
        lambda date_str=None: {
            'bull_sectors': ['半導體'],
            'bear_sectors': [],
            'bull_reasons': ['AI狂潮推升晶圓代工產值'],
            'bear_reasons': [],
            'bull_theme_map': {'半導體': 'AI狂潮帶動晶圓代工產值'},
            'bear_theme_map': {},
        },
    )
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {
        '2881': {'score': 1, 'reason': '殖利率題材升溫'}
    })
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (
            persisted_df.copy(),
            {
                'requested_date': '2026-03-13',
                'recommendation_date': '2026-03-12',
                'fallback_used': True,
                'market_circuit_breaker_active': True,
            },
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/daily-signals?strategy=v36&top_n=3')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['signals'][0]['news_signal_title'] == '🟢 個股新聞'
    assert payload['signals'][0]['news_reason_items'] == ['利多: 殖利率題材升溫']


def test_api_daily_signals_uses_actual_latest_date(monkeypatch):
    from app import app as flask_app
    import app as app_module

    captured = {}
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
            'trade_date': '2026-04-10',
            'strategy': 'v36_chip_momentum',
            'close_price': 950.0,
            'ai_score': 0.81,
            'rsi': 58.5,
            'volume': 180000,
            'news_boost_reason': '',
        }
    ])

    class FakeStrategy:
        name = 'v36_chip_momentum'
        display_name = '📊 籌碼動能 (V36)'
        stop_loss = 0.08
        take_profit = 0.15
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

    def fake_get_stock_data(*args, **kwargs):
        captured['date_str'] = kwargs.get('date_str')
        return market_df.copy(), kwargs.get('date_str')

    monkeypatch.setattr(app_module, 'get_actual_latest_date', lambda: '2026-04-10')
    monkeypatch.setattr(app_module, 'get_stock_data', fake_get_stock_data)
    monkeypatch.setattr(app_module, 'supplement_financial_data', lambda df: df)
    monkeypatch.setattr(app_module, 'StrategyManager', FakeManager)
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '半導體')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(
        app_module,
        'get_news_sentiment',
        lambda date_str=None: {
            'bull_sectors': [],
            'bear_sectors': [],
            'bull_reasons': [],
            'bear_reasons': [],
            'bull_theme_map': {},
            'bear_theme_map': {},
        },
    )
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (
            persisted_df.copy(),
            {
                'requested_date': '2026-04-10',
                'recommendation_date': '2026-04-10',
                'fallback_used': False,
                'market_circuit_breaker_active': False,
            },
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/daily-signals?strategy=v36&top_n=3')
    payload = response.get_json()

    assert response.status_code == 200
    assert captured['date_str'] == '2026-04-10'
    assert payload['requested_date'] == '2026-04-10'