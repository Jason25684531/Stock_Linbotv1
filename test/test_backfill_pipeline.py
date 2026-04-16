import pandas as pd

from jobs import update_database
from jobs import backfill_pipeline


def _fake_calendar_frame(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({'Close': [1.0] * len(dates)}, index=pd.to_datetime(dates))


def test_update_market_date_skips_when_rows_too_few(monkeypatch):
    sparse_df = pd.DataFrame([
        {'stock_id': f'{1000 + idx}', 'close_price': 10.0, 'volume': 1000}
        for idx in range(50)
    ])
    process_calls = []

    monkeypatch.setattr(update_database, 'fetch_market_data_with_mcp', lambda client, date_str: (sparse_df.copy(), []))
    monkeypatch.setattr(update_database, 'enrich_with_margin_balance', lambda df, date_str: (df, None))
    monkeypatch.setattr(update_database, 'process_and_save', lambda df, date_str, engine: process_calls.append(date_str) or len(df))

    result = update_database.update_market_date(object(), '2026-04-15', mcp_client=object())

    assert result == 0
    assert process_calls == []


def test_scan_pipeline_gaps_detects_market_and_recommendation_holes(monkeypatch):
    monkeypatch.setattr(backfill_pipeline, 'get_valid_market_dates', lambda start_date=None, end_date=None: ['2026-03-27', '2026-03-31'])
    monkeypatch.setattr(
        backfill_pipeline,
        'get_recommendation_dates',
        lambda start_date=None, end_date=None, include_heartbeats=True: ['2026-03-27'],
    )
    monkeypatch.setattr(
        backfill_pipeline.yf,
        'download',
        lambda *args, **kwargs: _fake_calendar_frame(['2026-03-27', '2026-03-30', '2026-03-31']),
    )

    gaps = backfill_pipeline.scan_pipeline_gaps('2026-03-27', '2026-03-31')

    assert gaps['missing_market_dates'] == ['2026-03-30']
    assert gaps['missing_recommendation_dates'] == ['2026-03-31']


def test_scan_pipeline_gaps_excludes_exchange_holidays(monkeypatch):
    monkeypatch.setattr(backfill_pipeline, 'get_valid_market_dates', lambda start_date=None, end_date=None: ['2026-04-02', '2026-04-07'])
    monkeypatch.setattr(
        backfill_pipeline,
        'get_recommendation_dates',
        lambda start_date=None, end_date=None, include_heartbeats=True: ['2026-04-02', '2026-04-07'],
    )
    monkeypatch.setattr(
        backfill_pipeline.yf,
        'download',
        lambda *args, **kwargs: _fake_calendar_frame(['2026-04-02', '2026-04-07']),
    )

    gaps = backfill_pipeline.scan_pipeline_gaps('2026-04-02', '2026-04-07')

    assert gaps['missing_market_dates'] == []
    assert gaps['excluded_non_trading_dates'] == ['2026-04-03', '2026-04-06']
    assert gaps['missing_recommendation_dates'] == []


def test_scan_pipeline_gaps_falls_back_when_calendar_unavailable(monkeypatch):
    monkeypatch.setattr(backfill_pipeline, 'get_valid_market_dates', lambda start_date=None, end_date=None: ['2026-03-27', '2026-03-31'])
    monkeypatch.setattr(
        backfill_pipeline,
        'get_recommendation_dates',
        lambda start_date=None, end_date=None, include_heartbeats=True: ['2026-03-27'],
    )
    monkeypatch.setattr(backfill_pipeline.yf, 'download', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('calendar down')))
    monkeypatch.setattr(backfill_pipeline, '_is_exchange_trading_day', lambda date_str, mcp_client=None: True)

    gaps = backfill_pipeline.scan_pipeline_gaps('2026-03-27', '2026-03-31')

    assert gaps['missing_market_dates'] == ['2026-03-30']
    assert gaps['excluded_non_trading_dates'] == []
    assert gaps['unverified_dates'] == []


def test_backfill_pipeline_repairs_missing_dates(monkeypatch):
    market_sequences = iter([
        ['2026-03-27', '2026-03-31'],
        ['2026-03-27', '2026-03-30', '2026-03-31'],
        ['2026-03-27', '2026-03-30', '2026-03-31'],
    ])
    recommendation_sequences = iter([
        ['2026-03-27'],
        ['2026-03-27'],
        ['2026-03-27', '2026-03-30', '2026-03-31'],
    ])
    updated_market_dates = []
    rebuilt_recommendation_dates = []

    monkeypatch.setattr(backfill_pipeline, 'get_db_engine', lambda: object())
    monkeypatch.setattr(backfill_pipeline, 'get_valid_market_dates', lambda start_date=None, end_date=None: next(market_sequences))
    monkeypatch.setattr(
        backfill_pipeline,
        'get_recommendation_dates',
        lambda start_date=None, end_date=None, include_heartbeats=True: next(recommendation_sequences),
    )
    monkeypatch.setattr(
        backfill_pipeline.yf,
        'download',
        lambda *args, **kwargs: _fake_calendar_frame(['2026-03-27', '2026-03-30', '2026-03-31']),
    )
    monkeypatch.setattr(
        backfill_pipeline,
        'update_market_date',
        lambda engine, date_str, mcp_client=None: updated_market_dates.append(date_str) or 120,
    )
    monkeypatch.setattr(
        backfill_pipeline,
        'run_daily_for_date',
        lambda date_str: rebuilt_recommendation_dates.append(date_str) or {'date': date_str},
    )

    summary = backfill_pipeline.backfill_pipeline('2026-03-27', '2026-03-31', dry_run=False)

    assert updated_market_dates == ['2026-03-30']
    assert rebuilt_recommendation_dates == ['2026-03-30', '2026-03-31']
    assert summary['remaining_market_dates'] == []
    assert summary['remaining_recommendation_dates'] == []