"""Focused contracts for the Web recommendation and chart data boundaries."""

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from core import db_helper


def _database():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE daily_market_data (trade_date TEXT, stock_id TEXT)'))
        conn.execute(text('CREATE TABLE daily_recommendations (stock_id TEXT, trade_date TEXT, strategy TEXT, ai_score REAL, close_price REAL, rsi REAL, volume INTEGER, news_boost_reason TEXT)'))
    return engine


def test_latest_valid_market_date_matches_full_history_rules(monkeypatch):
    engine = _database()
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    with engine.begin() as conn:
        for date, stocks in [('2026-10-08', ['0050', '2330', '2317']), ('2026-10-09', ['2330', '2317', '2454']), ('2026-10-10', ['0050', '2330', '2317'])]:
            conn.execute(text('INSERT INTO daily_market_data VALUES (:date, :stock)'), [{'date': date, 'stock': stock} for stock in stocks])
    monkeypatch.setattr(db_helper.Config, 'MARKET_SYMBOL', '0050')
    for cutoff in ['2026-10-08', '2026-10-09', '2026-10-10', '2026-10-13']:
        expected = db_helper.get_valid_market_dates(end_date=cutoff, min_row_count=2)
        assert db_helper.get_latest_valid_market_date(cutoff, min_row_count=2) == (expected[-1] if expected else None)
    assert db_helper.get_latest_valid_market_date('2026-10-07', min_row_count=2) is None


def test_latest_valid_market_date_handles_empty_data_boundaries_and_long_gaps(monkeypatch):
    engine = _database()
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(db_helper.Config, 'MARKET_SYMBOL', '0050')
    assert db_helper.get_valid_market_dates(min_row_count=2) == []
    assert db_helper.get_latest_valid_market_date(min_row_count=2) is None

    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_market_data VALUES "
            "('2026-09-01','0050'), ('2026-09-01','2330'), ('2026-09-01','2317')"
        ))
        # More than one page of invalid dates ensures the bounded resolver keeps scanning.
        for day in pd.date_range('2026-09-02', periods=20, freq='D'):
            date = day.strftime('%Y-%m-%d')
            conn.execute(text(
                "INSERT INTO daily_market_data VALUES "
                "(:date,'2330'), (:date,'2317'), (:date,'2454')"
            ), {'date': date})
        # Exactly min_row_count rows fails the established strict-greater-than rule.
        conn.execute(text(
            "INSERT INTO daily_market_data VALUES "
            "('2026-10-08','0050'), ('2026-10-08','2330')"
        ))
        # Enough rows without the market symbol is still invalid.
        conn.execute(text(
            "INSERT INTO daily_market_data VALUES "
            "('2026-10-09','2330'), ('2026-10-09','2317'), ('2026-10-09','2454')"
        ))
        # A weekend with otherwise valid-looking rows must not be selected.
        conn.execute(text(
            "INSERT INTO daily_market_data VALUES "
            "('2026-10-10','0050'), ('2026-10-10','2330'), ('2026-10-10','2317')"
        ))

    assert db_helper.get_latest_valid_market_date(min_row_count=2) == '2026-09-01'
    assert db_helper.get_latest_valid_market_date('2026-09-01', min_row_count=2) == '2026-09-01'
    assert db_helper.get_latest_valid_market_date('2026-08-31', min_row_count=2) is None


def test_canonical_and_legacy_queries_resolve_same_snapshot(monkeypatch):
    engine = _database()
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(db_helper, '_resolve_market_anchor_date', lambda requested: '2026-10-08')
    monkeypatch.setattr(db_helper, 'get_market_trend', lambda date: 'BULL')
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO daily_recommendations VALUES ('2330','2026-10-08','v31_hybrid',0.8,100,50,1000,'')"))
    canonical, meta = db_helper.get_recommendations_with_market_fallback('2026-10-08', 'hybrid_trend_rank', limit=5)
    legacy, legacy_meta = db_helper.get_recommendations_with_market_fallback('2026-10-08', 'v31_hybrid', limit=5)
    assert canonical['stock_id'].tolist() == legacy['stock_id'].tolist() == ['2330']
    assert meta == legacy_meta
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO daily_recommendations VALUES ('NONE','2026-10-08','hybrid_trend_rank',NULL,NULL,NULL,NULL,'NO_CANDIDATES_HEARTBEAT')"))
    rows, meta = db_helper.get_recommendations_with_market_fallback('2026-10-08', 'v31_hybrid', limit=5)
    assert rows.empty and meta['resolution_source'] == 'heartbeat'


def test_canonical_same_day_snapshot_wins_without_mixing_legacy_heartbeat(monkeypatch):
    engine = _database()
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(db_helper, '_resolve_market_anchor_date', lambda requested: '2026-10-08')
    monkeypatch.setattr(db_helper, 'get_market_trend', lambda date: 'BULL')
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_recommendations VALUES "
            "('2330','2026-10-08','hybrid_trend_rank',0.9,100,50,1000,'')"
        ))
        conn.execute(text(
            "INSERT INTO daily_recommendations VALUES "
            "('NONE','2026-10-08','v31_hybrid',NULL,NULL,NULL,NULL,'NO_CANDIDATES_HEARTBEAT')"
        ))

    rows, meta = db_helper.get_recommendations_with_market_fallback(
        '2026-10-08', 'hybrid_trend_rank', limit=5,
    )

    assert rows['stock_id'].tolist() == ['2330']
    assert meta['resolution_source'] == 'persisted'
    assert meta['has_persisted_snapshot'] is True


