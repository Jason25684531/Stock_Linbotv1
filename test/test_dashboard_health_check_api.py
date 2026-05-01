import pandas as pd
import math


class _FakeMCPClient:
    def __init__(self, trend_payload=None, screening_payload=None, hotspot_payload=None):
        self._trend_payload = trend_payload
        self._screening_payload = screening_payload
        self._hotspot_payload = hotspot_payload

    def get_twse_stock_trend_sync(self, stock_id, trade_date=None):
        return self._trend_payload

    def get_investment_screening_sync(self, stock_id, trade_date=None):
        return self._screening_payload

    def get_market_hotspot_sync(self, trade_date=None):
        return self._hotspot_payload


def _build_war_room_history(*, direct_flow=True, periods=80) -> pd.DataFrame:
    dates = pd.bdate_range('2026-01-05', periods=periods)
    rows = []
    for index, trade_date in enumerate(dates):
        open_price = 100.0 + index * 1.6
        close_price = open_price + 1.1 + ((index % 5) - 2) * 0.35
        high_price = max(open_price, close_price) + 2.6 + (index % 3) * 0.25
        low_price = min(open_price, close_price) - 2.1 - (index % 2) * 0.2
        rows.append(
            {
                'trade_date': trade_date.strftime('%Y-%m-%d'),
                'stock_id': '2330',
                'open_price': round(open_price, 2),
                'high_price': round(high_price, 2),
                'low_price': round(low_price, 2),
                'close_price': round(close_price, 2),
                'volume': 1500 + index * 45,
                'ma5': round(99.0 + index * 1.55, 2),
                'ma20': round(96.0 + index * 1.42, 2),
                'ma60': round(91.0 + index * 1.12, 2),
                'rsi': round(45.0 + (index % 12) * 2.4, 2),
                'bias': round(0.8 + index * 0.18, 2),
                'chip_score': round(0.32 + index * 0.009, 3),
                'foreign_buy': None if not direct_flow else 1200 + index * 35,
                'trust_buy': None if not direct_flow else 250 + index * 9,
                'dealer_buy': None if not direct_flow else -80 + index * 4,
            }
        )
    return pd.DataFrame(rows)


def _build_war_room_report(history_df: pd.DataFrame) -> dict[str, object]:
    latest = history_df.iloc[-1]
    return {
        'stock_id': '2330',
        'trade_date': latest['trade_date'],
        'open_price': latest['open_price'],
        'high_price': latest['high_price'],
        'low_price': latest['low_price'],
        'close_price': latest['close_price'],
        'ma5': latest['ma5'],
        'ma20': latest['ma20'],
        'ma60': latest['ma60'],
        'rsi': latest['rsi'],
        'bias': latest['bias'],
        'chip_score': latest['chip_score'],
        'ai_score': 0.83,
        'strategy_name': 'v34_turbo',
        'foreign_buy': latest['foreign_buy'],
        'trust_buy': latest['trust_buy'],
        'dealer_buy': latest['dealer_buy'],
        'op_margin': 0.24,
        'revenue_yoy': 31.5,
        'eps': 14.6,
    }


