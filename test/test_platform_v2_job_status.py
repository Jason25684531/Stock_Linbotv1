import json

from core.research.reference import batch_job


def test_platform_job_status_counts_its_own_plan_and_saved_runs(monkeypatch, tmp_path):
    from core.research.platform import params

    monkeypatch.setattr(params, 'OUT', tmp_path)
    rows = [{'strategy_id': f's{i}', 'state': 'REUSE', 'backtest_window': ['2015-01-01', '2025-12-31']}
            for i in range(16)]
    rows.extend([{'strategy_id': f'b{i}', 'state': 'DATA_BLOCKED', 'backtest_window': []} for i in range(3)])
    rows.extend([{'strategy_id': f'g{i}', 'state': 'GOVERNED_ONLY', 'backtest_window': []} for i in range(2)])
    (tmp_path / 'STRATEGY_READINESS_21.json').write_text(
        json.dumps({'result': {'strategies': rows}}), encoding='utf-8')
    runs = tmp_path / 'runs'
    (runs / 'new-run').mkdir(parents=True)
    (runs / 'latest').mkdir()
    old_run = runs / 'old-run'
    old_run.mkdir()
    old_run.touch(exist_ok=True)
    import os
    os.utime(old_run, (1, 1))
    monkeypatch.setattr(batch_job, '_read', lambda: {
        'pid': 42, 'started_at': '2026-11-13T04:00:00+00:00', 'kind': 'platform_baseline',
        'log': str(tmp_path / 'job.log')})
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [42])

    status = batch_job.state()

    assert status['status'] == 'RUNNING'
    assert status['planned'] == 16
    assert status['saved_runs'] == 1
