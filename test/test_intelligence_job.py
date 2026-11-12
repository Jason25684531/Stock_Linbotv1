"""Intelligence job: the shared single batch slot, job id, PARTIAL state, exit codes, REUSE and resume. No real process or data."""
import json

import pytest

from core.research.reference import batch_job, runner
from scripts import strategy_intelligence as si


class FakeProc:
    pid = 5151


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'OUT', tmp_path / 'reference_mode')
    return tmp_path / 'reference_mode'


def test_intelligence_kind_has_a_job_id_and_shares_the_slot(out, monkeypatch):
    seen = []
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [])
    job = batch_job.start(workers=1, kind='intelligence', popen=lambda cmd, **k: seen.append(cmd) or FakeProc())
    assert job['job_id'].endswith('-intelligence') and '--job-id' in seen[0] and 'scripts.strategy_intelligence' in seen[0]
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [5151])
    assert batch_job.start(kind='history_run', popen=lambda *a, **k: pytest.fail('second start'))['status'] == 'RUN_IN_PROGRESS'


def test_partial_log_is_not_finished(out, monkeypatch):
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [])
    job = batch_job.start(kind='intelligence', popen=lambda *a, **k: FakeProc())
    with open(job['log'], 'w', encoding='utf-8') as f:
        f.write(json.dumps({'strategy_id': 'a', 'strategy_status': 'DONE'}) + '\n' + json.dumps({'command': 'run', 'status': 'PARTIAL'}) + '\n')
    assert batch_job.state()['status'] == 'PARTIAL'


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    monkeypatch.setattr(si, 'OUT', tmp_path)
    monkeypatch.setattr(si, 'RESULTS', tmp_path / 'results')
    monkeypatch.setattr(si, 'catalog_ids', lambda: ['a', 'b'])
    monkeypatch.setattr(si, 'load_context', lambda: {'profile_id': 'EP1-x', 'evaluation': {'snapshot_id': 'HS1-x'}})
    monkeypatch.setattr(si, '_portfolio', lambda ctx, job, results: None)
    calls = []

    def analyze(ctx, job, sid):
        calls.append(sid)
        key = f'{sid}|perf'
        if job.exhausted(key):
            job.blocked_by_attempts(sid, 'performance', key)
            return None
        job.start(sid, 'performance', key, {})
        if sid == 'b':
            job.fail(sid, 'performance', key, ValueError('bad nav'))
            return None
        job.done(sid, 'performance', 'x', reused=False)
        result = {'identity': 'SI1-a', 'strategy_id': sid, 'identity_inputs': {'code': si.code_hashes()}}
        si.write_json(si.RESULTS / 'SI1-a' / f'{sid}.json', result)
        return result
    monkeypatch.setattr(si, 'analyze_strategy', analyze)
    return tmp_path, calls


def test_partial_failure_prints_no_done_and_exits_2(fake_run, capsys):
    tmp, _ = fake_run
    assert si.run_job('j1') == 2
    lines = capsys.readouterr().out.splitlines()
    assert not any('"status": "DONE"' in line for line in lines)
    last = json.loads(lines[-1])
    assert last['status'] == 'PARTIAL' and last['exit_code'] == 2 and last['strategies'] == {'a': 'DONE', 'b': 'FAILED'}
    assert json.loads((tmp / 'LATEST.json').read_text())['status'] == 'PARTIAL'


def test_latest_switches_only_to_stored_results(fake_run):
    tmp, _ = fake_run
    si.run_job('j1')
    latest = json.loads((tmp / 'LATEST.json').read_text())
    assert si.latest_problems(latest) == []
    assert si.latest_problems(latest | {'strategies': {'a': 'SI1-0000000000000000'}}) == ['a:SI1-0000000000000000']
    assert si.latest_problems(latest | {'portfolio': 'PF1-0000000000000000'}) == ['PORTFOLIO:PF1-0000000000000000']


def test_third_failure_stops_retrying(fake_run):
    tmp, _ = fake_run
    for n in range(3):
        si.run_job(f'j{n}')
    job = json.loads((tmp / 'jobs' / 'j2.json').read_text())
    assert job['rows']['b|performance']['status'] == 'FAILED_WITH_EVIDENCE'
    si.run_job('j3')
    row = json.loads((tmp / 'jobs' / 'j3.json').read_text())['rows']['b|performance']
    assert row['status'] == 'FAILED_WITH_EVIDENCE' and row['reason'] == 'MAX_ATTEMPTS_REACHED'


def test_context_failure_is_failed_with_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(si, 'OUT', tmp_path)
    monkeypatch.setattr(si, 'load_context', lambda: (_ for _ in ()).throw(FileNotFoundError('snapshot')))
    assert si.run_job('jx') == 1
    out = capsys.readouterr().out
    assert '"status": "FAILED"' in out and 'DONE' not in out


def test_stage_reuse_and_resume(tmp_path, monkeypatch):
    from core.research.intelligence import jobstate
    monkeypatch.setattr(si, 'ROOT', tmp_path)
    folder = tmp_path / 'results' / 'SI1-x'
    first = jobstate.Job(tmp_path, 'one')
    computed = []
    assert si._stage(first, 'a', 'performance', folder, {}, lambda: computed.append(1) or {'v': 1}) == {'v': 1}
    second = jobstate.Job(tmp_path, 'two')
    assert si._stage(second, 'a', 'performance', folder, {}, lambda: pytest.fail('recomputed')) == {'v': 1}
    assert second.state['rows']['a|performance']['status'] == 'REUSED' and computed == [1]
    other = tmp_path / 'results' / 'SI1-y'
    assert si._stage(second, 'a', 'performance', other, {}, lambda: computed.append(2) or {'v': 2}) == {'v': 2} and computed == [1, 2]
