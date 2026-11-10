import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from services import market_data_update as mdu
from services.market_data_update import MarketDataUpdateService, acquire_lock, load_schedule, save_schedule

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2025, 3, 5)  # Wednesday


def ok(rows=500, latest='2025-03-05', **extra):
    return lambda day: {'rows': rows, 'latest_trade_date': latest, 'required_empty': [], 'min_rows': 100, **extra}


class Counter:
    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    def __call__(self, day):
        self.calls += 1
        return self.fn(day)


def verified_calendar(day):
    """A calendar whose year schedule is imported: weekdays open, weekends closed (keeps tests off the real cache)."""
    return 'CLOSED_SCHEDULED' if mdu._as_date(day).weekday() >= 5 else 'EXPECTED_OPEN'


def service(tmp_path, adapters, sleeps=None, calendar=verified_calendar, **kwargs):
    sleeps = [] if sleeps is None else sleeps
    kwargs.setdefault('commit_floor', None)  # these fixtures use 2025 sessions; the G1 floor has its own tests (test_market_data_commit.py)
    return MarketDataUpdateService(root=tmp_path, adapters=adapters, sleep=sleeps.append, today=TODAY, calendar=calendar, **kwargs)


def by_dataset(result):
    return {r['dataset']: r for r in result['results']}


def test_lock_refuses_concurrent_run_without_calling_adapters(tmp_path):
    prices = Counter(ok())
    with acquire_lock(tmp_path):
        result = service(tmp_path, {'prices_volume': prices}).run('manual', datasets=['prices_volume'])
    assert result['status'] == 'UPDATE_IN_PROGRESS'
    assert prices.calls == 0
    assert not list(tmp_path.glob('[0-9]*.json'))
    assert not (tmp_path / '.lock').exists()


