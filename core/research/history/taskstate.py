"""Persistent work state of the 2026-11-10 engine-accounting and history-gap round (F01-F10 and one row per strategy), shown on the web.

One JSON file, outputs/engine_history_fix/state.json. Every task is in exactly one state; a terminal state carries its evidence. Updates are
whole-file atomic writes; nothing here runs work, it only records it.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / 'outputs/engine_history_fix/state.json'
STATES = ('PENDING', 'RUNNING', 'PASS', 'RETRYING', 'BLOCKED', 'FAILED', 'SKIPPED_DEPENDENCY')
TERMINAL = ('PASS', 'BLOCKED', 'FAILED', 'SKIPPED_DEPENDENCY')
MAX_REPAIRS = 3
ERROR_CLASSES = ('CODE_DEFECT', 'DATA_MISSING', 'DATA_INCONSISTENT', 'EXTERNAL_TEMPORARY', 'AUTHORIZATION', 'SECURITY', None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def input_hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]


def load(path: Path = STATE) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'round': '2026-11-10-engine-accounting-and-history-gaps-v1', 'tasks': {}, 'updated_at': None}


def _write(state: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.tmp-{os.getpid()}')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    os.replace(tmp, path)


def update(task_id: str, status: str, *, title: str | None = None, error_class: str | None = None, evidence: dict | list | str | None = None,
           checkpoint: str | None = None, inputs: str | None = None, retryable: bool | None = None, note: str | None = None, path: Path = STATE) -> dict:
    """Move one task; a RETRYING transition counts a repair round, and the fourth repair is refused (FAILED with evidence instead)."""
    if status not in STATES:
        raise ValueError(f'UNKNOWN_STATE: {status}')
    if error_class not in ERROR_CLASSES:
        raise ValueError(f'UNKNOWN_ERROR_CLASS: {error_class}')
    state = load(path)
    task = state['tasks'].get(task_id) or {'task_id': task_id, 'status': 'PENDING', 'repairs': 0, 'history': [], 'started_at': None}
    if status == 'RETRYING':
        if task['repairs'] >= MAX_REPAIRS:
            status, note = 'FAILED', f'{note or ""} FAILED_WITH_EVIDENCE: repair limit {MAX_REPAIRS} reached'.strip()
        else:
            task['repairs'] += 1
    if status == 'RUNNING' and not task.get('started_at'):
        task['started_at'] = _now()
    task.update(status=status, updated_at=_now())
    for key, value in (('title', title), ('error_class', error_class), ('evidence', evidence), ('checkpoint', checkpoint), ('inputs', inputs),
                       ('retryable', retryable), ('note', note)):
        if value is not None:
            task[key] = value
    if status in TERMINAL:
        task['finished_at'] = _now()
    if status == 'PASS':
        task['error_class'] = None  # a passed task carries no open error; the history keeps the earlier failures
    task['history'].append({'at': task['updated_at'], 'status': status, 'note': note})
    state['tasks'][task_id] = task
    state['updated_at'] = _now()
    _write(state, path)
    return task


def summary(state: dict | None = None) -> dict:
    state = state or load()
    counts = {s: 0 for s in STATES}
    for task in state['tasks'].values():
        counts[task['status']] += 1
    open_tasks = [t['task_id'] for t in state['tasks'].values() if t['status'] not in TERMINAL]
    return {'counts': counts, 'open': open_tasks, 'all_terminal': not open_tasks, 'updated_at': state.get('updated_at')}
