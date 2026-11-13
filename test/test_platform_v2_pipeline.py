"""Research pipeline: checkpoints, failure classes, attempt limit, exit codes, dry-run and the scheduler registration."""
import json

import pytest

from jobs import run_research_platform as rp


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, 'OUT', tmp_path / 'pipeline')
    calls = []

    def step(name, fail=None):
        def fn():
            calls.append(name)
            if fail:
                raise fail
            return {'ok': name}
        return fn
    def make(**failures):
        return {n: (deps, (), step(n, failures.get(n))) for n, deps in (
            ('data_trust', ()), ('baseline', ('data_trust',)), ('analysis', ('baseline',)), ('models', ('data_trust',)),
            ('prediction_tracking', ('models',)), ('portfolio', ('baseline',)), ('health', ()))}
    return calls, make, tmp_path / 'pipeline'


def test_all_complete_exit_zero_and_reuse(pipeline):
    calls, make, out = pipeline
    assert rp.run('j1', make()) == 0
    assert json.loads((out / 'LATEST.json').read_text(encoding='utf-8'))['status'] == 'DONE'
    calls.clear()
    assert rp.run('j2', make()) == 0
    assert calls == ['analysis', 'health']  # identical identity: the others are REUSED; analysis and health always re-check


def test_one_failure_keeps_independent_steps_and_exits_two(pipeline):
    calls, make, out = pipeline
    code = rp.run('j1', make(models=FileNotFoundError('DATA missing')))
    latest = json.loads((out / 'LATEST.json').read_text(encoding='utf-8'))
    assert code == 2 and latest['status'] == 'PARTIAL'
    assert latest['steps']['models']['failure_class'] == 'DATA'
    assert latest['steps']['prediction_tracking']['status'] == 'SKIPPED'
    assert all(latest['steps'][n]['status'] == 'COMPLETE' for n in ('baseline', 'analysis', 'portfolio', 'health'))


def test_three_failures_stop_retrying(pipeline):
    calls, make, _ = pipeline
    for _ in range(4):
        rp.run('j', make(health=RuntimeError('boom')))
    assert calls.count('health') == 3


def test_every_step_failing_exits_one(pipeline):
    _, make, _ = pipeline
    boom = {n: RuntimeError('x') for n in rp.STEP_NAMES}
    assert rp.run('j', make(**boom)) == 1


@pytest.mark.parametrize('error, cls', [(ValueError('OD-2: late'), 'GOVERNANCE'), (AssertionError(), 'TEST'), (MemoryError(), 'ENVIRONMENT'),
                                        (OSError('INSUFFICIENT_DISK: 0.5 GB free'), 'ENVIRONMENT'),
                                        (KeyError('x'), 'DATA'), (ZeroDivisionError(), 'CODE')])
def test_classification(error, cls):
    assert rp.classify(error) == cls


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rp, 'OUT', tmp_path / 'pipeline')
    assert rp.main(['--dry-run']) == 0
    assert not (tmp_path / 'pipeline').exists() and 'baseline' in capsys.readouterr().out


def test_scheduler_adds_only_a_research_pipeline():
    from jobs import scheduler
    assert scheduler.PIPELINES['research'] == (scheduler.ScheduledStep('research_platform'),)
    assert 'research_platform' in scheduler.DRY_RUN_SUPPORTED_JOBS
    expected = {'daily': ['update_database', 'run_daily', 'daily_backtest_validation', 'push_to_line'],
                'evening': ['update_database', 'run_daily', 'daily_backtest_validation', 'push_to_line'],
                'morning': ['push_to_line'], 'push_evening': ['push_to_line']}
    assert {n: [s.target for s in scheduler.PIPELINES[n]] for n in expected} == expected
    assert all('research_platform' not in [s.target for s in scheduler.PIPELINES[n]] for n in expected)


def test_pipeline_source_never_writes_mysql_or_line():
    source = open(rp.__file__, encoding='utf-8').read()
    for forbidden in ('push_to_line', 'line_bot', 'db_write_gate', 'to_sql', 'schtasks', 'CREATE_SCHEDULE'):
        assert forbidden not in source


