"""Isolated timer, local alert sink and owned Flask restart acceptance."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from scripts.system_acceptance import OUT, ROOT, now, write


def readonly_sql():
    """Reject persistent SQL mutations during acceptance browser/health reads."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    def check(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split(None, 1)[0].upper() not in ('SELECT', 'SHOW', 'DESCRIBE', 'PRAGMA', 'SET'):
            raise PermissionError('ACCEPTANCE_READ_ONLY_SQL')
    event.listen(Engine, 'before_cursor_execute', check)


def alert(path, event, reason, attempts=0):
    """Local-only configurable file sink. Caller supplies redacted reasons, never credentials."""
    record = {'at': now(), 'job': 'isolated-acceptance', 'event': event,
              'severity': 'INFO' if event == 'SUCCESS' else 'ERROR', 'reason': reason,
              'attempted_recovery': f'bounded retry count={attempts}', 'retry_count': attempts,
              'human_required': event != 'SUCCESS', 'evidence': 'outputs/final_system_acceptance/operations_probe.json',
              'scope': 'MOCK_LOCAL_ONLY'}
    with Path(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    return record


def timer_probe(root):
    from scripts import task_controller as tc
    tc.create('timer', 'timer acceptance', [], 'acceptance', 'final', root=root, handler='probe')
    calls, results, errors = [], [], []
    def handler(task, directory):
        tc.step('timer', 'persist', lambda: calls.append('once') or 'saved', directory)
        time.sleep(.1)  # keep the slot occupied while the duplicate trigger fires
        tc.set_status('timer', 'VERIFYING', directory)
    def trigger():
        try:
            results.append(tc.run_once({'probe': handler}, root))
        except Exception as error:
            errors.append(repr(error))
    timers = [threading.Timer(.05, trigger), threading.Timer(.06, trigger)]
    for timer in timers:
        timer.start()
    for timer in timers:
        timer.join(timeout=15)
    assert not errors and all(not timer.is_alive() for timer in timers), errors
    assert calls == ['once'] and sum(bool(r['ran']) for r in results) == 1, results
    return {'status': 'PASS', 'timer': 'threading.Timer real elapsed clock', 'triggers': 2, 'writes': len(calls), 'results': results}


def serve(ready):
    readonly_sql()
    from app import app
    from werkzeug.serving import make_server
    server = make_server('127.0.0.1', 0, app, threaded=True)
    Path(ready).write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
    server.serve_forever()


def web_restart(root):
    from playwright.sync_api import sync_playwright
    rows = []
    # Own both processes; no discovery or termination of any pre-existing service.
    for attempt in range(2):
        ready = root / f'ready-{attempt}.json'
        log = OUT / f'web-restart-{attempt}.log'
        with log.open('w', encoding='utf-8') as stream:
            proc = subprocess.Popen([sys._base_executable, '-X', 'utf8', '-m', 'scripts.system_operations_probe', 'serve', str(ready)],
                                    cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 60
                while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(.1)
                assert ready.exists(), f'Flask failed to start; see {log}'
                port = json.loads(ready.read_text(encoding='utf-8'))['port']
                with sync_playwright() as browser_tool:
                    probe = browser_tool.request.new_context()
                    response = probe.get(f'http://127.0.0.1:{port}/platform/research/v4', timeout=60000)
                    assert response.status == 200
                    probe.dispose()
                rows.append({'attempt': attempt + 1, 'port': port, 'status': 'PASS', 'pid': proc.pid})
            finally:
                proc.kill()
                proc.wait(timeout=15)
    return {'status': 'PASS', 'scope': 'two owned real Flask processes, hard stop and fresh startup; no Windows reboot', 'starts': rows}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'os-trigger':
        write('windows_trigger_result.json', {'at': now(), 'status': 'PASS', 'scope': 'ISOLATED_OS_TASK_ONLY',
                                             'interpreter': sys.executable, 'cwd': str(Path.cwd())})
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        serve(sys.argv[2])
        return 0
    evidence = {'started_at': now(), 'scope': 'ISOLATED_ONLY', 'checks': {}}
    with tempfile.TemporaryDirectory(prefix='linbot-final-') as temp:
        root = Path(temp)
        for name, fn in [('timer_duplicate', lambda: timer_probe(root / 'tasks')), ('web_restart', lambda: web_restart(root))]:
            try:
                evidence['checks'][name] = fn()
            except Exception as error:
                evidence['checks'][name] = {'status': 'FAIL', 'reason': f'{type(error).__name__}: {error}'}
    path = OUT / 'local_alerts.jsonl'
    if path.exists():
        raise RuntimeError('Keep prior alert evidence; choose a new run directory to repeat')
    events = ['SUCCESS', 'FAILURE', 'RETRY_EXHAUSTED', 'DATA_STALE', 'STARTUP_FAILURE', 'DISK_LOW', 'DATABASE_UNAVAILABLE', 'CONSECUTIVE_FAILURE']
    for event in events:
        alert(path, event, 'injected acceptance scenario', 3 if 'FAIL' in event or 'RETRY' in event else 0)
    saved = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert [r['event'] for r in saved] == events and all(r['scope'] == 'MOCK_LOCAL_ONLY' for r in saved)
    evidence['checks']['alerts'] = {'status': 'PASS', 'events': events, 'sink': str(path), 'external_delivery': 'NOT_TESTED'}
    evidence['ended_at'] = now()
    evidence['status'] = 'PASS' if all(v['status'] == 'PASS' for v in evidence['checks'].values()) else 'FAIL'
    write('operations_probe.json', evidence)
    print(json.dumps(evidence))
    return int(evidence['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
