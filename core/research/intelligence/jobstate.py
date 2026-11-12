"""Persistent per-strategy, per-stage job state, failure classes and bounded retries (design D10)."""
from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

FAILURE_CLASSES = ('CODE_ERROR', 'DATA_MISSING', 'DATA_INVALID', 'DATA_TRUST_BLOCKED', 'RESOURCE_EXHAUSTED', 'TRANSIENT_SOURCE_FAILURE',
                   'PERMISSION_BLOCKED', 'MODEL_OR_STATISTICAL_LIMITATION', 'SECURITY_RISK', 'GOVERNANCE_BLOCKED')
MAX_ATTEMPTS = 3
EXIT = {'COMPLETE': 0, 'FAILED': 1, 'PARTIAL': 2}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def classify(exc: BaseException) -> str:
    text = f'{type(exc).__name__}: {exc}'
    rules = (('SECURITY', 'SECURITY_RISK'), ('PROFILE_NOT_FROZEN', 'GOVERNANCE_BLOCKED'), ('SEALED', 'GOVERNANCE_BLOCKED'),
             ('DATA_TRUST', 'DATA_TRUST_BLOCKED'), ('SNAPSHOT_SOURCES_CHANGED', 'DATA_INVALID'), ('INSUFFICIENT', 'MODEL_OR_STATISTICAL_LIMITATION'))
    for marker, cls in rules:
        if marker in text:
            return cls
    if isinstance(exc, MemoryError):
        return 'RESOURCE_EXHAUSTED'
    if isinstance(exc, PermissionError):
        return 'PERMISSION_BLOCKED'
    if isinstance(exc, FileNotFoundError):
        return 'DATA_MISSING'
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return 'TRANSIENT_SOURCE_FAILURE'
    if isinstance(exc, ValueError) or type(exc).__name__ in ('EmptyDataError', 'ParserError'):
        return 'DATA_INVALID'
    return 'CODE_ERROR'


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    os.replace(tmp, path)


class Job:
    """One job file (rows keyed strategy|stage) plus a shared attempts ledger keyed by stage identity, so retries survive restarts."""

    def __init__(self, root: Path, job_id: str):
        self.root, self.job_id = Path(root), job_id
        self.path = self.root / 'jobs' / f'{job_id}.json'
        self.attempts_path = self.root / 'jobs' / 'attempts.json'
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.is_file() else \
            {'job_id': job_id, 'started_at': now(), 'status': 'RUNNING', 'rows': {}}
        self.attempts = json.loads(self.attempts_path.read_text(encoding='utf-8')) if self.attempts_path.is_file() else {}
        self.save()

    def save(self) -> None:
        _write(self.path, self.state)
        _write(self.attempts_path, self.attempts)

    def row(self, sid: str, stage: str) -> dict:
        return self.state['rows'].setdefault(f'{sid}|{stage}', {'strategy_id': sid, 'stage': stage, 'status': 'PENDING', 'retries': 0})

    def exhausted(self, key: str) -> bool:
        return self.attempts.get(key, {}).get('failures', 0) >= MAX_ATTEMPTS

    def start(self, sid, stage, key, hashes: dict) -> None:
        self.row(sid, stage).update(status='RUNNING', started_at=now(), stage_identity=key, **hashes)
        self.save()

    def done(self, sid, stage, output: str, reused: bool) -> None:
        self.row(sid, stage).update(status='REUSED' if reused else 'DONE', finished_at=now(), output_path=output, checkpoint=output)
        self.save()

    def skipped(self, sid, stage, reason: str) -> None:
        self.row(sid, stage).update(status='NOT_APPLICABLE', reason=reason, finished_at=now())
        self.save()

    def fail(self, sid, stage, key: str, exc: BaseException) -> str:
        cls = classify(exc)
        record = self.attempts.setdefault(key, {'failures': 0})
        record.update(failures=record['failures'] + 1, last_error=f'{type(exc).__name__}: {str(exc)[:400]}', failure_class=cls, at=now())
        status = 'FAILED_WITH_EVIDENCE' if record['failures'] >= MAX_ATTEMPTS else 'FAILED'
        self.row(sid, stage).update(status=status, failure_class=cls, retries=record['failures'], last_error=record['last_error'],
                                    traceback=traceback.format_exception(exc)[-3:], finished_at=now())
        self.save()
        return status

    def blocked_by_attempts(self, sid, stage, key: str) -> None:
        rec = self.attempts[key]
        self.row(sid, stage).update(status='FAILED_WITH_EVIDENCE', failure_class=rec.get('failure_class'), retries=rec['failures'],
                                    last_error=rec.get('last_error'), reason='MAX_ATTEMPTS_REACHED', finished_at=now())
        self.save()

    def finish(self) -> str:
        statuses = [r['status'] for r in self.state['rows'].values()]
        ok = {'DONE', 'REUSED', 'NOT_APPLICABLE'}
        failed = [s for s in statuses if s in ('FAILED', 'FAILED_WITH_EVIDENCE', 'RUNNING', 'PENDING')]
        overall = 'FAILED' if not statuses or not any(s in ok for s in statuses) else 'PARTIAL' if failed else 'COMPLETE'
        self.state.update(status=overall, finished_at=now(), exit_code=EXIT[overall])
        self.save()
        return overall

    def per_strategy(self) -> dict:
        out = {}
        for r in self.state['rows'].values():
            current = out.get(r['strategy_id'])
            bad = r['status'] in ('FAILED', 'FAILED_WITH_EVIDENCE', 'RUNNING', 'PENDING')
            out[r['strategy_id']] = r['status'] if bad else (current or r['status'])
        return out