def test_dependency_identity_invalidates_downstream_checkpoints(pipeline, tmp_path, monkeypatch):
    calls, make, _ = pipeline
    monkeypatch.setattr(rp, 'ROOT', tmp_path)
    source = tmp_path / 'readiness.json'
    source.write_text('snapshot-1', encoding='utf-8')
    steps = make()
    steps['data_trust'] = ((), ('readiness.json',), lambda: calls.append('data_trust') or {'snapshot': source.read_text(encoding='utf-8')})

    assert rp.run('j1', steps) == 0
    calls.clear()
    source.write_text('snapshot-2', encoding='utf-8')
    assert rp.run('j2', steps) == 0
    assert {'data_trust', 'baseline', 'analysis', 'models', 'prediction_tracking', 'portfolio'} <= set(calls)


def test_missing_checkpoint_input_fails_as_data_instead_of_being_ignored(pipeline, tmp_path, monkeypatch):
    _, make, out = pipeline
    monkeypatch.setattr(rp, 'ROOT', tmp_path)
    steps = make()
    steps['data_trust'] = ((), ('missing.json',), lambda: {'unexpected': True})

    assert rp.run('j1', steps) == 2
    latest = json.loads((out / 'LATEST.json').read_text(encoding='utf-8'))
    assert latest['steps']['data_trust']['status'] == 'FAILED'
    assert latest['steps']['data_trust']['failure_class'] == 'DATA'


def test_baseline_preflight_requires_matching_characterizations_resources_and_pipeline_slot(monkeypatch):
    import os
    import psutil
    import shutil
    from types import SimpleNamespace
    from core.research.reference import batch_job
    from scripts import platform_v2 as p2

    monkeypatch.setattr(batch_job, 'state', lambda: {'kind': 'platform_pipeline', 'status': 'RUNNING', 'pid': os.getpid()})
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=32 * 2 ** 30))
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=2 * 2 ** 30))
    calls = []
    def characterize(strategy_id, start, end):
        calls.append((strategy_id, start, end))
        return {'strategy_id': strategy_id, 'status': 'PASS', 'snapshot_id': 'HS1-same'}
    monkeypatch.setattr(p2, 'characterize', characterize)

    result = rp._baseline_preflight(p2)
    assert [call[0] for call in calls] == ['mean_reversion', 'kd_oversold']
    assert result['slot']['kind'] == 'platform_pipeline'
    assert result['resources']['memory_required_gb'] == 24


def test_baseline_preflight_fails_closed_on_characterization_mismatch(monkeypatch):
    import os
    import psutil
    import shutil
    from types import SimpleNamespace
    from core.research.reference import batch_job
    from scripts import platform_v2 as p2

    monkeypatch.setattr(batch_job, 'state', lambda: {'kind': 'platform_pipeline', 'status': 'RUNNING', 'pid': os.getpid()})
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=32 * 2 ** 30))
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=2 * 2 ** 30))
    monkeypatch.setattr(p2, 'characterize', lambda strategy_id, start, end: {
        'strategy_id': strategy_id, 'status': 'PASS', 'snapshot_id': strategy_id})

    with pytest.raises(RuntimeError, match='CHARACTERIZATION_SNAPSHOT_MISMATCH'):
        rp._baseline_preflight(p2)


def test_baseline_preflight_rejects_a_conflicting_slot(monkeypatch):
    from core.research.reference import batch_job
    from scripts import platform_v2 as p2

    monkeypatch.setattr(batch_job, 'state', lambda: {'kind': 'platform_backtest', 'status': 'RUNNING', 'pid': 1})
    with pytest.raises(RuntimeError, match='SLOT_CONFLICT'):
        rp._baseline_preflight(p2)


@pytest.mark.parametrize('memory_gb,disk_gb,reason', [(23, 2, 'INSUFFICIENT_MEMORY'), (32, 0.5, 'INSUFFICIENT_DISK')])
def test_baseline_preflight_rejects_insufficient_resources(monkeypatch, memory_gb, disk_gb, reason):
    import os
    import psutil
    import shutil
    from types import SimpleNamespace
    from core.research.reference import batch_job
    from scripts import platform_v2 as p2

    monkeypatch.setattr(batch_job, 'state', lambda: {'kind': 'platform_pipeline', 'status': 'RUNNING', 'pid': os.getpid()})
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=memory_gb * 2 ** 30))
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=disk_gb * 2 ** 30))

    error = MemoryError if reason == 'INSUFFICIENT_MEMORY' else OSError
    with pytest.raises(error, match=reason):
        rp._baseline_preflight(p2)
