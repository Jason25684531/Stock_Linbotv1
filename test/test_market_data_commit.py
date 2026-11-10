"""Safe market-table commits (design D4): date floor, backup receipt, write intent, value reread, crash resolution, restore. SQLite only."""
import json

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from services import market_data_update as mdu


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'market.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE daily_market_data (stock_id TEXT, trade_date TEXT, close_price REAL, volume REAL, rsi REAL)'))
        conn.execute(text("INSERT INTO daily_market_data VALUES ('1101', '2026-11-04', 40.0, 1000, 55.0), ('1101', '2026-11-05', 39.0, 900, 51.0)"))
    return engine


def _prices(day, close=41.0, ids=('1101', '2330')):
    frame = pd.DataFrame({'stock_id': list(ids), 'trade_date': day, 'close_price': close, 'volume': [1200.0] * len(ids)})
    return {'frame': frame, 'rows': len(frame), 'min_rows': 1, 'latest_trade_date': day, 'required_empty': []}


def _service(tmp_path, db, adapter=_prices, **kw):
    committer = mdu.TableCommitter('daily_market_data', ('stock_id', 'trade_date'), engine=lambda: db)
    calendar = lambda day: 'OPEN_OBSERVED'
    return mdu.MarketDataUpdateService(root=tmp_path / 'updates', adapters={'prices_volume': adapter}, committers={'prices_volume': committer},
                                       enforce=lambda name: None, today='2026-11-06', calendar=calendar, **kw)


def _table(db):
    return pd.read_sql(text('SELECT * FROM daily_market_data ORDER BY trade_date, stock_id'), db)


def _row(record):
    return {r['dataset']: r for r in record['results']}['prices_volume']


def test_floor_refuses_research_window_keys(tmp_path, db):
    row = _row(_service(tmp_path, db).run('backfill', dates=['2025-12-31'], datasets=['prices_volume']))
    assert row['status'] == 'COMMIT_FLOOR_VIOLATION' and len(_table(db)) == 2


def test_commit_backs_up_first_records_intent_and_rereads_values(tmp_path, db):
    service = _service(tmp_path, db)
    row = _row(service.run('manual', dates=['2026-11-05'], datasets=['prices_volume']))
    assert row['status'] == 'SUCCESS' and row['committed_rows'] == 2 and row['reread']['match'] is True
    receipt = json.loads((tmp_path / 'updates/backups/daily_market_data/RECEIPT.json').read_text(encoding='utf-8'))
    assert receipt['rows'] == 2 and receipt['sha256']  # the full table before the first commit
    intent = json.loads(next((tmp_path / 'updates/intents').glob('*.json')).read_text(encoding='utf-8'))
    assert intent['status'] == 'COMMITTED' and intent['inserted_keys'] == [['2330', '2026-11-05']]
    after = _table(db)
    assert after[after.trade_date == '2026-11-05'].close_price.tolist() == [41.0, 41.0]


def test_backup_failure_blocks_the_commit(tmp_path, db, monkeypatch):
    monkeypatch.setattr(mdu.TableCommitter, 'export', lambda self: (_ for _ in ()).throw(OSError('disk full')))
    row = _row(_service(tmp_path, db).run('manual', dates=['2026-11-05'], datasets=['prices_volume']))
    assert row['status'] == 'BACKUP_REQUIRED' and _table(db).close_price.tolist() == [40.0, 39.0]


def test_crash_after_commit_is_resolved_by_reread_without_refetch(tmp_path, db, monkeypatch):
    calls = []
    adapter = lambda day: calls.append(day) or _prices(day)
    service = _service(tmp_path, db, adapter=adapter)
    monkeypatch.setattr(mdu.MarketDataUpdateService, '_finish_intent', lambda self, path, status: (_ for _ in ()).throw(SystemExit('crash')))
    with pytest.raises(SystemExit):
        service.run('manual', dates=['2026-11-05'], datasets=['prices_volume'])
    monkeypatch.undo()
    resolved = _service(tmp_path, db).resolve_intents()
    assert [r['status'] for r in resolved] == ['COMMITTED'] and calls == ['2026-11-05']