def test_stale_lock_with_dead_pid_is_removed(tmp_path, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    (tmp_path / '.lock').write_text(f'999999\n{old}\n', encoding='utf-8')
    monkeypatch.setattr(mdu, '_pid_alive', lambda pid: False)
    result = service(tmp_path, {'prices_volume': ok()}).run('manual', datasets=['prices_volume'])
    assert result['status'] == 'COMPLETED'


def test_recent_lock_or_live_pid_is_kept(tmp_path, monkeypatch):
    lock = tmp_path / '.lock'
    lock.write_text(f'999999\n{datetime.now(timezone.utc).isoformat()}\n', encoding='utf-8')
    monkeypatch.setattr(mdu, '_pid_alive', lambda pid: False)
    assert service(tmp_path, {'prices_volume': ok()}).run('manual')['status'] == 'UPDATE_IN_PROGRESS'
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    lock.write_text(f'{os.getpid()}\n{old}\n', encoding='utf-8')
    monkeypatch.setattr(mdu, '_pid_alive', lambda pid: True)
    assert service(tmp_path, {'prices_volume': ok()}).run('manual')['status'] == 'UPDATE_IN_PROGRESS'
    assert lock.exists()


def test_retry_then_isolated_failure(tmp_path):
    def broken(day):
        raise ConnectionError('TWT49U down')

    actions, sleeps = Counter(broken), []
    result = service(tmp_path, {'prices_volume': ok(), 'corporate_actions': actions}, sleeps).run(
        'manual', datasets=['corporate_actions', 'prices_volume'])
    rows = by_dataset(result)
    assert rows['corporate_actions']['status'] == 'FAILED'
    assert rows['corporate_actions']['attempts'] == 3 and actions.calls == 3
    assert len(rows['corporate_actions']['errors']) == 3
    assert sleeps == [2, 4]
    assert rows['prices_volume']['status'] == 'SUCCESS'
    assert result['status'] == 'COMPLETED_WITH_ERRORS'
    log = json.loads((tmp_path / f"{result['update_id']}.json").read_text(encoding='utf-8'))
    assert log['mode'] == 'manual' and log['dates'] == ['2025-03-05'] and 'duration_seconds' in log


def test_retry_recovers_on_second_attempt(tmp_path):
    calls = []

    def flaky(day):
        calls.append(day)
        if len(calls) == 1:
            raise TimeoutError('slow')
        return {'rows': 300, 'latest_trade_date': day, 'min_rows': 100}

    result = service(tmp_path, {'prices_volume': flaky}).run('manual', datasets=['prices_volume'])
    row = by_dataset(result)['prices_volume']
    assert row['status'] == 'SUCCESS' and row['attempts'] == 2


def test_idempotent_rerun_skips_and_force_reruns(tmp_path):
    prices = Counter(ok())
    svc = service(tmp_path, {'prices_volume': prices})
    first = svc.run('backfill', dates=['2025-03-04'], datasets=['prices_volume'])
    second = svc.run('backfill', dates=['2025-03-04'], datasets=['prices_volume'])
    assert by_dataset(first)['prices_volume']['status'] == 'SUCCESS'
    assert by_dataset(second)['prices_volume']['status'] == 'SKIPPED_ALREADY_SUCCESS'
    assert prices.calls == 1
    forced = svc.run('backfill', dates=['2025-03-04'], datasets=['prices_volume'], force=True)
    assert by_dataset(forced)['prices_volume']['status'] == 'SUCCESS' and prices.calls == 2
    assert len(svc.history()) == 3


def test_integrity_invalid_is_not_recorded_as_success(tmp_path):
    prices = Counter(ok(rows=10))
    svc = service(tmp_path, {'prices_volume': prices, 'benchmark': ok(rows=1, required_empty=['taiex_close'])})
    result = svc.run('manual', datasets=['prices_volume', 'benchmark'])
    rows = by_dataset(result)
    assert rows['prices_volume']['status'] == 'INTEGRITY_INVALID'
    assert rows['benchmark']['status'] == 'INTEGRITY_INVALID'
    svc.run('manual', datasets=['prices_volume'])
    assert prices.calls == 2


def test_stale_reports_expected_and_actual_dates(tmp_path):
    result = service(tmp_path, {'prices_volume': ok(latest='2025-03-03')}).run('manual', datasets=['prices_volume'])
    row = by_dataset(result)['prices_volume']
    assert row['status'] == 'STALE'
    assert row['checks']['expected_latest_trade_date'] == '2025-03-05'
    assert row['checks']['latest_trade_date'] == '2025-03-03'
    assert 'trading_calendar' in row['checks']['note']


def test_weekend_expected_date_is_previous_friday(tmp_path):
    svc = MarketDataUpdateService(root=tmp_path, adapters={'prices_volume': ok(latest='2025-03-07')}, sleep=lambda s: None,
                                  today=date(2025, 3, 9))
    result = svc.run('manual', datasets=['prices_volume'])
    assert result['dates'] == ['2025-03-07']
    assert by_dataset(result)['prices_volume']['status'] == 'SUCCESS'


def test_pit_unverified_for_revenue_and_financials(tmp_path):
    adapters = {'monthly_revenue': ok(latest=None, has_announcement_date=False),
                'financials': ok(latest=None, has_announcement_date=None)}
    result = service(tmp_path, adapters).run('manual', datasets=['monthly_revenue', 'financials'])
    rows = by_dataset(result)
    assert rows['monthly_revenue']['status'] == 'PIT_UNVERIFIED'
    assert rows['financials']['status'] == 'PIT_UNVERIFIED'
    assert result['status'] == 'COMPLETED'
    verified = service(tmp_path, {'financials': ok(latest=None, has_announcement_date=True)}).run(
        'manual', datasets=['financials'], force=True)
    assert by_dataset(verified)['financials']['status'] == 'SUCCESS'


def test_not_covered_and_auth_blocked_reporting(tmp_path):
    def denied(day):
        raise PermissionError('download_market_data: no grant')

    adapters = {'corporate_actions': Counter(denied),
                'benchmark_tri': lambda day: {'status': 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'reason': 'no local file'},
                'margin': lambda day: {'status': 'COVERED_BY', 'covered_by': 'prices_volume'}}
    result = service(tmp_path, adapters).run('manual', datasets=['corporate_actions', 'benchmark_tri', 'margin', 'financials'])
    rows = by_dataset(result)
    assert rows['corporate_actions']['status'] == 'IMPLEMENTED_BUT_AUTH_BLOCKED'
    assert rows['corporate_actions']['attempts'] == 1 and adapters['corporate_actions'].calls == 1
    assert rows['benchmark_tri']['status'] == 'IMPLEMENTED_BUT_AUTH_BLOCKED'
    assert rows['margin']['status'] == 'COVERED_BY'
    assert rows['financials']['status'] == 'NOT_COVERED'
    gaps = {gap['item']: gap['status'] for gap in result['coverage_gaps']}
    assert gaps == {'splits': 'NOT_COVERED', 'capital_reductions': 'NOT_COVERED'}


def test_daily_ignores_the_schedule_switch_and_catches_up(tmp_path):
    """The trusted ingestion grant is the switch (design D4); schedule.json no longer blocks daily mode."""
    prices = Counter(ok())
    svc = service(tmp_path, {'prices_volume': prices}, latest_market_date=lambda: '2025-03-04')
    assert load_schedule(tmp_path)['enabled'] is False
    result = svc.run('daily', datasets=['prices_volume'])
    assert result['status'] == 'COMPLETED' and result['dates'] == ['2025-03-05'] and prices.calls == 1


def test_schedule_never_verifies_os_task(tmp_path):
    saved = save_schedule(tmp_path, True, '18:30')
    assert saved == {'enabled': True, 'time': '18:30', 'os_task_verified': False}
    assert service(tmp_path, {}).status()['scheduler_state'] == 'SCHEDULER_SETUP_REQUIRED'
    with pytest.raises(ValueError):
        save_schedule(tmp_path, True, '25:99')
    (tmp_path / 'schedule.json').write_text(json.dumps({'enabled': True, 'time': '18:30', 'os_task_verified': True}))
    assert service(tmp_path, {}).status()['scheduler_state'] == 'OS_TASK_VERIFIED'
    assert save_schedule(tmp_path, False, '19:00')['os_task_verified'] is True
    assert load_schedule(tmp_path)['enabled'] is False


@pytest.mark.parametrize('dates, code', [
    (['2025/03/04'], 'INVALID_DATE_FORMAT'),
    (['2025-3-4'], 'INVALID_DATE_FORMAT'),
    (['2025-03-06'], 'FUTURE_DATE'),
    ([], 'DATES_REQUIRED'),
    ([(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(32)], 'TOO_MANY_DATES'),
])
def test_backfill_validation(tmp_path, dates, code):
    prices = Counter(ok())
    with pytest.raises(ValueError, match=code):
        service(tmp_path, {'prices_volume': prices}).run('backfill', dates=dates)
    assert prices.calls == 0


def test_backfill_accepts_31_dates_and_unknown_dataset_is_refused(tmp_path):
    dates = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(31)]
    result = service(tmp_path, {'margin': lambda day: {'status': 'COVERED_BY'}}).run('backfill', dates=dates, datasets=['margin'])
    assert len(result['results']) == 31
    with pytest.raises(ValueError, match='UNKNOWN_DATASETS'):
        service(tmp_path, {}).run('manual', datasets=['line_push'])


def test_status_and_history(tmp_path):
    svc = service(tmp_path, {'prices_volume': ok(), 'benchmark': ok(rows=1, latest='2025-03-03')})
    svc.run('manual', datasets=['prices_volume', 'benchmark'])
    status = svc.status()
    assert status['latest_trade_date'] == '2025-03-05'
    assert status['datasets']['prices_volume']['status'] == 'SUCCESS'
    assert [r['dataset'] for r in status['attention']] == ['benchmark']
    assert status['update_in_progress'] is False
    assert len(svc.history(limit=5)) == 1


def test_import_graph_is_clean():
    code = (
        'import sys\n'
        'import services.market_data_update\n'
        "bad = ('line_bot', 'line_flows', 'line_message_builder', 'push_to_line', 'run_daily', 'broker', 'recommendation')\n"
        'hits = sorted(m for m in sys.modules if any(b in m for b in bad))\n'
        "heavy = sorted(m for m in ('sqlalchemy', 'pandas', 'core.db_helper', 'jobs.update_database', 'config') if m in sys.modules)\n"
        'print(hits, heavy)\n'
        'assert not hits and not heavy, (hits, heavy)\n'
    )
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_service_never_writes_pipeline_runs(tmp_path, monkeypatch):
    """jobs/update_database.py owns the update_database pipeline_runs rows (design D4); the service writes none."""
    import core.db_helper as db
    calls = []
    monkeypatch.setattr(db, 'record_pipeline_step_start', lambda **kw: calls.append(kw))
    monkeypatch.setattr(db, 'record_pipeline_step_finish', lambda **kw: calls.append(kw))
    service(tmp_path, {'prices_volume': ok()}).run('manual', datasets=['prices_volume'])
    assert calls == []


# ---------------------------------------------------------------- change 2026-11-09-autonomous-data-readiness-v1 (design D6, D7)

def test_precheck_contexts_equal_the_real_requests():
    from core.research.sources import twse
    from core.runtime import public_data
    contexts = mdu.precheck('corporate_actions', '2025-03-05', gate=lambda ctx: None)
    real = public_data.context(twse.TWT49U_URL, params={'startDate': '20250305', 'endDate': '20250305', 'response': 'json'})
    assert [c.key for c in contexts] == [real.key]
    # the MCP server builds the MI_INDEX URL with a query string; the benchmark adapter passes params: both are one request identity
    mcp_style = public_data.context(mdu.endpoints('prices_volume', '2025-03-05', 'TWSE')[0]['url'])
    assert mcp_style.key == mdu.precheck('benchmark', '2025-03-05', gate=lambda ctx: None)[0].key
    revenue = mdu.precheck('monthly_revenue', '2025-03-05', gate=lambda ctx: None)
    assert {c.dataset for c in revenue} == {'monthly_revenue'} and len(revenue) == 8  # Feb and Jan x sii and otc x '2' and '02' spellings
    assert [dict(c.parameters)['TYPEK'] for c in mdu.precheck('financials', '2025-03-05', gate=lambda ctx: None, market='ALL')] == ['sii', 'otc']


def test_restricted_endpoint_and_denied_budget_block_before_fetch():
    with pytest.raises(mdu.EndpointBlocked) as blocked:
        mdu.precheck('prices_volume', '2025-03-05', gate=lambda ctx: None, market='ALL')
    assert sum(r.startswith('RESTRICTED_SOURCE_REQUIRES_SEPARATE_AUTHORIZATION:tpex') for r in blocked.value.reasons) == 3

    def deny(ctx):
        raise PermissionError('download_market_data: BudgetDenied: BUDGET_UNASSIGNED: no approved fixed request plan for this purpose')
    fetched = []
    run = mdu.guarded('corporate_actions', fetched.append, check=lambda n, d: mdu.precheck(n, d, gate=deny), audit=set)
    with pytest.raises(mdu.EndpointBlocked) as denied:
        run('2025-03-05')
    assert denied.value.reasons[0].startswith('BUDGET_UNASSIGNED:') and fetched == []


def test_unexpected_audited_request_is_source_scope_mismatch(tmp_path):
    audits = iter([{(1, 'expected')}, {(1, 'expected'), (2, 'other-request')}])
    run = mdu.guarded('benchmark', lambda day: {'rows': 1}, check=lambda n, d: [], audit=lambda: next(audits))
    assert run('2025-03-05') == {'status': 'SOURCE_SCOPE_MISMATCH', 'unexpected_request_keys': ['other-request']}
    svc = service(tmp_path, {'benchmark': lambda day: {'status': 'SOURCE_SCOPE_MISMATCH'}})
    assert by_dataset(svc.run('manual', datasets=['benchmark']))['benchmark']['status'] == 'SOURCE_SCOPE_MISMATCH'


def _prices(day, rows=150, duplicate=False):
    import pandas as pd
    ids = [str(1000 + i) for i in range(rows)]
    frame = pd.DataFrame({'stock_id': ids + ids[:1] * duplicate, 'trade_date': day, 'close_price': 10.0})
    return {'frame': frame, 'rows': len(frame), 'min_rows': 100, 'latest_trade_date': day, 'required_empty': []}


def test_commit_only_after_write_authorization_and_reread(tmp_path):
    commits = []
    committers = {'prices_volume': (lambda frame, day: commits.append(frame) or len(frame), lambda frame, day: len(frame))}

    def denied(name):
        raise PermissionError('write_production_db: write_production_db is a Production operation; mode is research')
    blocked = by_dataset(service(tmp_path / 'a', {'prices_volume': _prices}, committers=committers, enforce=denied).run('manual', datasets=['prices_volume']))
    row = blocked['prices_volume']
    assert row['status'] == 'COMMIT_AUTH_BLOCKED' and commits == [] and Path(row['staging']['path']).is_file()
    assert row['staging']['sha256'] == mdu._file_sha(row['staging']['path']) and not (tmp_path / 'a' / 'state.json').exists()
    done = by_dataset(service(tmp_path / 'b', {'prices_volume': _prices}, committers=committers, enforce=lambda name: None).run('manual', datasets=['prices_volume']))
    assert done['prices_volume']['status'] == 'SUCCESS' and done['prices_volume']['reread_rows'] == 150 and len(commits[0]) == 150
    short = {'prices_volume': (lambda frame, day: len(frame), lambda frame, day: 3)}
    failed = by_dataset(service(tmp_path / 'c', {'prices_volume': _prices}, committers=short, enforce=lambda name: None).run('manual', datasets=['prices_volume']))
    assert failed['prices_volume']['status'] == 'COMMIT_VERIFY_FAILED'


def test_staged_file_changed_before_commit_is_refused(tmp_path):
    def tamper(name):
        for path in (tmp_path / 'staging').rglob('*.parquet'):
            path.write_bytes(path.read_bytes() + b'x')
    commits = []
    committers = {'prices_volume': (lambda frame, day: commits.append(1), lambda frame, day: 0)}
    row = by_dataset(service(tmp_path, {'prices_volume': _prices}, committers=committers, enforce=tamper).run('manual', datasets=['prices_volume']))['prices_volume']
    assert row['status'] == 'STAGING_HASH_MISMATCH' and commits == []


def test_duplicate_keys_never_commit(tmp_path):
    """Duplicate primary keys stop the commit for every SPLIT dataset (monthly revenue is SPLIT since design D4)."""
    import pandas as pd
    commits = []
    committers = {name: (lambda frame, day: commits.append(1), lambda frame, day: 10**6) for name in ('prices_volume', 'monthly_revenue')}
    revenue = pd.DataFrame({'stock_id': ['1101', '1101'], 'year': [2025, 2025], 'month': [2, 2], 'revenue': [1.0, 2.0]})
    adapters = {'prices_volume': lambda day: _prices(day, duplicate=True),
                'monthly_revenue': lambda day: {'frame': revenue, 'rows': 2, 'latest_trade_date': None, 'has_announcement_date': True, 'required_empty': []}}
    rows = by_dataset(service(tmp_path, adapters, committers=committers, enforce=lambda name: None).run('manual', datasets=list(adapters)))
    for name in adapters:
        assert rows[name]['status'] == 'INTEGRITY_INVALID' and 'PRIMARY_KEY_DUPLICATES:1' in rows[name]['validation']
    assert commits == []


def test_schema_drift_is_not_retried(tmp_path):
    class SchemaDriftError(Exception):
        pass

    def drift(day):
        raise SchemaDriftError('TWT49U required fields are missing')
    calls = Counter(drift)
    row = by_dataset(service(tmp_path, {'corporate_actions': calls}).run('manual', datasets=['corporate_actions']))['corporate_actions']
    assert row['status'] == 'SCHEMA_DRIFT' and calls.calls == 1


def test_unverified_calendar_gives_freshness_unknown_and_closed_days_skip(tmp_path):
    unverified = service(tmp_path / 'u', {'prices_volume': ok(latest='2025-03-03')}, calendar=lambda day: 'CALENDAR_UNVERIFIED')
    row = by_dataset(unverified.run('manual', datasets=['prices_volume']))['prices_volume']
    assert row['status'] == 'FRESHNESS_UNKNOWN' and row['checks']['stale'] is False
    save_schedule(tmp_path / 'h', True, '17:00')
    holiday = service(tmp_path / 'h', {'prices_volume': Counter(ok())}, calendar=lambda day: 'CLOSED_SCHEDULED')
    assert holiday.run('daily')['status'] == 'SKIPPED_NON_BUSINESS_DAY'


def test_verify_scheduled_requires_a_scheduled_trigger(tmp_path, monkeypatch):
    svc = service(tmp_path, {name: ok() for name in mdu.DATASETS})
    svc.run('manual')
    assert 'TRIGGER_NOT_SCHEDULED' in svc.verify_scheduled()['problems']
    monkeypatch.setenv('STOCK_LINBOT_TRIGGER', 'scheduled')
    svc.run('manual', force=True)
    result = svc.verify_scheduled()
    assert result['status'] == 'VERIFIED' and result['problems'] == [] and result['session'] == '2025-03-05'


def test_cli_daily_against_an_isolated_root(tmp_path):
    root = tmp_path / 'isolated_root'
    env = {**os.environ, 'STOCK_LINBOT_UPDATE_ROOT': str(root)}
    done = subprocess.run([sys.executable, '-m', 'services.market_data_update', '--daily'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and json.loads(done.stdout)['status'] == 'SKIPPED_INGESTION_DISABLED'
    assert not root.exists()  # without a trusted ingestion grant a daily run reads, fetches and writes nothing, not even the root


def test_scheduled_run_that_updated_nothing_fails_acceptance(tmp_path, monkeypatch):
    monkeypatch.setenv('STOCK_LINBOT_TRIGGER', 'scheduled')
    blocked = {name: (lambda day: {'status': 'IMPLEMENTED_BUT_AUTH_BLOCKED'}) for name in mdu.DATASETS}
    svc = service(tmp_path, blocked)
    svc.run('manual')
    result = svc.verify_scheduled()
    assert result['status'] == 'FAILED'
    assert sorted(p.split(':')[1] for p in result['problems'] if p.startswith('DATASET_NOT_UPDATED')) == sorted(mdu.REQUIRED_DAILY)


def test_commit_exception_is_reported_and_the_run_continues(tmp_path):
    def broken(frame, day):
        raise RuntimeError('Market schema missing nullable columns')
    committers = {'prices_volume': (broken, lambda frame, day: 0)}
    adapters = {'prices_volume': _prices, 'benchmark': ok(rows=1)}
    result = service(tmp_path, adapters, committers=committers, enforce=lambda name: None).run('manual', datasets=list(adapters))
    rows = by_dataset(result)
    assert rows['prices_volume']['status'] == 'COMMIT_FAILED' and Path(rows['prices_volume']['staging']['path']).is_file()
    assert rows['benchmark']['status'] == 'SUCCESS' and result['status'] == 'COMPLETED_WITH_ERRORS'
    assert 'prices_volume|2025-03-05' not in json.loads((tmp_path / 'state.json').read_text(encoding='utf-8'))


def test_rows_already_present_do_not_prove_a_commit(tmp_path):
    """The re-read sees every key (an earlier pipeline wrote them) but this commit wrote fewer rows: not a success."""
    committers = {'prices_volume': (lambda frame, day: len(frame) - 1, lambda frame, day: len(frame))}
    row = by_dataset(service(tmp_path, {'prices_volume': _prices}, committers=committers, enforce=lambda name: None).run('manual', datasets=['prices_volume']))
    assert row['prices_volume']['status'] == 'COMMIT_VERIFY_FAILED'


def test_one_write_authorization_per_commit(tmp_path, monkeypatch):
    from core.runtime import operation_policy
    calls = []
    monkeypatch.setattr(operation_policy, 'enforce', lambda *a, **k: calls.append(a))
    svc = service(tmp_path, {})
    monkeypatch.delenv('STOCK_LINBOT_ENFORCE', raising=False)
    svc._authorize_commit('daily_market_data')
    monkeypatch.setenv('STOCK_LINBOT_ENFORCE', 'write_production_db')
    svc._authorize_commit('daily_market_data')
    assert [call[0] for call in calls] == [operation_policy.Operation.COMMIT_MARKET_DATA] * 2  # one ingestion commit check per commit, always

    def denied(frame, day):
        raise PermissionError('write_production_db: write_production_db is a Production operation; mode is research')
    row = by_dataset(service(tmp_path / 'g', {'prices_volume': _prices}, committers={'prices_volume': (denied, lambda f, d: 0)})
                     .run('manual', datasets=['prices_volume']))['prices_volume']
    assert row['status'] == 'COMMIT_AUTH_BLOCKED'


def test_financials_commit_never_alters_the_schema(tmp_path, monkeypatch):
    """Missing optional column: COMMIT_FAILED before any statement; ensure_financial_columns (ALTER TABLE) is never called."""
    import pandas as pd
    from sqlalchemy import create_engine, text
    from core import db_helper
    engine = create_engine(f"sqlite:///{tmp_path / 'fin.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE financial_statements (stock_id TEXT, year INT, quarter INT, eps REAL)'))
    monkeypatch.setattr(db_helper, 'ensure_financial_columns', lambda conn: pytest.fail('the service never alters a table'))
    committer = mdu.TableCommitter('financial_statements', ('stock_id', 'year', 'quarter'), engine=lambda: engine,
                                   required_columns=mdu.FINANCIAL_OPTIONAL_COLUMNS, date_of=mdu.COMMITTERS['financials'].date_of)
    frame = pd.DataFrame({'stock_id': ['1101', '2330'], 'year': [2024, 2024], 'quarter': [4, 4], 'eps': [1.0, 2.0]})
    adapter = lambda day: {'frame': frame, 'rows': 2, 'latest_trade_date': None, 'has_announcement_date': True, 'required_empty': []}
    row = by_dataset(service(tmp_path, {'financials': adapter}, committers={'financials': committer}, enforce=lambda name: None)
                     .run('manual', datasets=['financials']))['financials']
    assert row['status'] == 'COMMIT_FAILED' and 'SCHEMA_NOT_READY' in row['errors'][0]
    assert pd.read_sql(text('SELECT COUNT(*) AS n FROM financial_statements'), engine)['n'][0] == 0
    q3 = frame.assign(year=2026, quarter=3)
    assert mdu.COMMITTERS['financials'].key_dates(q3, '2026-11-05') == ['2026-09-28', '2026-09-28']  # the period, not the run date