def test_fundamental_missing_snapshot_never_falls_back_to_legacy_rows(monkeypatch):
    from core.strategy_manager import StrategyManager

    engine = _database()
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(db_helper, '_resolve_market_anchor_date', lambda requested: '2026-10-08')
    monkeypatch.setattr(db_helper, 'get_market_trend', lambda date: 'BULL')
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_recommendations VALUES "
            "('2330','2026-10-08','v31_hybrid',0.9,100,50,1000,'')"
        ))

    rows, meta = db_helper.get_recommendations_with_market_fallback(
        '2026-10-08', StrategyManager.FUNDAMENTAL_RUNTIME_STRATEGY_ID, limit=5,
    )

    assert rows.empty
    assert meta['resolution_source'] == 'missing'
    assert meta['has_persisted_snapshot'] is False


def test_web_and_line_resolve_same_strategy_and_requested_date(monkeypatch):
    import app.web_server as web_server
    from app import app as flask_app
    from jobs import push_to_line

    engine = _database()
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(db_helper, 'get_latest_valid_market_date', lambda **kwargs: '2026-10-08')
    monkeypatch.setattr(db_helper, 'get_market_trend', lambda date: 'BULL')
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda: '2026-10-08')
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_recommendations VALUES "
            "('2330','2026-10-08','v31_hybrid',0.9,100,50,1000,'')"
        ))

    response = flask_app.test_client().get(
        '/api/market/recommendations?strategy=hybrid_trend_rank&date=2026-10-13&top_n=5'
    )
    line_rows, line_meta = push_to_line._resolve_push_strategy_rows(
        'v31_hybrid', '2026-10-13', 5,
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['strategy_id'] == 'hybrid_trend_rank'
    assert payload['requested_date'] == line_meta['requested_date'] == '2026-10-13'
    assert payload['market_anchor_date'] == line_meta['market_anchor_date'] == '2026-10-08'
    assert [row['stock_id'] for row in payload['data']['recommendations']] == line_rows['stock_id'].tolist() == ['2330']


def test_fundamental_market_endpoint_reports_blocked_without_legacy_fallback(monkeypatch):
    import app.web_server as web_server
    from app import app as flask_app
    from core.strategy_manager import StrategyManager

    engine = _database()
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(db_helper, 'get_latest_valid_market_date', lambda **kwargs: '2026-10-08')
    monkeypatch.setattr(db_helper, 'get_market_trend', lambda date: 'BULL')
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda: '2026-10-08')
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO daily_recommendations VALUES "
            "('2330','2026-10-08','v31_hybrid',0.9,100,50,1000,'')"
        ))

    response = flask_app.test_client().get(
        f'/api/market/recommendations?strategy={StrategyManager.FUNDAMENTAL_RUNTIME_STRATEGY_ID}&date=2026-10-13'
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['strategy_id'] == StrategyManager.FUNDAMENTAL_RUNTIME_STRATEGY_ID
    assert payload['freshness_status'] == 'blocked'
    assert payload['reason'] == '正式策略尚不可用'
    assert payload['data']['recommendations'] == []


def test_stock_chart_uses_local_history_without_optional_providers(monkeypatch):
    import app.dashboard_payloads as dashboard_payloads

    history = pd.DataFrame([{'trade_date': '2026-10-08', 'stock_id': '2330', 'open_price': 100, 'high_price': 105, 'low_price': 99, 'close_price': 103, 'volume': 1234}])
    monkeypatch.setattr(dashboard_payloads.services, 'get_stock_history', lambda stock_id, limit, end_date: history)
    monkeypatch.setattr(dashboard_payloads.services, 'get_stock_report', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('optional report called')))
    monkeypatch.setattr(dashboard_payloads.services, '_build_market_snapshot', lambda: (_ for _ in ()).throw(AssertionError('optional MCP called')))
    payload = dashboard_payloads._build_dashboard_health_check_payload_local('2330', '2026-10-13', basic_only=True)
    assert payload['quote']['source_date'] == '2026-10-08'
    assert payload['series']['candles'] == [{'time': '2026-10-08', 'open': 100.0, 'high': 105.0, 'low': 99.0, 'close': 103.0}]
    assert payload['status'] == 'degraded'


def test_stock_chart_default_date_does_not_follow_stale_recommendations(monkeypatch):
    import app.dashboard_payloads as dashboard_payloads

    seen = {}
    monkeypatch.setattr(dashboard_payloads.services, '_current_line_date', lambda: '2026-06-26')
    def history(stock_id, limit, end_date):
        seen['end_date'] = end_date
        return pd.DataFrame([{'trade_date': '2026-10-08', 'stock_id': stock_id, 'open_price': 100, 'high_price': 105, 'low_price': 99, 'close_price': 103, 'volume': 1234}])
    monkeypatch.setattr(dashboard_payloads.services, 'get_stock_history', history)
    payload = dashboard_payloads._build_dashboard_health_check_payload_local('2330', basic_only=True)
    assert seen['end_date'] >= '2026-10-08'
    assert payload['as_of_date'] == '2026-10-08'
