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


def service(tmp_path, adapters, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return MarketDataUpdateService(root=tmp_path, adapters=adapters, sleep=sleeps.append, today=TODAY)


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
    assert 'holidays' in row['checks']['note']


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


def test_daily_disabled_skips_without_fetch(tmp_path):
    prices = Counter(ok())
    svc = service(tmp_path, {'prices_volume': prices})
    assert svc.run('daily')['status'] == 'SKIPPED_DISABLED'
    assert prices.calls == 0 and not list(tmp_path.glob('[0-9]*.json'))
    save_schedule(tmp_path, True, '18:30')
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


def test_update_database_waits_for_a_manual_update(monkeypatch, capsys):
    """The evening pipeline must not stop because a manual update holds the lock: main() waits, then runs."""
    import contextlib
    import jobs.update_database as job
    import services.market_data_update as svc
    calls = {'n': 0}

    @contextlib.contextmanager
    def lock():
        calls['n'] += 1
        if calls['n'] < 3:
            raise svc.UpdateInProgress('held')
        yield

    monkeypatch.setattr(svc, 'acquire_lock', lock)
    monkeypatch.setattr(job, 'LOCK_WAIT_SECONDS', 0)
    monkeypatch.setattr(job, '_run_update', lambda: 0)
    assert job.main() == 0 and calls['n'] == 3
    monkeypatch.setattr(job, 'LOCK_WAIT_ATTEMPTS', 1)
    calls['n'] = -10
    assert job.main() == 1
    assert 'UPDATE_IN_PROGRESS: lock wait timed out' in capsys.readouterr().out


def test_pipeline_bookkeeping_denial_never_writes_database(tmp_path, monkeypatch):
    import core.db_helper as db
    import core.runtime.operation_policy as policy
    calls = []
    instance = service(tmp_path, {'prices_volume': ok()})
    instance.uses_default_adapters = True
    monkeypatch.setattr(policy, 'enforce', lambda *a, **k: (_ for _ in ()).throw(policy.AuthorizationDenied('denied')))
    monkeypatch.setattr(db, 'record_pipeline_step_start', lambda **kw: calls.append(kw))
    instance._pipeline_step('start')
    assert calls == []
