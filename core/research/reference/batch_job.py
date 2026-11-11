"""One background reference batch at a time, started and cancelled from /reference by an admin (Owner brief 2026-11-09, R5).

The batch runs as its own process (`python -m core.research.reference.runner`), never inside a web request. Any running runner process,
whoever started it, blocks a second start, so the same backtest is never run twice at once. Cancel stops only the process this module
started; runs already saved stay in the run store and the next batch REUSEs them by identity.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.research.reference import catalog, runner

MODULE = 'core.research.reference.runner'
HISTORY = 'scripts.historical_foundation'  # change 2026-11-10-historical-data-foundation-v1: one job slot shared by every kind
KINDS = {'reference': [MODULE], 'history_refresh': [HISTORY, 'refresh'], 'history_run': [HISTORY, 'run']}
DONE = '"status": "DONE"'  # last line a history command prints when it completes


def _job_path() -> Path:
    return runner.OUT / 'jobs' / 'current.json'


def _read() -> dict:
    try:
        return json.loads(_job_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _write(job) -> None:
    _job_path().parent.mkdir(parents=True, exist_ok=True)
    _job_path().write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding='utf-8')


def runner_pids() -> list:
    """Every live process whose command line runs the reference runner (a pid reused by another program never matches)."""
    import psutil
    found = []
    for proc in psutil.process_iter(['pid', 'cmdline']):
        cmdline = proc.info.get('cmdline') or []
        if MODULE in cmdline or HISTORY in cmdline:
            found.append(proc.info['pid'])
    return found


def planned() -> int:
    return sum(1 for cap in catalog.capabilities() if cap.engine is not None and not cap.governance and not cap.static_block)


def state() -> dict:
    job, alive = _read(), runner_pids()
    if not job:
        return {'status': 'RUNNING_ELSEWHERE' if alive else 'IDLE', 'pids': alive, 'planned': planned()}
    started = job['started_at']
    saved = sorted(p.name for p in runner._runs().glob('*') if p.is_dir() and
                   datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat() >= started) if runner._runs().is_dir() else []
    report = runner.latest_report()
    try:
        tail = Path(job['log']).read_text(encoding='utf-8', errors='replace').splitlines()[-5:]
    except OSError:
        tail = []
    if job.get('pid') in alive:
        status = 'RUNNING'
    elif job.get('status') == 'CANCELLED':
        status = 'CANCELLED'
    elif job.get('kind', 'reference') != 'reference':
        status = 'FINISHED' if any(DONE in line for line in tail) else 'FAILED'
    elif report and report.get('created_at', '') >= started:
        status, job['batch_id'] = 'FINISHED', report['batch_id']
    else:
        status = 'FAILED'
    return {**job, 'status': status, 'pids': alive, 'saved_runs': len(saved), 'planned': planned(), 'log_tail': tail}


def start(workers=6, popen=subprocess.Popen, kind='reference') -> dict:
    """Check, spawn and record under one cross-process lock, so a double click handled by two threads starts one runner."""
    from core.research.sources.twse import _exclusive
    if kind not in KINDS:
        raise ValueError(f'UNKNOWN_JOB_KIND: {kind}')
    with _exclusive(runner.OUT / 'jobs' / 'start.lock', timeout=30):
        return _start_locked(workers, popen, kind)


def _start_locked(workers, popen, kind='reference') -> dict:
    alive = runner_pids()
    if alive:
        return {'status': 'RUN_IN_PROGRESS', 'pids': alive}
    stamp = datetime.now(timezone.utc)
    log = runner.OUT / 'jobs' / f'{"batch" if kind == "reference" else kind}_{stamp:%Y%m%dT%H%M%SZ}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w', encoding='utf-8') as handle:
        proc = popen([sys.executable, '-X', 'utf8', '-m', *KINDS[kind], '--workers', str(int(workers))], cwd=str(runner.ROOT),
                     stdout=handle, stderr=subprocess.STDOUT)
    job = {'pid': proc.pid, 'started_at': stamp.isoformat(), 'workers': int(workers), 'log': str(log), 'status': 'STARTED', 'kind': kind}
    _write(job)
    return job


def cancel(kill=None) -> dict:
    job = _read()
    if not job or job.get('pid') not in runner_pids():
        return {'status': 'NOT_RUNNING'}
    if kill is None:
        import psutil

        def kill(pid):
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                child.terminate()
            parent.terminate()
    kill(job['pid'])
    _write({**job, 'status': 'CANCELLED', 'cancelled_at': datetime.now(timezone.utc).isoformat()})
    return {'status': 'CANCELLED', 'pid': job['pid']}
