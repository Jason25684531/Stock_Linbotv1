"""Persistent task controller for the quant orchestrator: on-disk state, per-task lock, idempotent steps, fail-closed grants, verified completion.

State lives on disk (outputs/task_state/<task_id>.json), never in agent memory. Every write is atomic (tmp + os.replace).
A step key that is already done is skipped, so a restart or a duplicate trigger never repeats a write. DONE needs passing tests,
no open BLOCKING/MAJOR review finding and existing evidence files. Grants are read from .claude/owner_grants.json, which the
PreToolUse guard (.claude/hooks/guard.py) forbids agents to write; a missing, expired or out-of-scope grant means WAITING_OWNER.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'outputs/task_state'
# Owner keeps grants outside the repo with an ACL the agent account cannot write (STOCK_LINBOT_GRANTS); the repo file is the fallback.
GRANTS = Path(os.environ.get('STOCK_LINBOT_GRANTS') or ROOT / '.claude/owner_grants.json')
STATES = ('PENDING', 'RUNNING', 'VERIFYING', 'DONE', 'BLOCKED', 'WAITING_OWNER', 'WAITING_DATA', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'CANCELLED')
TERMINAL = ('DONE', 'FAILED_FINAL', 'CANCELLED')
BLOCKER_TYPES = ('DATA', 'LOGIC', 'BACKTEST', 'VALIDATION', 'AUTHORIZATION', 'PRODUCTION')
MAX_RETRIES = 2
RETRY_BACKOFF_MINUTES = 15
PRIORITIES = ('P0', 'P1', 'P2', 'P3', 'P4')
AUTO = ('PENDING', 'FAILED_RETRYABLE', 'WAITING_DATA')  # the only states the scheduler picks up by itself
LOCK_STALE_SECONDS = 6 * 3600  # ponytail: wall-clock staleness; a heartbeat if runs ever exceed hours


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _path(task_id, root):
    return Path(root) / f'{task_id}.json'


def _write(task, root):
    path = _path(task['task_id'], root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.stem}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps({**task, 'updated_at': now()}, ensure_ascii=False, indent=1), encoding='utf-8')
    for attempt in range(5):  # Windows: a reader or antivirus holding the file makes os.replace fail briefly
        try:
            os.replace(tmp, path)  # atomic: a crash leaves the old or the new file, never half of one
            return
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))
    raise PermissionError(f'could not replace {path}')


def _update(task_id, root, change):
    """Serialized load-modify-write: a short per-task write lock, separate from the run Lock, so concurrent updates never lose fields."""
    wlock = Path(root) / f'{task_id}.wlock'
    if wlock.is_file() and time.time() - wlock.stat().st_mtime > 60:
        wlock.unlink(missing_ok=True)  # a writer crashed mid-update; updates take milliseconds
    for _ in range(100):
        try:
            fd = os.open(wlock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            time.sleep(0.05)
    else:
        raise RuntimeError(f'{wlock.name} busy')
    try:
        os.close(fd)
        task = load(task_id, root)
        result = change(task)
        _write(task, root)
        return task if result is None else result
    finally:
        wlock.unlink(missing_ok=True)


def load(task_id, root=STATE) -> dict:
    return json.loads(_path(task_id, root).read_text(encoding='utf-8'))


def create(task_id, purpose, acceptance, agent, goal_id, depends_on=(), root=STATE, handler=None, priority='P2', params=None) -> dict:
    """Idempotent: an existing task is returned unchanged, never reset."""
    if _path(task_id, root).is_file():
        return load(task_id, root)
    task = {'task_id': task_id, 'goal_id': goal_id, 'run_id': None, 'purpose': purpose, 'acceptance': list(acceptance), 'agent': agent,
            'depends_on': list(depends_on), 'status': 'PENDING', 'created_at': now(), 'checkpoint': None, 'inputs_sha256': {}, 'code_version': None,
            'experiment_ids': [], 'done_steps': {}, 'remaining_steps': [], 'errors': [], 'retry_count': 0, 'blockers': [], 'next_action': 'claim',
            'handler': handler, 'priority': priority, 'params': params or {}, 'check_after': None}
    _write(task, root)
    return task


def set_status(task_id, status, root=STATE, **fields) -> dict:
    if status not in STATES:
        raise ValueError(f'unknown status {status}')
    def change(task):
        if task['status'] in TERMINAL:
            raise ValueError(f"{task_id} is {task['status']}; terminal tasks are never reopened (create a new task)")
        task.update(fields, status=status)
    return _update(task_id, root, change)


class Lock:
    """Exclusive per-task lock (O_EXCL create). A second trigger fails fast instead of running in parallel."""

    def __init__(self, task_id, root=STATE):
        self.path = Path(root) / f'{task_id}.lock'

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file() and datetime.now().timestamp() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
            self.path.unlink(missing_ok=True)  # owner died long ago; partial work is recovered from done_steps. ponytail: two racers can both see it stale, use a heartbeat if that matters
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(f'{self.path.name} is held: another run of this task is active') from None
        os.write(fd, json.dumps({'pid': os.getpid(), 'at': now()}).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        try:  # only the owner removes the lock
            if json.loads(self.path.read_text())['pid'] == os.getpid():
                self.path.unlink()
        except (OSError, ValueError, KeyError):
            pass


def holds_lock(task_id, root=STATE) -> bool:
    try:
        return json.loads((Path(root) / f'{task_id}.lock').read_text())['pid'] == os.getpid()
    except (OSError, ValueError, KeyError):
        return False


def deps_done(task, root=STATE) -> list:
    return [d for d in task['depends_on'] if not _path(d, root).is_file() or load(d, root)['status'] != 'DONE']


def step(task_id, key, fn, root=STATE):
    """Run fn() once per idempotency key (e.g. strategy_version|data_date|signal|purpose). Done keys are skipped; failures are recorded."""
    if not holds_lock(task_id, root):
        raise RuntimeError(f'{task_id}: step() runs only inside `with Lock(task_id)` (one active run per task)')
    task = load(task_id, root)
    if task['status'] in TERMINAL:
        raise ValueError(f"{task_id} is {task['status']}; no step runs on a terminal task")
    if key in task['done_steps']:
        return task['done_steps'][key]['result']
    try:
        result = fn()
    except Exception as exc:  # recorded, then re-raised: the caller decides retry vs final
        _update(task_id, root, lambda t: t['errors'].append({'at': now(), 'step': key, 'error': f'{type(exc).__name__}: {exc}'[:500]}))
        raise

    def done(t):
        t['done_steps'][key] = {'at': now(), 'result': result}
        t['checkpoint'] = key
        t['remaining_steps'] = [s for s in t['remaining_steps'] if s != key]
    _update(task_id, root, done)
    return result


def fail(task_id, error, root=STATE, at=None) -> dict:
    """Retryable until MAX_RETRIES, then FAILED_FINAL: no unbounded retry of the same problem."""
    task = load(task_id, root)
    retries = task['retry_count'] + 1
    status = 'FAILED_RETRYABLE' if retries <= MAX_RETRIES else 'FAILED_FINAL'
    retry_at = ((at or datetime.now(timezone.utc)) + timedelta(minutes=RETRY_BACKOFF_MINUTES * 2 ** (retries - 1))).isoformat(timespec='seconds')
    return set_status(task_id, status, root, retry_count=retries, errors=task['errors'] + [{'at': now(), 'error': error}],
                      check_after=retry_at if status == 'FAILED_RETRYABLE' else None,
                      next_action=f'retry from checkpoint after {retry_at}' if status == 'FAILED_RETRYABLE' else 'needs new evidence or a new task')


def wait_data(task_id, data_date, check_after, reason, root=STATE) -> dict:
    """WAITING_DATA with the data date and the earliest next check: the scheduler skips the task until then (no busy retry)."""
    return set_status(task_id, 'WAITING_DATA', root, data_date=data_date, check_after=check_after,
                      next_action=f'check {data_date} again after {check_after}: {reason}')


def block(task_id, blocker_type, detail, reopen_when, owner=False, alternative='', root=STATE) -> dict:
    if blocker_type not in BLOCKER_TYPES:
        raise ValueError(f'unknown blocker type {blocker_type}')
    task = load(task_id, root)
    blocker = {'blocker_id': f"{task_id}:B{len(task['blockers']) + 1}", 'type': blocker_type, 'detail': detail, 'reopen_when': reopen_when,
               'requires_owner_approval': owner, 'alternative': alternative, 'at': now()}
    status = 'WAITING_OWNER' if owner else 'BLOCKED'  # WAITING_DATA only via wait_data(), which always sets a check time
    return set_status(task_id, status, root, blockers=task['blockers'] + [blocker], next_action=f'reopen when: {reopen_when}')


def grant_for(operation, grants_path=GRANTS, at=None) -> dict | None:
    """Fail closed: unreadable file, unknown operation, expired or exhausted grant -> None."""
    try:
        grants = json.loads(Path(grants_path).read_text(encoding='utf-8'))['grants']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    at = at or datetime.now(timezone.utc)
    for g in grants:
        try:
            if g['operation'] == operation and datetime.fromisoformat(g['expires_at']) > at and g.get('max_uses', 1) > 0:
                return g
        except (KeyError, ValueError, TypeError):
            continue
    return None


def grant_trusted(grants_path=GRANTS) -> bool:
    """Trusted only if: outside the repo, no read-only attribute (the agent could clear it), writing is refused by the ACL
    (access denied, not a sharing violation), and the file is owned by another account (the agent cannot change its ACL)."""
    import stat
    path = Path(grants_path)
    try:
        if not path.is_file() or path.resolve().is_relative_to(ROOT):
            return False
        if getattr(path.stat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_READONLY:
            return False
        if file_owner(path) == current_user():
            return False
        with path.open('r+b'):
            return False  # writable by this account
    except PermissionError as exc:
        return getattr(exc, 'winerror', 5) == 5  # 32 = held open by another process: says nothing about the ACL
    except OSError:
        return False


def file_owner(path) -> str:
    import win32security
    sd = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
    return win32security.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())


def current_user() -> str:
    import win32api
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])


def usable_grant(operation, dataset, day, grants_path=GRANTS, at=None) -> tuple[dict | None, str]:
    """(grant, '') only for a trusted, unexpired grant whose scope names this dataset and date; otherwise (None, reason)."""
    if not grant_trusted(grants_path):
        return None, f'grant file {grants_path} is not OS-protected (writable by this account or inside the repo)'
    grant = grant_for(operation, grants_path, at)
    if grant is None:
        return None, f'no unexpired {operation} grant'
    scope = grant.get('scope') or {}
    if dataset not in scope.get('datasets', []) or day not in scope.get('dates', []):
        return None, f'{operation} grant does not cover {dataset} {day}'
    return grant, ''


def require_grant(task_id, operation, root=STATE, grants_path=GRANTS):
    """Level 1 gate: without a valid Owner grant the task waits; nothing runs."""
    grant = grant_for(operation, grants_path)
    if grant is None:
        block(task_id, 'AUTHORIZATION', f'no valid Owner grant for {operation}', f'Owner adds a grant for {operation} to .claude/owner_grants.json', owner=True, root=root)
        raise PermissionError(f'{operation}: no valid Owner grant')
    return grant


def complete(task_id, tests_passed: bool, review: dict, evidence_paths, root=STATE) -> dict:
    """DONE only with passing tests, no open BLOCKING/MAJOR review finding, and every evidence file present; else back to VERIFYING."""
    task = load(task_id, root)
    if task['status'] != 'VERIFYING':
        raise ValueError(f"{task_id} is {task['status']}; only a VERIFYING task can complete")
    inside = lambda p: (ROOT / p).resolve().is_relative_to(ROOT) or Path(p).resolve().is_relative_to(Path(root).resolve())
    missing = [p for p in evidence_paths if not inside(p) or not ((ROOT / p).exists() or Path(p).exists())]
    open_findings = [f for f in review.get('findings', []) if f.get('severity') in ('BLOCKING', 'MAJOR') and not f.get('resolved')]
    waiting = deps_done(task, root)
    reasons = [r for r, bad in (('tests failed', not tests_passed), (f'dependencies not DONE: {waiting}', bool(waiting)), (f'open review findings: {len(open_findings)}', bool(open_findings)),
                                ('no independent review', not review.get('reviewer')), (f'missing evidence: {missing}', bool(missing))) if bad]
    if reasons:
        return set_status(task_id, 'VERIFYING', root, next_action='; '.join(reasons))
    return set_status(task_id, 'DONE', root, review=review, evidence=list(evidence_paths), next_action=None)


def runnable(handlers, root=STATE, at=None) -> list:
    """Tasks the scheduler may run now: an automatic state, a known handler, every dependency DONE, the wait over, no run lock held.
    WAITING_OWNER, BLOCKED, VERIFYING and terminal tasks are never picked: they wait for their event without stopping the others."""
    at = at or datetime.now(timezone.utc)
    out = []
    for p in sorted(Path(root).glob('*.json')):
        t = json.loads(p.read_text(encoding='utf-8'))
        if not (isinstance(t, dict) and 'task_id' in t) or t['status'] not in AUTO or t.get('handler') not in handlers:
            continue
        if deps_done(t, root) or (Path(root) / f"{t['task_id']}.lock").exists():
            continue
        if t.get('check_after') and datetime.fromisoformat(t['check_after']) > at:
            continue
        out.append(t)
    return sorted(out, key=lambda t: (PRIORITIES.index(t.get('priority', 'P2')), t['created_at']))


def next_wake(root=STATE):
    """Earliest check_after among waiting/retrying tasks: when an external trigger should come back, or None."""
    times = [t['check_after'] for t in (json.loads(p.read_text(encoding='utf-8')) for p in Path(root).glob('*.json'))
             if isinstance(t, dict) and t.get('status') in ('WAITING_DATA', 'FAILED_RETRYABLE') and t.get('check_after')]
    return min(times) if times else None


def run_once(handlers, root=STATE, at=None, max_tasks=20) -> dict:
    """One scheduler pass. Each runnable task runs at most once; a failing or blocked task never stops the others.
    With nothing runnable the pass records IDLE_UNTIL_NEXT_EVENT and returns: an external scheduler or event starts the next pass (no loop here).
    A handler(task, root) must leave the task DONE / VERIFYING / WAITING_* / BLOCKED; leaving it RUNNING counts as a failure."""
    ran = []
    for task in runnable(handlers, root, at)[:max_tasks]:
        task_id = task['task_id']
        try:
            with Lock(task_id, root):
                set_status(task_id, 'RUNNING', root, run_id=now())
                try:
                    handlers[task['handler']](load(task_id, root), root)
                except Exception as exc:  # bounded: fail() moves to FAILED_FINAL after MAX_RETRIES
                    fail(task_id, f'{type(exc).__name__}: {exc}'[:500], root, at)
                if load(task_id, root)['status'] == 'RUNNING':
                    fail(task_id, 'handler returned without a final state', root, at)
        except RuntimeError:  # lock held: another runner owns it this time
            continue
        ran.append((task_id, load(task_id, root)['status']))
    state = {'at': (at or datetime.now(timezone.utc)).isoformat(timespec='seconds'), 'ran': ran,
             'state': 'RAN' if ran else 'IDLE_UNTIL_NEXT_EVENT', 'next_wake': next_wake(root)}
    Path(root).mkdir(parents=True, exist_ok=True)
    (Path(root) / '_scheduler.snapshot').write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')
    return state


STOP_CLASS = {'WAITING_OWNER': 'WAITING_OWNER', 'WAITING_DATA': 'WAITING_DATA', 'BLOCKED': 'WAITING_OWNER', 'VERIFYING': 'REVIEW_BLOCKED',
              'FAILED_RETRYABLE': 'RUNTIME_FAILURE', 'FAILED_FINAL': 'RUNTIME_FAILURE'}


def stop_report(handlers=(), root=STATE) -> list:
    """Why each open task is not moving: the stop class plus its reopen condition. PENDING without a handler = NO_RUNNABLE_TASK (needs a session)."""
    rows = []
    for t in (json.loads(p.read_text(encoding='utf-8')) for p in sorted(Path(root).glob('*.json'))):
        if not (isinstance(t, dict) and 'task_id' in t) or t['status'] in TERMINAL:
            continue
        cls = STOP_CLASS.get(t['status']) or ('RUNNABLE' if t.get('handler') in handlers and not deps_done(t, root) else 'NO_RUNNABLE_TASK')
        rows.append({'task_id': t['task_id'], 'status': t['status'], 'stop_class': cls, 'next_action': t.get('next_action')})
    return rows


def dev_work(root=STATE, at=None) -> list:
    """Development tasks an orchestrator session can move: PENDING / due FAILED_RETRYABLE with dependencies DONE, and VERIFYING (needs a reviewer).
    Tasks with a deterministic handler belong to the Daily Runner; WAITING_* and BLOCKED wait for their event."""
    at = at or datetime.now(timezone.utc)
    out = []
    for t in (json.loads(p.read_text(encoding='utf-8')) for p in sorted(Path(root).glob('*.json'))):
        if not (isinstance(t, dict) and 'task_id' in t) or t.get('handler'):
            continue
        due = not t.get('check_after') or datetime.fromisoformat(t['check_after']) <= at
        if t['status'] == 'VERIFYING' or (t['status'] in ('PENDING', 'FAILED_RETRYABLE') and due and not deps_done(t, root)):
            out.append(t['task_id'])
    return out


def summary(root=STATE) -> list:
    tasks = (json.loads(p.read_text(encoding='utf-8')) for p in sorted(Path(root).glob('*.json')) if not p.name.startswith('_'))
    return [{k: t[k] for k in ('task_id', 'status', 'agent', 'checkpoint', 'next_action')} | {'blockers': len(t['blockers'])}
            for t in tasks if isinstance(t, dict) and 'task_id' in t]  # snapshots and foreign json are not tasks


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('summary', 'show', 'stop-report', 'has-dev-work'))
    parser.add_argument('--task')
    args = parser.parse_args()
    if args.command == 'has-dev-work':  # exit 0 only when an AI session has something to do (scheduler gate, saves idle runs)
        work = dev_work()
        print(json.dumps(work, ensure_ascii=False))
        sys.exit(0 if work else 1)
    print(json.dumps({'summary': summary, 'stop-report': stop_report}.get(args.command, lambda: load(args.task))(), ensure_ascii=False, indent=1))
