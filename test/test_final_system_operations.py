"""Isolated final acceptance counterexamples; no production jobs or notifications."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def test_system_status_reader_never_initializes_schema(monkeypatch):
    from app import web_server
    calls = []
    monkeypatch.setattr(web_server.db_helper, 'ensure_pipeline_run_state_schema', lambda *a: calls.append('DDL'))
    class Engine:
        def connect(self):
            raise ConnectionError('isolated database outage')
    monkeypatch.setattr(web_server.db_helper, 'get_db_engine', lambda: Engine())
    result, code = web_server._build_system_status_payload(include_freshness=False)
    assert code == 200 and result
    assert calls == []


@pytest.mark.parametrize('payload', ['{broken', '[]', 'null'])
def test_corrupt_checkpoint_refuses_recovery(tmp_path, monkeypatch, payload):
    from jobs import run_research_platform as rp
    monkeypatch.setattr(rp, 'OUT', tmp_path)
    path = tmp_path / 'steps/data_trust.json'
    path.parent.mkdir()
    path.write_text(payload, encoding='utf-8')
    with pytest.raises(ValueError, match='CHECKPOINT_INVALID'):
        rp._state('data_trust')
    assert path.read_text(encoding='utf-8') == payload


def test_morning_wrapper_preserves_scheduler_failure(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows batch execution')
    original = Path('execution/morning_run.bat').read_text(encoding='utf-8')
    # Execute the real wrapper control flow with only command/path substitutions.
    text = original.replace('cd /d D:\\01_Project\\Stocke\\Stock_Linbotv1', f'cd /d "{tmp_path}"')
    text = text.replace('%PYTHON% -X utf8 jobs\\scheduler.py morning', 'cmd /c exit 7')
    text = text.replace('timeout /t 10 >nul', 'ver >nul')
    wrapper = tmp_path / 'probe.bat'
    wrapper.write_text(text, encoding='utf-8')
    done = subprocess.run(['cmd', '/c', str(wrapper)], capture_output=True, timeout=15)
    assert done.returncode == 7


def test_hard_killed_task_recovers_completed_checkpoint(tmp_path):
    from scripts import task_controller as tc
    tc.create('probe', 'isolated hard kill', [], 'acceptance', 'final', root=tmp_path, handler='probe')
    code = '''import sys,time
from pathlib import Path
from scripts import task_controller as tc
root=Path(sys.argv[1])
with tc.Lock('probe',root):
 tc.set_status('probe','RUNNING',root)
 tc.step('probe','committed',lambda: 'saved',root)
 (root/'ready').write_text('ready')
 time.sleep(30)
'''
    child = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', code, str(tmp_path)])
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / 'ready').exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.03)
        assert (tmp_path / 'ready').exists()
        assert tc.runnable({'probe': None}, tmp_path) == []  # live owner must never be stolen
        # Windows venv launcher has a Python child: terminate the owned tree.
        import psutil
        for owned in psutil.Process(child.pid).children(recursive=True):
            owned.kill()
            owned.wait(timeout=10)
        child.kill()
        child.wait(timeout=10)
        def handler(task, root):
            assert tc.step('probe', 'committed', lambda: pytest.fail('duplicate write'), root) == 'saved'
            tc.set_status('probe', 'VERIFYING', root)
        result = tc.run_once({'probe': handler}, tmp_path)
        assert result['ran'] == [('probe', 'VERIFYING')]
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_old_live_lock_is_not_stolen(tmp_path):
    from scripts import task_controller as tc
    with tc.Lock('live', tmp_path):
        os.utime(tmp_path / 'live.lock', (1, 1))
        with pytest.raises(RuntimeError, match='held'):
            with tc.Lock('live', tmp_path):
                pytest.fail('stole live owner')


@pytest.mark.parametrize('payload', [None, '{bad', '[]', '{"status":"PASS"}'])
def test_operations_summary_is_defensive_and_read_only(tmp_path, monkeypatch, payload):
    from services import web_dashboard
    monkeypatch.setattr(web_dashboard, 'ROOT', tmp_path)
    path = tmp_path / 'outputs/final_system_acceptance/operations_summary.json'
    if payload is not None:
        path.parent.mkdir(parents=True)
        path.write_text(payload, encoding='utf-8')
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert web_dashboard.operations_status()['status'] == 'UNAVAILABLE'
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('status', ['VERIFYING', 'DONE', 'WAITING_DATA'])
def test_stale_scheduler_candidate_is_rechecked_under_lock(tmp_path, monkeypatch, status):
    from scripts import task_controller as tc
    candidate = tc.create('race', 'race', [], 'acceptance', 'final', root=tmp_path, handler='probe')
    task = candidate | {'status': status, 'check_after': '2099-01-01T00:00:00+00:00'}
    tc._write(task, tmp_path)
    monkeypatch.setattr(tc, 'runnable', lambda *a: [candidate])
    assert tc.run_once({'probe': lambda *a: pytest.fail('stale candidate ran')}, tmp_path)['ran'] == []


@pytest.mark.parametrize('payload', [{'status': 'COMPLETE'}, {'status': 'COMPLETE', 'key': 3, 'attempts': 1}])
def test_late_corrupt_checkpoint_prevents_all_execution(tmp_path, monkeypatch, payload):
    from jobs import run_research_platform as rp
    monkeypatch.setattr(rp, 'OUT', tmp_path)
    rp._save('health', payload)
    steps = {name: ((), (), lambda: pytest.fail('executed before checkpoint validation')) for name in rp.STEP_NAMES}
    with pytest.raises(ValueError, match='CHECKPOINT_INVALID: health'):
        rp.run('probe', steps)


@pytest.mark.parametrize('kind', ['network', 'mysql'])
def test_transient_outage_is_classified_and_recovers_with_bounded_retry(tmp_path, monkeypatch, kind):
    from jobs import run_research_platform as rp
    from sqlalchemy.exc import OperationalError
    monkeypatch.setattr(rp, 'OUT', tmp_path)
    error = ConnectionError('injected network outage') if kind == 'network' else OperationalError(None, None, Exception('injected database outage'))
    attempts = []
    def transient():
        attempts.append(1)
        if len(attempts) < 3:
            raise error
        return {'recovered': True}
    steps = {name: ((), (), lambda: {'isolated': True}) for name in rp.STEP_NAMES}
    steps['health'] = ((), (), transient)
    for _ in range(2):
        assert rp.run('outage', steps) == 2
        assert rp._state('health')['failure_class'] == 'ENVIRONMENT'
    assert rp.run('recovered', steps) == 0
    assert len(attempts) == 3 and rp._state('health')['status'] == 'COMPLETE'


def test_production_pipeline_aliases_share_exclusive_slot_without_running_jobs(tmp_path, monkeypatch):
    from jobs import scheduler
    from core.research.sources.twse import _exclusive
    monkeypatch.setattr(scheduler, 'REPO_ROOT', tmp_path)
    calls = []
    monkeypatch.setattr(scheduler, '_run_pipeline_steps', lambda *a, **k: calls.append(a) or 0)
    with _exclusive(tmp_path / 'outputs/scheduler/production_pipeline.lock'):
        for name in ('daily', 'evening', 'morning', 'push_evening'):
            assert scheduler.run_pipeline(name, stop_on_error=True) == 75
        assert calls == []
    assert scheduler.run_pipeline('evening', stop_on_error=True) == 0
    assert len(calls) == 1


def test_backup_filters_binary_secrets_and_unreviewed_archives(tmp_path, monkeypatch):
    from scripts import system_acceptance_backup as backup
    monkeypatch.setattr(backup, 'ROOT', tmp_path)
    monkeypatch.setenv('FINAL_PROBE_API_KEY', 'fake-process-secret-acceptance')
    (tmp_path / '.env').write_text('FINAL_PROBE_API_KEY=fake-dotenv-secret-acceptance\n', encoding='utf-8')
    values = backup.secret_values()
    assert b'fake-process-secret-acceptance' in values and b'fake-dotenv-secret-acceptance' in values
    binary = tmp_path / 'model.pkl'
    binary.write_bytes(b'\x00fake-process-secret-acceptance\x00')
    assert backup.allowed(binary, values) == (False, 'secret_value_detected')
    archive = tmp_path / 'unknown.zip'
    archive.write_bytes(b'not accepted without member review')
    assert backup.allowed(archive, values)[0] is False
    assert not backup.has_secret(b'ordinary_brief_identifier', [b'brief'])
    assert backup.has_secret(b'password=brief', [b'brief'])
    assert backup.has_secret(b'x://user:brief@host', [b'brief'])