def test_uncommitted_intent_resolves_to_not_committed(tmp_path, db, monkeypatch):
    service = _service(tmp_path, db)
    monkeypatch.setattr(mdu.TableCommitter, 'write', lambda self, *a: (_ for _ in ()).throw(SystemExit('crash before write')))
    with pytest.raises(SystemExit):
        service.run('manual', dates=['2026-11-05'], datasets=['prices_volume'])
    monkeypatch.undo()
    assert [r['status'] for r in _service(tmp_path, db).resolve_intents()] == ['NOT_COMMITTED']


def test_restore_returns_the_table_to_its_state_before_the_update(tmp_path, db):
    before = _table(db)
    service = _service(tmp_path, db)
    record = service.run('manual', dates=['2026-11-05'], datasets=['prices_volume'])
    restored = service.restore(record['update_id'])
    assert restored == [{'table': 'daily_market_data', 'date': '2026-11-05', 'status': 'RESTORED'}]
    pd.testing.assert_frame_equal(_table(db), before)


def test_commit_authorization_names_the_table(tmp_path, db):
    asked = []
    service = _service(tmp_path, db)
    service._enforce = lambda table: asked.append(table) or (_ for _ in ()).throw(PermissionError('INGESTION_TABLE_NOT_GRANTED'))
    row = _row(service.run('manual', dates=['2026-11-05'], datasets=['prices_volume']))
    assert row['status'] == 'COMMIT_AUTH_BLOCKED' and asked == ['daily_market_data'] and len(_table(db)) == 2


def test_daily_catches_up_from_the_database_skipping_closures_and_unpublished_today(tmp_path, db):
    from datetime import datetime
    seen = []
    adapter = lambda day: seen.append(day) or _prices(day)
    service = _service(tmp_path, db, adapter=adapter, latest_market_date=lambda: '2026-10-21',
                       now=lambda: datetime(2026, 10, 28, 14, 0))  # before 15:00: today is not requested
    service._today = '2026-10-28'
    service.calendar = lambda day: 'CLOSED_OBSERVED' if str(day) in ('2026-10-23', '2026-10-26') else 'OPEN_OBSERVED'
    record = service.run('daily', datasets=['prices_volume'])
    assert record['dates'] == ['2026-10-22', '2026-10-27'] and seen == record['dates']


def test_daily_runs_without_the_schedule_switch_and_caps_the_catch_up(tmp_path, db):
    from datetime import datetime
    service = _service(tmp_path, db, latest_market_date=lambda: '2026-01-02', now=lambda: datetime(2026, 11, 6, 18, 30))
    assert len(service.catch_up_dates()) == mdu.MAX_BACKFILL_DATES and service.catch_up_dates()[0] == '2026-01-05'


def test_rehearsal_runs_the_full_path_with_zero_http_and_untouched_sentinels(tmp_path, monkeypatch):
    from pathlib import Path
    from core.runtime import public_data
    monkeypatch.setattr(public_data, '_send', lambda *a, **k: pytest.fail('a rehearsal never sends HTTP'))
    cache = Path(__file__).resolve().parents[1] / 'artifacts/official_data_recovery_cache/mi_index'
    result = mdu.rehearse(cache, '2026-10-22', root=tmp_path / 'r')
    row = result['record']['results'][0]
    assert result['sentinels_unchanged'] and row['status'] == 'SUCCESS' and row['reread']['match'] is True
    assert result['rows_in_rehearsal_db'] == row['committed_rows'] > 900
    closed = mdu.rehearse(cache, '2026-10-23', root=tmp_path / 'c')
    assert closed['record']['results'][0]['status'] == 'CLOSED_OBSERVED' and closed['rows_in_rehearsal_db'] == 0


