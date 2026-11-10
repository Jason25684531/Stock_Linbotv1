"""Reference batch from the web (Owner brief 2026-11-09, R5): admin + CSRF, one runner process at a time, cancel keeps saved runs,
fixed-order Markdown download only. No real process is started."""
import pytest

from core.research.reference import batch_job, runner


class FakeProc:
    pid = 4242


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'OUT', tmp_path / 'reference_mode')
    return tmp_path / 'reference_mode'


def test_start_refuses_while_any_runner_process_lives(out, monkeypatch):
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [])
    started = batch_job.start(workers=2, popen=lambda *a, **k: FakeProc())
    assert started['pid'] == 4242 and (out / 'jobs' / 'current.json').is_file()
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [4242])
    assert batch_job.start(popen=lambda *a, **k: pytest.fail('second start'))['status'] == 'RUN_IN_PROGRESS'
    assert batch_job.state()['status'] == 'RUNNING'


def test_cancel_stops_only_the_recorded_process(out, monkeypatch):
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [])
    assert batch_job.cancel(kill=lambda pid: pytest.fail('nothing to kill'))['status'] == 'NOT_RUNNING'
    batch_job.start(popen=lambda *a, **k: FakeProc())
    killed = []
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [4242, 999])
    assert batch_job.cancel(kill=killed.append)['status'] == 'CANCELLED' and killed == [4242]
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [])
    assert batch_job.state()['status'] == 'CANCELLED'


def _admin_client(monkeypatch):
    from app import app, platform_routes
    monkeypatch.setattr(platform_routes, 'is_admin', lambda: True)
    monkeypatch.setattr(platform_routes, 'admin_state', lambda: (True, ''))
    client = app.test_client()
    with client.session_transaction() as s:
        s['csrf'] = 'tok'
    return client


def test_web_start_needs_admin_and_csrf_and_refuses_a_duplicate(out, monkeypatch):
    from app import app
    calls = []
    monkeypatch.setattr(batch_job, 'start', lambda: calls.append(1) or {'status': 'STARTED', 'pid': 1})
    assert app.test_client().post('/admin/reference/start').status_code == 403 and not calls
    client = _admin_client(monkeypatch)
    assert client.post('/admin/reference/start', data={'csrf': 'bad'}).status_code == 403 and not calls
    assert client.post('/admin/reference/start', data={'csrf': 'tok'}).status_code == 302 and calls == [1]
    monkeypatch.setattr(batch_job, 'start', lambda: {'status': 'RUN_IN_PROGRESS', 'pids': [1]})
    assert client.post('/admin/reference/start', data={'csrf': 'tok'}).status_code == 409
    assert client.post('/admin/reference/delete', data={'csrf': 'tok'}).status_code == 404


def test_page_shows_job_state_and_downloads_only_stored_markdown(out, monkeypatch):
    from app import app
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: [77])
    batch = {'batch_id': '20261109T000000Z-aaaaaaaa', 'labels': runner.LABELS, 'disclosure': runner.DISCLOSURE, 'reference_group': 'g',
             'counts': {s: 0 for s in runner.STATES}, 'rows': [], 'created_at': 'now', 'plan_only': False}
    runner.write_report(batch)
    client = app.test_client()
    html = client.get('/reference').get_data(as_text=True)
    assert 'data-testid="reference-job-status">RUNNING_ELSEWHERE<' in html and 'report.md' in html
    reply = client.get('/reference/batches/20261109T000000Z-aaaaaaaa/report.md')
    assert reply.status_code == 200 and b'20261109T000000Z-aaaaaaaa' in reply.data
    assert client.get('/reference/batches/20261109T000000Z-aaaaaaaa/report.json').status_code == 404
    assert client.get('/reference/batches/..%2F..%2Fsecret/report.md').status_code == 404


def test_dashboard_card_uses_the_push_gate_and_shows_line_states(monkeypatch, tmp_path):
    from datetime import datetime
    from app import app, platform_routes
    import core.db_helper as db
    monkeypatch.setattr(db, 'get_latest_valid_market_date', lambda *a, **k: '2026-11-09')
    monkeypatch.setattr(db, 'get_actual_latest_date', lambda *a, **k: '2026-11-06')
    monkeypatch.setattr(platform_routes, 'ROOT', tmp_path)
    (tmp_path / 'outputs' / 'line_publish').mkdir(parents=True)
    (tmp_path / 'outputs' / 'line_publish' / '2026-11-09_evening.json').write_text('{"state": "SENT"}', encoding='utf-8')
    card = platform_routes.publish_overview(now=datetime(2026, 11, 9, 19, 0))
    assert card['fresh'] is False and 'recommendations=2026-11-06' in card['reason'] and card['line'][0]['state'] == 'SENT'
    monkeypatch.setattr(platform_routes, 'publish_overview', lambda: card)
    html = app.test_client().get('/dashboard').get_data(as_text=True)
    assert 'data-testid="publish-universe">TWSE_LISTED_ONLY<' in html and '2026-11-09_evening SENT' in html


def test_strategy_center_items_carry_the_reference_state_and_levels(out):
    from app import app
    row = {'strategy_id': 'mean_reversion', 'state': 'RUN', 'reason': 'NO_IDENTICAL_RUN', 'run_id': 'R9',
           'levels': {'ENGINEERING_PASS': True, 'REPRODUCIBLE': True, 'DATA_TRUST_VERIFIED': False, 'CERTIFIED': False},
           'metrics': {track: {'total_return': {'value': v, 'reason': None}, 'sharpe': {'value': None, 'reason': 'ZERO_VARIANCE'},
                               'max_drawdown': {'value': -0.3, 'reason': None}} for track, v in (('base', -0.29), ('x2', -0.59))}}
    runner.write_report({'batch_id': '20261109T000000Z-bbbbbbbb', 'labels': runner.LABELS, 'disclosure': runner.DISCLOSURE, 'reference_group': 'g',
                         'counts': {s: 0 for s in runner.STATES}, 'rows': [row], 'created_at': 'now', 'plan_only': False})
    html = app.test_client().get('/strategies').get_data(as_text=True)
    assert 'data-testid="reference-status"' in html and '"run_id": "R9"' in html and '"tej_required": false' in html


def test_two_simultaneous_starts_launch_one_runner(out, monkeypatch):
    """Adversarial review 2026-11-09: a double click handled by two threads must not spawn two runners."""
    import threading
    import time
    spawned = []
    monkeypatch.setattr(batch_job, 'runner_pids', lambda: time.sleep(0.2) or list(spawned))

    def popen(*a, **k):
        proc = FakeProc()
        spawned.append(proc.pid)
        return proc
    results = []
    threads = [threading.Thread(target=lambda: results.append(batch_job.start(popen=popen)['status'])) for _ in range(2)]
    [t.start() for t in threads], [t.join() for t in threads]
    assert sorted(results) == ['RUN_IN_PROGRESS', 'STARTED'] and spawned == [4242]
