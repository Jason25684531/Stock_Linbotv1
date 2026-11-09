"""Immutable backtest run store: one directory per run ID, a SHA256 manifest, and per-strategy pointers.

outputs/backtest_runs/<run_id>/            run.json, nav.csv, holdings.csv, trades.csv, picks.csv, metrics.json, [analysis.json], manifest.json
outputs/backtest_runs/latest/successful/   last successful single-strategy run per strategy
outputs/backtest_runs/latest/reference/    last registered baseline (RESEARCH / OFFICIAL_ARCHIVED, reference=true) per strategy
A run is written to a staging directory, hashed, re-verified and renamed; pointers move only after that. A failure writes FAILED + error.json.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil

import pandas as pd

ROOT = Path(__file__).resolve().parents[2] / 'outputs/backtest_runs'
RUN_ID = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{8}$')
STATUS_CLASSES = ('OFFICIAL_ARCHIVED', 'RESEARCH', 'EXPERIMENTAL', 'DATA_BLOCKED')
REQUIRED = ('strategy_id', 'parameter_hash', 'source_hash', 'data_version', 'comparison_group', 'certification_status', 'status_class')
REQUIRED_FILES = ('nav', 'holdings', 'trades', 'picks', 'metrics')
DEV_END = '2025-12-31'
WEB_GROUP_PREFIX = 'WEB_ADHOC'


class ManifestMismatch(ValueError):
    pass


def new_run_id():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dump(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=1, allow_nan=False, default=str)


def _write_frames(folder, frames):
    for name, value in frames.items():
        if isinstance(value, pd.DataFrame):
            value.to_csv(folder / f'{name}.csv', index=False, encoding='utf-8', mode='x')
        else:
            _dump(folder / f'{name}.json', value)


def _seal(folder):
    manifest = {f.name: file_hash(f) for f in sorted(folder.iterdir()) if f.name != 'manifest.json'}
    _dump(folder / 'manifest.json', manifest)
    return manifest


def _safe(strategy_id):
    """Pointer file name: readable part plus a hash, so IDs with ':' or '/' never collide or escape the folder."""
    return re.sub(r'[^A-Za-z0-9_.-]', '_', strategy_id)[:60] + '-' + hashlib.sha256(strategy_id.encode('utf-8')).hexdigest()[:8]


def _pointer(root, kind, strategy_id, run_id):
    folder = Path(root) / 'latest' / kind
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{_safe(strategy_id)}.json'
    staging = target.with_suffix('.json.tmp')
    staging.write_text(json.dumps({'strategy_id': strategy_id, 'run_id': run_id}, ensure_ascii=False), encoding='utf-8')
    os.replace(staging, target)


def _finalize(staging, final):
    if final.exists():
        raise FileExistsError(f'RUN_EXISTS:{final.name}')
    os.rename(staging, final)


def _check(record, frames):
    missing = [key for key in REQUIRED if record.get(key) in (None, '')]
    if missing:
        raise ValueError('RUN_IDENTITY_MISSING:' + ','.join(missing))
    status = record['status_class']
    if status not in STATUS_CLASSES:
        raise ValueError(f'UNKNOWN_STATUS_CLASS:{status}')
    if record.get('reference') and status not in ('RESEARCH', 'OFFICIAL_ARCHIVED'):
        raise ValueError('REFERENCE_REQUIRES_REGISTERED_BASELINE')
    if status == 'DATA_BLOCKED':
        if not record.get('reason') or set(frames) - {'metrics'}:
            raise ValueError('DATA_BLOCKED_RECORD_HAS_NO_PERFORMANCE')
        return
    if missing := [name for name in REQUIRED_FILES if name not in frames]:
        raise ValueError('RUN_FILES_MISSING:' + ','.join(missing))
    nav = frames['nav']
    research = not str(record['comparison_group']).startswith(WEB_GROUP_PREFIX)
    if research and len(nav) and str(nav.iloc[:, 0].astype(str).max())[:10] > DEV_END:
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')


def save_run(record, frames, *, root=None, run_id=None):
    """Write a finished run; return its run ID. Raises on any problem, leaving pointers untouched."""
    _check(record, frames)
    root = Path(root or ROOT)
    root.mkdir(parents=True, exist_ok=True)
    run_id = run_id or new_run_id()
    if not RUN_ID.match(run_id):
        raise ValueError('INVALID_RUN_ID')
    staging, final = root / f'.{run_id}.tmp', root / run_id
    if final.exists():
        raise FileExistsError(f'RUN_EXISTS:{run_id}')
    staging.mkdir(exist_ok=False)
    try:
        _dump(staging / 'run.json', {**record, 'run_id': run_id, 'status': 'SUCCESS',
                                     'created_at': datetime.now(timezone.utc).isoformat()})
        _write_frames(staging, frames)
        manifest = _seal(staging)
        if any(file_hash(staging / name) != digest for name, digest in manifest.items()):
            raise ManifestMismatch('WRITE_VERIFY_FAILED')
        _finalize(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    ids = record.get('strategy_ids') or [record['strategy_id']]
    if len(ids) == 1:
        _pointer(root, 'successful', ids[0], run_id)
        if record.get('reference'):
            _pointer(root, 'reference', ids[0], run_id)
    return run_id


def save_failure(record, error, *, root=None):
    """FAILED run: run.json + error.json + manifest, never a pointer move."""
    root = Path(root or ROOT)
    root.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id()
    staging, final = root / f'.{run_id}.tmp', root / run_id
    staging.mkdir(exist_ok=False)
    _dump(staging / 'run.json', {**record, 'run_id': run_id, 'status': 'FAILED', 'created_at': datetime.now(timezone.utc).isoformat()})
    _dump(staging / 'error.json', {'error_type': type(error).__name__, 'message': str(error)[:2000]})
    _seal(staging)
    _finalize(staging, final)
    return run_id


def load_run(run_id, *, root=None):
    """Verified run: {'run': run.json, 'dir': Path, 'files': [names]}; ManifestMismatch on tampering, FileNotFoundError if unknown."""
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        raise FileNotFoundError('INVALID_RUN_ID')
    folder = Path(root or ROOT) / run_id
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    present = {f.name for f in folder.iterdir()} - {'manifest.json'}
    if present != set(manifest) or any(file_hash(folder / name) != digest for name, digest in manifest.items()):
        raise ManifestMismatch(f'MANIFEST_MISMATCH:{run_id}')
    return {'run': json.loads((folder / 'run.json').read_text(encoding='utf-8')), 'dir': folder, 'files': sorted(manifest)}


def read_frame(loaded, name):
    """A manifest-listed CSV or JSON of a verified run."""
    if f'{name}.csv' in loaded['files']:
        return pd.read_csv(loaded['dir'] / f'{name}.csv', dtype={'stock_id': str, 'ticker': str, 'asset_id': str})
    if f'{name}.json' in loaded['files']:
        return json.loads((loaded['dir'] / f'{name}.json').read_text(encoding='utf-8'))
    raise FileNotFoundError(name)


def history(*, root=None, strategy_id=None):
    """Every run (newest first) with a verified flag; FAILED runs included."""
    root = Path(root or ROOT)
    rows = []
    for folder in sorted((p for p in root.iterdir() if RUN_ID.match(p.name)), reverse=True) if root.is_dir() else []:
        try:
            run, verified = load_run(folder.name, root=root)['run'], True
        except (ManifestMismatch, OSError, ValueError):
            run, verified = {'run_id': folder.name, 'status': 'UNVERIFIED'}, False
        ids = run.get('strategy_ids') or [run.get('strategy_id')]
        if strategy_id is None or strategy_id in ids:
            rows.append({**run, 'verified': verified})
    return sorted(rows, key=lambda r: (r.get('created_at', ''), r['run_id']), reverse=True)


def latest(strategy_id, kind='successful', *, root=None):
    """Verified run named by the pointer, or None."""
    pointer = Path(root or ROOT) / 'latest' / kind / f'{_safe(strategy_id)}.json'
    if not pointer.is_file():
        return None
    try:
        return load_run(json.loads(pointer.read_text(encoding='utf-8'))['run_id'], root=root)['run']
    except (ManifestMismatch, OSError, ValueError, KeyError):
        return None