def test_single_precision_columns_reread_at_their_wire_precision(tmp_path):
    """MySQL FLOAT comes back with 6 significant digits (monthly_revenue.revenue_yoy 11850.93 -> 11850.9, 2026-11-09 real run);
    only those columns are compared at rtol 1e-5, DOUBLE columns stay at 1e-9."""
    engine = create_engine(f"sqlite:///{tmp_path / 'f.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE monthly_revenue (stock_id TEXT, year INT, month INT, revenue_yoy FLOAT, other DOUBLE)'))
    committer = mdu.TableCommitter('monthly_revenue', ('stock_id', 'year', 'month'), engine=lambda: engine)
    assert committer.single_precision() == {'revenue_yoy'}
    staged = pd.DataFrame({'stock_id': ['1439'], 'year': [2026], 'month': [9], 'revenue_yoy': [11850.93], 'other': [1.0]})
    reread = staged.assign(revenue_yoy=[11850.9])
    keys = committer.keys
    assert not mdu.same_rows(reread, staged, keys)
    assert mdu.same_rows(reread, staged, keys, loose={'revenue_yoy'})
    assert not mdu.same_rows(reread.assign(revenue_yoy=[11851.5]), staged, keys, loose={'revenue_yoy'})
    assert not mdu.same_rows(reread.assign(other=[1.00001]), staged, keys, loose={'revenue_yoy'})


def test_a_refetch_keeps_columns_the_staged_frame_does_not_carry(tmp_path, db):
    """Codex review 2026-11-09 (BLOCKING): delete+insert of an existing key must not wipe columns written by others (run_daily's rsi)."""
    row = _row(_service(tmp_path, db).run('manual', dates=['2026-11-05'], datasets=['prices_volume']))
    after = _table(db).set_index(['stock_id', 'trade_date'])
    assert row['status'] == 'SUCCESS' and after.loc[('1101', '2026-11-05'), 'rsi'] == 51.0 and after.loc[('1101', '2026-11-05'), 'close_price'] == 41.0
    assert pd.isna(after.loc[('2330', '2026-11-05'), 'rsi'])  # a new key has no earlier value to keep


def test_an_unreconciled_intent_blocks_its_unit_in_the_next_run(tmp_path, db):
    """Codex review 2026-11-09 (MAJOR): a PENDING intent whose rows equal neither image stops that dataset and date, nothing is written."""
    service = _service(tmp_path, db)
    service.run('manual', dates=['2026-11-05'], datasets=['prices_volume'])
    intent_path = next((tmp_path / 'updates/intents').glob('*.json'))
    intent = json.loads(intent_path.read_text(encoding='utf-8'))
    intent_path.write_text(json.dumps({**intent, 'status': 'PENDING'}), encoding='utf-8')
    with db.begin() as conn:
        conn.execute(text("UPDATE daily_market_data SET close_price = 99 WHERE trade_date = '2026-11-05'"))
    row = _row(service.run('manual', dates=['2026-11-05'], datasets=['prices_volume'], force=True))
    assert row['status'] == 'RECONCILIATION_REQUIRED' and set(_table(db)[lambda t: t.trade_date == '2026-11-05'].close_price) == {99.0}


def test_restore_takes_the_update_lock_and_the_commit_authorization(tmp_path, db):
    """Codex and Claude reviews 2026-11-09 (MAJOR): restore is a write like any commit."""
    service = _service(tmp_path, db)
    update_id = service.run('manual', dates=['2026-11-05'], datasets=['prices_volume'])['update_id']
    refused = _service(tmp_path, db, ).__class__(root=tmp_path / 'updates', adapters={}, committers=service.committers, today='2026-11-06',
                                                calendar=lambda d: 'OPEN_OBSERVED', enforce=lambda name: (_ for _ in ()).throw(PermissionError('INGESTION_DISABLED')))
    assert refused.restore(update_id)[0]['status'] == 'COMMIT_AUTH_BLOCKED' and set(_table(db)[lambda t: t.trade_date == '2026-11-05'].close_price) == {41.0}
    with mdu.acquire_lock(tmp_path / 'updates'):
        assert service.restore(update_id) == [{'status': 'UPDATE_IN_PROGRESS'}]
    assert service.restore(update_id)[0]['status'] == 'RESTORED'
