import pandas as pd
import pytest

from core import db_helper
from jobs.report_market_data_integrity import run_integrity_audit
from jobs import push_to_line
from jobs.update_database import merge_market_data, process_and_save


def test_merge_market_data_keeps_authoritative_source_date():
    source = pd.DataFrame({
        'stock_id': ['2330'],
        'trade_date': ['2026-10-08'],
        'close_price': [100],
    })

    merged = merge_market_data(source, pd.DataFrame(), '2026-10-08')

    assert merged['trade_date'].tolist() == ['2026-10-08']


def test_merge_market_data_rejects_stale_source_date_instead_of_relabeling():
    source = pd.DataFrame({
        'stock_id': ['2330'],
        'trade_date': ['2026-06-26'],
        'close_price': [100],
    })

    with pytest.raises(ValueError, match='source date does not match'):
        merge_market_data(source, pd.DataFrame(), '2026-10-08')


def test_merge_market_data_does_not_invent_missing_source_date():
    source = pd.DataFrame({'stock_id': ['2330'], 'close_price': [100]})

    assert merge_market_data(source, pd.DataFrame(), '2026-10-08').empty


def test_normalize_date_str_rejects_trailing_garbage_and_invalid_dates():
    assert db_helper.normalize_date_str('2026-10-08garbage') is None
    assert db_helper.normalize_date_str('2026-02-30') is None
    assert db_helper.normalize_date_str('2026-10-08T13:45:00') == '2026-10-08'


def test_merge_market_data_rejects_malformed_source_date():
    source = pd.DataFrame({
        'stock_id': ['2330'],
        'trade_date': ['2026-10-08garbage'],
        'close_price': [100],
    })

    with pytest.raises(ValueError, match='source date does not match'):
        merge_market_data(source, pd.DataFrame(), '2026-10-08')


def test_process_and_save_rejects_warrants_but_keeps_supported_etfs(monkeypatch):
    captured = []
    monkeypatch.setattr(db_helper, 'upsert_stock_data', lambda frame, _table: captured.extend(frame.stock_id) or len(frame))
    frame = pd.DataFrame({
        'stock_id': ['2330', '0050', '006208', '123456', '23301'],
        'trade_date': ['2026-10-08'] * 5,
        'close_price': [100] * 5,
    })

    assert process_and_save(frame, '2026-10-08', engine=None) == 3
    assert captured == ['2330', '0050', '006208']


def test_process_and_save_rejects_weekend_snapshot(monkeypatch):
    monkeypatch.setattr(
        db_helper,
        'upsert_stock_data',
        lambda *_args, **_kwargs: pytest.fail('weekend market snapshots must not be written'),
    )
    frame = pd.DataFrame({
        'stock_id': ['2330'],
        'trade_date': ['2026-10-10'],
        'close_price': [100],
    })

    with pytest.raises(ValueError, match='business weekday'):
        process_and_save(frame, '2026-10-10', engine=None)


def test_merge_market_data_rejects_mixed_snapshot_dates():
    source = pd.DataFrame({
        'stock_id': ['2330', '2317'],
        'trade_date': ['2026-10-08', '2026-06-26'],
        'close_price': [100, 200],
    })

    with pytest.raises(ValueError, match='source date does not match'):
        merge_market_data(source, pd.DataFrame(), '2026-10-08')


def test_market_upsert_is_retry_safe_without_relying_on_a_unique_index(monkeypatch):
    from sqlalchemy import create_engine, text

    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE daily_market_data ('
            'stock_id TEXT NOT NULL, trade_date TEXT NOT NULL, close_price REAL NOT NULL)'
        ))
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    batch = pd.DataFrame({
        'stock_id': ['2330', '2330'],
        'trade_date': ['2026-10-08', '2026-10-08'],
        'close_price': [100, 101],
    })

    assert db_helper.upsert_stock_data(batch) == 1
    assert db_helper.upsert_stock_data(batch) == 1
    with engine.connect() as conn:
        rows = conn.execute(text(
            'SELECT stock_id, trade_date, close_price FROM daily_market_data'
        )).all()
    assert rows == [('2330', '2026-10-08', 101)]