def _patch_war_room_dependencies(monkeypatch, app_module, history_df: pd.DataFrame):
    report = _build_war_room_report(history_df)
    as_of_date = str(report['trade_date'])

    monkeypatch.setattr(app_module, 'get_stock_history', lambda stock_id, limit=120, end_date=None: history_df.copy())
    monkeypatch.setattr(app_module, 'get_stock_report', lambda stock_id, as_of_date=None: dict(report))
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '半導體')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(
        app_module,
        '_get_sector_news_summary',
        lambda sector, date_str=None: {
            'raw': 'AI 伺服器需求續強',
            'items': ['AI 伺服器需求續強'],
            'is_bearish': False,
            'title': '🟢 半導體 消息面',
        },
    )
    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'ok', 'date_str': as_of_date, 'summary': '盤勢偏多。'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': as_of_date, 'summary': '法人與籌碼同步偏多。'})
    monkeypatch.setattr(
        app_module,
        '_build_dashboard_rule_report',
        lambda *args, **kwargs: {
            'confidence': 73,
            'summary': '多方結構延續。',
            'signal_lights': [],
            'action_scripts': [],
        },
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
    monkeypatch.setattr(
        app_module,
        'MCPClient',
        lambda: _FakeMCPClient(
            trend_payload={
                'dataset': 'twse_stock_trend',
                'requested_date': as_of_date,
                'as_of_date': as_of_date,
                'stock_id': '2330',
                'status': 'ok',
                'fallback_used': False,
                'source': ['foreign_investor_flow'],
                'cache_status': 'fresh',
                'payload_version': 'v1',
                'record': {'stock_id': '2330', 'trade_date': as_of_date, 'sector': '半導體'},
            },
            screening_payload={
                'dataset': 'investment_screening',
                'requested_date': as_of_date,
                'as_of_date': as_of_date,
                'stock_id': '2330',
                'status': 'ok',
                'report_sections': {'technical': ['量價結構偏強']},
                'screening': {'score': 4},
                'record': {'stock_id': '2330', 'trade_date': as_of_date, 'sector': '半導體'},
            },
        ),
    )


def test_dashboard_health_check_api_returns_provenance_and_rule_report(monkeypatch):
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
            },
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
    report = {
        'stock_id': '2330',
        'trade_date': '2026-04-29',
        'open_price': 750.0,
        'high_price': 772.0,
        'low_price': 745.0,
        'close_price': 768.0,
        'ma5': 744.0,
        'ma20': 718.0,
        'ma60': 670.0,
        'rsi': 62.0,
        'bias': 4.1,
        'chip_score': 0.81,
        'ai_score': 0.85,
        'strategy_name': 'v34_turbo',
        'foreign_buy': 3200,
        'trust_buy': 600,
        'dealer_buy': -120,
        'op_margin': 0.24,
        'revenue_yoy': 31.5,
    }

    monkeypatch.setattr(app_module, 'get_stock_history', lambda stock_id, limit=120, end_date=None: history_df.copy())
    monkeypatch.setattr(app_module, 'get_stock_report', lambda stock_id, as_of_date=None: dict(report))
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '半導體')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(app_module, '_get_sector_news_summary', lambda sector, date_str=None: {'raw': 'AI伺服器需求續強', 'items': ['AI伺服器需求續強'], 'is_bearish': False, 'title': '🟢 半導體 消息面'})
    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '盤面偏多，上漲家數明顯優於下跌家數。'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '三大法人偏多，且外資站在買方，籌碼面偏正向。'})
    monkeypatch.setattr(app_module, '_build_dashboard_llm_report', lambda *args, **kwargs: {'provider': 'gemini', 'status': 'fallback', 'available': False, 'used_fallback': True, 'message': '未設定 GEMINI_KEY，顯示規則版診斷。', 'trend': '', 'chips': '', 'macro': '', 'risk': ''})
    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', lambda *args, **kwargs: kwargs['refresh_fn']())
    monkeypatch.setattr(
        app_module,
        'MCPClient',
        lambda: _FakeMCPClient(
            trend_payload={
                'dataset': 'twse_stock_trend',
                'requested_date': '2026-04-29',
                'as_of_date': '2026-04-29',
                'stock_id': '2330',
                'status': 'ok',
                'fallback_used': False,
                'source': ['foreign_investor_flow'],
                'cache_status': 'fresh',
                'payload_version': 'v1',
                'quote': {'close_price': 768.0},
                'indicators': {'chip_score': 0.81},
                'institutional': {'foreign_buy': 3200, 'trust_buy': 600, 'dealer_buy': -120},
                'series': {
                    'candles': [
                        {'time': '2026-04-28', 'open': 742.0, 'high': 752.0, 'low': 736.0, 'close': 748.0},
                        {'time': '2026-04-29', 'open': 750.0, 'high': 772.0, 'low': 745.0, 'close': 768.0},
                    ]
                },
                'record': {'stock_id': '2330', 'trade_date': '2026-04-29', 'sector': '半導體'},
            },
            screening_payload={
                'dataset': 'investment_screening',
                'requested_date': '2026-04-29',
                'as_of_date': '2026-04-29',
                'stock_id': '2330',
                'status': 'ok',
                'report_sections': {'technical': ['RSI 62.0', 'AI score 0.85']},
                'screening': {'score': 4},
                'record': {'stock_id': '2330', 'trade_date': '2026-04-29', 'sector': '半導體'},
            },
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/dashboard/health-check?symbol=2330&date=2026-04-29')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['status'] == 'ok'
    assert payload['symbol'] == '2330'
    assert payload['requested_date'] == '2026-04-29'
    assert payload['as_of_date'] == '2026-04-29'
    assert payload['fallback_used'] is False
    assert payload['quote']['close_price'] == 768.0
    assert len(payload['series']['candles']) == 2
    assert payload['rule_report']['confidence'] >= 60
    assert len(payload['rule_report']['signal_lights']) == 3
    assert len(payload['rule_report']['action_scripts']) == 3
    assert payload['news']['title'] == '🟢 半導體 消息面'
    assert payload['llm_report']['used_fallback'] is True
    assert payload['cache_status'] == 'fresh'
    assert payload['payload_version'] == 'v1'
    assert payload['screening']['score'] == 4


def test_dashboard_health_check_api_marks_fallback_date(monkeypatch):
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
    monkeypatch.setattr(app_module, 'get_stock_report', lambda stock_id, as_of_date=None: {'stock_id': '2330', 'trade_date': '2026-04-28', 'close_price': 748.0, 'ma5': 739.0, 'ma20': 710.0, 'ma60': 666.0, 'rsi': 58.0, 'strategy_name': 'v34_turbo'})
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '半導體')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(app_module, '_get_sector_news_summary', lambda sector, date_str=None: {'raw': '', 'items': [], 'is_bearish': False, 'title': ''})
    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '盤勢中性。'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '法人分歧。'})
    monkeypatch.setattr(app_module, '_build_dashboard_llm_report', lambda *args, **kwargs: {'provider': 'gemini', 'status': 'fallback', 'available': False, 'used_fallback': True, 'message': 'fallback', 'trend': '', 'chips': '', 'macro': '', 'risk': ''})
    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', lambda *args, **kwargs: kwargs['refresh_fn']())
    monkeypatch.setattr(
        app_module,
        'MCPClient',
        lambda: _FakeMCPClient(
            trend_payload={
                'dataset': 'twse_stock_trend',
                'requested_date': '2026-04-29',
                'as_of_date': '2026-04-28',
                'stock_id': '2330',
                'status': 'partial',
                'fallback_used': True,
                'source': ['foreign_investor_flow'],
                'warnings': ['requested date unavailable'],
                'cache_status': 'fresh',
                'record': {'stock_id': '2330', 'trade_date': '2026-04-28', 'sector': '半導體'},
            },
            screening_payload=None,
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/dashboard/health-check?symbol=2330&date=2026-04-29')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['status'] == 'partial'
    assert payload['as_of_date'] == '2026-04-28'
    assert payload['fallback_used'] is True
    assert 'requested date unavailable' in payload['warnings']


def test_dashboard_health_check_api_returns_partial_when_mcp_fields_degrade(monkeypatch):
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

    monkeypatch.setattr(app_module, 'get_stock_history', lambda stock_id, limit=120, end_date=None: history_df.copy())
    monkeypatch.setattr(app_module, 'get_stock_report', lambda stock_id, as_of_date=None: {'stock_id': '2330', 'trade_date': '2026-04-29', 'close_price': 768.0, 'ma5': 744.0, 'ma20': 718.0, 'ma60': 670.0, 'rsi': 62.0, 'chip_score': 0.81, 'strategy_name': 'v34_turbo'})
    monkeypatch.setattr(app_module, 'get_stock_sector', lambda stock_id: '半導體')
    monkeypatch.setattr(app_module, '_get_stock_mentions_map', lambda stock_ids: {})
    monkeypatch.setattr(app_module, '_get_sector_news_summary', lambda sector, date_str=None: {'raw': '', 'items': [], 'is_bearish': False, 'title': ''})
    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '盤勢中性。'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '法人分歧。'})
    monkeypatch.setattr(app_module, '_build_dashboard_llm_report', lambda *args, **kwargs: {'provider': 'gemini', 'status': 'fallback', 'available': False, 'used_fallback': True, 'message': 'fallback', 'trend': '', 'chips': '', 'macro': '', 'risk': ''})
    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', lambda *args, **kwargs: kwargs['refresh_fn']())
    monkeypatch.setattr(
        app_module,
        'MCPClient',
        lambda: _FakeMCPClient(
            trend_payload={
                'dataset': 'twse_stock_trend',
                'requested_date': '2026-04-29',
                'as_of_date': '2026-04-29',
                'stock_id': '2330',
                'status': 'partial',
                'fallback_used': False,
                'degraded_fields': ['institutional'],
                'warnings': ['institutional flow degraded'],
                'cache_status': 'fresh',
                'record': {'stock_id': '2330', 'trade_date': '2026-04-29', 'sector': '半導體'},
            },
            screening_payload=None,
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/dashboard/health-check?symbol=2330&date=2026-04-29')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['status'] == 'partial'
    assert payload['cache_status'] == 'fresh'
    assert 'institutional' in payload['degraded_fields']
    assert 'institutional flow degraded' in payload['warnings']


def test_dashboard_macro_api_returns_degraded_status(monkeypatch):
    import app as app_module
    from app import app as flask_app

    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'error', 'date_str': '2026-04-29', 'message': '盤勢快照暫不可用。'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '法人偏多。'})
    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', lambda *args, **kwargs: kwargs['refresh_fn']())
    monkeypatch.setattr(
        app_module,
        'MCPClient',
        lambda: _FakeMCPClient(
            hotspot_payload={
                'dataset': 'market_hotspot',
                'requested_date': '2026-04-29',
                'as_of_date': '2026-04-29',
                'status': 'degraded',
                'source': ['stock_basic_snapshot'],
                'degraded_fields': ['institutional'],
                'warnings': ['institutional flow unavailable for hotspot ranking'],
                'cache_status': 'fresh',
                'payload_version': 'v1',
                'breadth': {'advancing': 1, 'declining': 1, 'unchanged': 0},
                'hotspots': {'top_gainers': [{'stock_id': '2330'}], 'top_foreign_inflows': []},
                'records': [{'stock_id': '2330', 'trade_date': '2026-04-29'}],
            },
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/dashboard/macro?date=2026-04-29')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['status'] == 'degraded'
    assert payload['market_snapshot']['status'] == 'error'
    assert payload['chip_snapshot']['status'] == 'ok'
    assert payload['market_hotspot']['breadth']['advancing'] == 1
    assert payload['cache_status'] == 'fresh'


def test_dashboard_macro_api_sanitizes_nan_payload_values(monkeypatch):
    import app as app_module
    from app import app as flask_app

    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '盤勢中性。'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '法人分歧。'})
    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', lambda *args, **kwargs: kwargs['refresh_fn']())
    monkeypatch.setattr(
        app_module,
        'MCPClient',
        lambda: _FakeMCPClient(
            hotspot_payload={
                'dataset': 'market_hotspot',
                'requested_date': '2026-04-29',
                'as_of_date': '2026-04-29',
                'status': 'ok',
                'source': ['stock_basic_snapshot'],
                'cache_status': 'fresh',
                'payload_version': 'v1',
                'breadth': {'advancing': 1, 'declining': 1, 'unchanged': 0},
                'hotspots': {
                    'top_gainers': [
                        {'stock_id': '2330', 'stock_name': math.nan, 'trade_date': '2026-04-29'}
                    ],
                    'top_foreign_inflows': [],
                },
                'records': [
                    {'stock_id': '2330', 'stock_name': math.nan, 'trade_date': '2026-04-29'}
                ],
            },
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/dashboard/macro?date=2026-04-29')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['market_hotspot']['hotspots']['top_gainers'][0]['stock_name'] is None


def test_dashboard_json_sanitizer_handles_pandas_missing_scalars():
    import app as app_module

    payload = app_module._sanitize_dashboard_json(
        {
            'stock_name': pd.NA,
            'updated_at': pd.NaT,
            'items': [{'value': pd.NA}],
        }
    )

    assert payload == {
        'stock_name': None,
        'updated_at': None,
        'items': [{'value': None}],
    }


def test_dashboard_template_installs_lightweight_charts_legacy_patch():
    from flask import render_template
    from app import app as flask_app

    with flask_app.test_request_context('/dashboard'):
        html = render_template(
            'dashboard.html',
            active_strategies=['v31_hybrid'],
            strategy_options=['v31_hybrid'],
            current_strategy='v31_hybrid',
            current_mode='balanced',
        )

    assert '__stockLinbotLegacyPatch' in html
    assert 'addCandlestickSeries' in html


def test_dashboard_health_check_api_accepts_war_room_query_state(monkeypatch):
    import app as app_module
    from app import app as flask_app

    captured: dict[str, object] = {}

    def fake_builder(stock_id, requested_date=None, period=None, overlays=None, panes=None):
        captured.update(
            {
                'stock_id': stock_id,
                'requested_date': requested_date,
                'period': period,
                'overlays': overlays,
                'panes': panes,
            }
        )
        return {'status': 'ok', 'symbol': stock_id, 'view_state': {'period': period}}

    monkeypatch.setattr(app_module, '_build_dashboard_health_check_payload', fake_builder)

    client = flask_app.test_client()
    response = client.get(
        '/api/dashboard/health-check?symbol=2330&date=2026-04-24'
        '&period=weekly&overlay=ma5,support&overlays=resistance'
        '&pane=macd,rsi&panes=flow'
    )

    assert response.status_code == 200
    assert captured['stock_id'] == '2330'
    assert captured['requested_date'] == '2026-04-24'
    assert captured['period'] == 'weekly'
    assert captured['overlays'] == ['ma5', 'support', 'resistance']
    assert captured['panes'] == ['macd', 'rsi', 'flow']


def test_dashboard_health_check_api_returns_weekly_war_room_payload(monkeypatch):
    import app as app_module
    from app import app as flask_app

    history_df = _build_war_room_history(direct_flow=True)
    _patch_war_room_dependencies(monkeypatch, app_module, history_df)

    client = flask_app.test_client()
    response = client.get(
        '/api/dashboard/health-check?symbol=2330&date=2026-04-24'
        '&period=weekly&overlays=ma5,ma20,support,resistance'
        '&panes=macd,rsi,kd,flow'
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['status'] == 'ok'
    assert payload['view_state']['period'] == 'weekly'
    assert payload['view_state']['overlays']['selected'] == ['ma5', 'ma20', 'support', 'resistance']
    assert payload['view_state']['panes']['selected'] == ['macd', 'rsi', 'kd', 'flow']
    assert payload['war_room']['timeframe']['period'] == 'weekly'
    assert len(payload['series']['candles']) < len(history_df)
    assert payload['war_room']['structure']['status'] == 'ok'
    assert payload['war_room']['structure']['supports'][0]['label'] == 'S1'
    assert payload['war_room']['structure']['resistances'][0]['label'] == 'R1'
    assert payload['war_room']['price_map']['close_price'] == payload['quote']['close_price']
    assert payload['war_room']['panes']['macd']['status'] == 'ok'
    assert payload['war_room']['panes']['flow']['source_mode'] == 'direct'


def test_dashboard_health_check_api_preserves_long_war_room_series_when_mcp_series_is_short(monkeypatch):
    import app as app_module
    from app import app as flask_app

    history_df = _build_war_room_history(direct_flow=True, periods=320)
    _patch_war_room_dependencies(monkeypatch, app_module, history_df)
    requested_limits: list[int] = []

    def fake_history(stock_id, limit=120, end_date=None):
        requested_limits.append(limit)
        return history_df.copy()

    latest = history_df.iloc[-1]
    short_series = {
        'candles': [
            {
                'time': row['trade_date'],
                'open': row['open_price'],
                'high': row['high_price'],
                'low': row['low_price'],
                'close': row['close_price'],
            }
            for _, row in history_df.head(120).iterrows()
        ]
    }
    monkeypatch.setattr(app_module, 'get_stock_history', fake_history)
    monkeypatch.setattr(
        app_module,
        'MCPClient',
        lambda: _FakeMCPClient(
            trend_payload={
                'dataset': 'twse_stock_trend',
                'requested_date': latest['trade_date'],
                'as_of_date': latest['trade_date'],
                'stock_id': '2330',
                'status': 'ok',
                'fallback_used': False,
                'source': ['foreign_investor_flow'],
                'cache_status': 'fresh',
                'payload_version': 'v1',
                'series': short_series,
                'record': {'stock_id': '2330', 'trade_date': latest['trade_date'], 'sector': '半導體'},
            },
            screening_payload=None,
        ),
    )

    client = flask_app.test_client()
    response = client.get('/api/dashboard/health-check?symbol=2330&period=daily')
    payload = response.get_json()

    assert response.status_code == 200
    assert requested_limits and requested_limits[0] >= 260
    assert payload['war_room']['timeframe']['resolved_bars'] == len(history_df)
    assert len(payload['series']['candles']) == len(history_df)


def test_dashboard_health_check_api_marks_proxy_flow_when_direct_series_missing(monkeypatch):
    import app as app_module
    from app import app as flask_app

    history_df = _build_war_room_history(direct_flow=False)
    _patch_war_room_dependencies(monkeypatch, app_module, history_df)

    client = flask_app.test_client()
    response = client.get('/api/dashboard/health-check?symbol=2330&date=2026-04-24&period=monthly&panes=flow')
    payload = response.get_json()

    flow_pane = payload['war_room']['panes']['flow']

    assert response.status_code == 200
    assert payload['view_state']['period'] == 'monthly'
    assert flow_pane['status'] == 'partial'
    assert flow_pane['source_mode'] == 'proxy'
    assert flow_pane['proxy_basis'] == ['chip_score']
    assert 'proxy' in flow_pane['message'].lower()
    assert payload['war_room']['chip_flow']['source_mode'] == 'proxy'
