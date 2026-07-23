from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine

from core import db_helper


def _memory_engine():
    return create_engine('sqlite:///:memory:')


def test_dashboard_aggregation_cache_returns_fresh_payload():
    engine = _memory_engine()
    payload = {
        'dataset': 'twse_stock_trend',
        'status': 'ok',
        'requested_date': '2026-04-08',
        'as_of_date': '2026-04-08',
        'stock_id': '2330',
        'records': [{'stock_id': '2330', 'trade_date': '2026-04-08'}],
    }

    saved = db_helper.save_dashboard_aggregation_cache(
        'twse_stock_trend',
        payload,
        stock_id='2330',
        market='ALL',
        requested_date='2026-04-08',
        ttl_seconds=300,
        engine=engine,
    )

    cache_entry = db_helper.get_dashboard_aggregation_cache(
        'twse_stock_trend',
        stock_id='2330',
        market='ALL',
        requested_date='2026-04-08',
        engine=engine,
    )

    assert saved is True
    assert cache_entry is not None
    assert cache_entry['cache_status'] == 'fresh'
    assert cache_entry['payload']['stock_id'] == '2330'
    assert cache_entry['payload_version'] == db_helper.DASHBOARD_AGGREGATION_CACHE_VERSION


def test_dashboard_aggregation_cache_refreshes_stale_entry():
    engine = _memory_engine()
    stale_payload = {
        'dataset': 'twse_stock_trend',
        'status': 'ok',
        'requested_date': '2026-04-08',
        'as_of_date': '2026-04-07',
        'stock_id': '2330',
        'records': [{'stock_id': '2330', 'trade_date': '2026-04-07'}],
    }
    fresh_payload = {
        'dataset': 'twse_stock_trend',
        'status': 'ok',
        'requested_date': '2026-04-08',
        'as_of_date': '2026-04-08',
        'stock_id': '2330',
        'records': [{'stock_id': '2330', 'trade_date': '2026-04-08'}],
    }

    db_helper.save_dashboard_aggregation_cache(
        'twse_stock_trend',
        stale_payload,
        stock_id='2330',
        market='ALL',
        requested_date='2026-04-08',
        ttl_seconds=0,
        engine=engine,
    )

    resolved = db_helper.resolve_dashboard_aggregation_cache(
        'twse_stock_trend',
        stock_id='2330',
        market='ALL',
        requested_date='2026-04-08',
        refresh_fn=lambda: fresh_payload,
        ttl_seconds=300,
        engine=engine,
    )

    cache_entry = db_helper.get_dashboard_aggregation_cache(
        'twse_stock_trend',
        stock_id='2330',
        market='ALL',
        requested_date='2026-04-08',
        engine=engine,
    )

    assert resolved is not None
    assert resolved['cache_status'] == 'fresh'
    assert resolved['as_of_date'] == '2026-04-08'
    assert cache_entry is not None
    assert cache_entry['payload']['as_of_date'] == '2026-04-08'


def test_dashboard_aggregation_cache_falls_back_to_stale_when_refresh_fails():
    engine = _memory_engine()
    stale_payload = {
        'dataset': 'market_hotspot',
        'status': 'partial',
        'requested_date': '2026-04-08',
        'as_of_date': '2026-04-07',
        'records': [{'stock_id': '2330', 'trade_date': '2026-04-07'}],
        'warnings': ['initial stale payload'],
    }

    db_helper.save_dashboard_aggregation_cache(
        'market_hotspot',
        stale_payload,
        market='ALL',
        requested_date='2026-04-08',
        ttl_seconds=0,
        engine=engine,
    )

    resolved = db_helper.resolve_dashboard_aggregation_cache(
        'market_hotspot',
        market='ALL',
        requested_date='2026-04-08',
        refresh_fn=lambda: (_ for _ in ()).throw(RuntimeError('upstream unavailable')),
        ttl_seconds=300,
        engine=engine,
    )

    assert resolved is not None
    assert resolved['cache_status'] == 'stale'
    assert resolved['fallback_used'] is True
    assert any('cache refresh failed' in warning for warning in resolved['warnings'])


def test_get_stock_history_allows_more_than_one_year_for_war_room(monkeypatch):
    engine = _memory_engine()
    dates = pd.bdate_range('2025-01-02', periods=300)
    rows = [
        {
            'trade_date': trade_date.strftime('%Y-%m-%d'),
            'stock_id': '2330',
            'open_price': 100 + index,
            'high_price': 102 + index,
            'low_price': 98 + index,
            'close_price': 101 + index,
            'volume': 1000 + index,
        }
        for index, trade_date in enumerate(dates)
    ]
    pd.DataFrame(rows).to_sql('daily_market_data', engine, index=False)
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(
        db_helper,
        'get_table_columns',
        lambda table_name, engine=None, refresh=False: {
            'trade_date',
            'stock_id',
            'open_price',
            'high_price',
            'low_price',
            'close_price',
            'volume',
        },
    )

    history_df = db_helper.get_stock_history('2330', limit=360, end_date='2026-12-31')

    assert len(history_df) == 300
    assert history_df.iloc[0]['trade_date'] == rows[0]['trade_date']


def test_get_stock_history_deduplicates_trade_dates_by_larger_volume(monkeypatch):
    engine = _memory_engine()
    rows = [
        {
            'trade_date': '2026-03-27',
            'stock_id': '2317',
            'open_price': 197.0,
            'high_price': 200.5,
            'low_price': 196.5,
            'close_price': 199.5,
            'volume': 0,
        },
        {
            'trade_date': '2026-03-27',
            'stock_id': '2317',
            'open_price': 197.0,
            'high_price': 200.5,
            'low_price': 196.5,
            'close_price': 199.5,
            'volume': 29525374,
        },
        {
            'trade_date': '2026-03-30',
            'stock_id': '2317',
            'open_price': 194.5,
            'high_price': 195.5,
            'low_price': 193.0,
            'close_price': 194.0,
            'volume': 46503907,
        },
    ]
    pd.DataFrame(rows).to_sql('daily_market_data', engine, index=False)
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(
        db_helper,
        'get_table_columns',
        lambda table_name, engine=None, refresh=False: {
            'trade_date',
            'stock_id',
            'open_price',
            'high_price',
            'low_price',
            'close_price',
            'volume',
        },
    )

    history_df = db_helper.get_stock_history('2317', limit=10, end_date='2026-03-30')

    assert history_df['trade_date'].tolist() == ['2026-03-27', '2026-03-30']
    assert history_df.loc[history_df['trade_date'] == '2026-03-27', 'volume'].item() == 29525374