def test_market_upsert_propagates_write_errors(monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError

    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE daily_market_data ('
            'stock_id TEXT NOT NULL, trade_date TEXT NOT NULL, close_price REAL NOT NULL)'
        ))
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    batch = pd.DataFrame({'stock_id': ['2330'], 'trade_date': ['2026-10-08']})

    with pytest.raises(IntegrityError):
        db_helper.upsert_stock_data(batch)


def test_read_only_integrity_report_includes_invalid_keys_and_does_not_mutate():
    from sqlalchemy import create_engine, text

    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE daily_market_data (stock_id TEXT, trade_date TEXT)'))
        conn.execute(text('CREATE TABLE daily_recommendations (stock_id TEXT, trade_date TEXT)'))
        conn.execute(text(
            "INSERT INTO daily_market_data VALUES "
            "('2330', '2026-10-08'), ('2330', '2026-10-08'), ('123', '2026-10-10')"
        ))
        conn.execute(text("INSERT INTO daily_recommendations VALUES ('2330', '2026-06-26')"))

    report = run_integrity_audit(engine, sample_limit=5)

    assert report['read_only'] is True
    assert report['market_data']['latest_date'] == '2026-10-10'
    assert report['market_data']['latest_date_rows'] == 1
    assert report['recommendations']['latest_date'] == '2026-06-26'
    assert report['duplicate_key_group_count'] == 1
    assert report['weekend_row_count'] == 1
    assert report['invalid_identifier_length_row_count'] == 1
    assert report['invalid_identifier_length_rows_by_length'] == {'3': 1}
    assert report['invalid_identifier_length_key_samples'] == [{'stock_id': '123', 'trade_date': '2026-10-10'}]
    with engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM daily_market_data')).scalar() == 3
        assert conn.execute(text('SELECT COUNT(*) FROM daily_recommendations')).scalar() == 1


def test_line_push_blocks_before_delivery_when_recommendations_are_stale(monkeypatch):
    calls = []
    monkeypatch.setattr(push_to_line, '_recommendation_freshness_issue', lambda: (True, '2026-10-08', '2026-06-26'))
    monkeypatch.setattr(push_to_line, 'record_pipeline_step_start', lambda **kwargs: None)
    monkeypatch.setattr(push_to_line, 'record_pipeline_step_finish', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(push_to_line, 'run_evening', lambda **kwargs: pytest.fail('stale push must not start'))

    assert push_to_line.main(['--time', 'evening']) == 2
    assert calls[0]['status'] == 'blocked'
    assert calls[0]['trade_date'] == '2026-10-08'
    assert '2026-06-26' in calls[0]['error_summary']


def test_line_freshness_uses_latest_valid_market_date(monkeypatch):
    seen = []
    monkeypatch.setattr(push_to_line, '_current_push_request_date', lambda: '2026-10-13')
    monkeypatch.setattr(push_to_line, 'get_latest_valid_market_date', lambda **kwargs: seen.append(kwargs) or '2026-10-08')
    monkeypatch.setattr(push_to_line, 'get_actual_latest_date', lambda: '2026-10-08')

    assert push_to_line._recommendation_freshness_issue() == (False, '2026-10-08', '2026-10-08')
    assert seen == [{'end_date': '2026-10-13'}]


def test_recommendations_api_does_not_use_market_date_as_missing_source_date(monkeypatch):
    import app.web_server as web_server_module
    import app as app_module
    from app import app as flask_app

    monkeypatch.setattr(web_server_module, '_latest_ui_market_date', lambda: '2026-10-08')
    monkeypatch.setattr(
        app_module,
        'get_recommendations_with_market_fallback',
        lambda **kwargs: (pd.DataFrame(), {
            'recommendation_date': '2026-10-08',
            'last_available_recommendation_date': None,
            'fallback_used': False,
            'fallback_too_old': False,
            'resolution_source': 'missing',
        }),
    )

    payload = flask_app.test_client().get('/api/market/recommendations').get_json()

    assert payload['as_of_date'] == '2026-10-08'
    assert payload['recommendation_date'] is None
    assert payload['status'] == 'empty'
