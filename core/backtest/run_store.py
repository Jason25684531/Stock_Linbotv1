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
# evidence_version 2 (change 2026-11-09-autonomous-data-readiness-v1): admission copy plus complete post-run evidence
REQUIRED_FILES_V2 = REQUIRED_FILES + ('positions_daily', 'trades_stress', 'open_positions', 'costs', 'valuation_events', 'pit_detail', 'health')
ADMISSION_KEYS = ('admission_id', 'trial_id', 'hypothesis_id', 'code_sha256', 'inputs_sha256', 'input_paths', 'config', 'pinned',
                  'valuation_policy', 'research_window', 'admitted_at')
PINNED_ROLES = ('benchmark', 'calendar', 'data')
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
    if record.get('evidence_version') == 2:
        admission, empty = record.get('admission_record') or {}, (None, '', {}, [])
        incomplete = [k for k in ADMISSION_KEYS if admission.get(k) in empty]
        incomplete += [f'pinned.{role}' for role in PINNED_ROLES if (admission.get('pinned') or {}).get(role) in empty]
        incomplete += [name for name in REQUIRED_FILES_V2 if name not in frames]
        if incomplete:
            raise ValueError('RUN_ADMISSION_INCOMPLETE:' + ','.join(incomplete))
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


def _when(text):
    return datetime.fromisoformat(str(text).replace('Z', '+00:00'))


def evidence_status(loaded, ledger_row=None, ledger_verified=False, hash_input=None) -> dict:
    """ADMISSION_VERIFIED only against the trusted trial ledger row (the caller reads and verifies the ledger; core never imports scripts).
    An admission_record inside run.json, or hashes taken after the run, never upgrade a run on their own.
    hash_input(path) -> sha256 recomputes admitted inputs for reproducible_offline (skipped when None)."""
    run = loaded['run']
    admission, trial = run.get('admission_record') or {}, run.get('trial_id')  # v1 'admission' is the kind string (TRIAL / REPLAY)
    if not trial:
        return {'status': 'HISTORICAL_EVIDENCE_MISSING', 'reasons': ['NO_TRIAL'], 'reproducible_offline': False}
    if ledger_row is None:
        return {'status': 'POST_RUN_NOT_ADMISSION_VERIFIED' if admission else 'HISTORICAL_EVIDENCE_MISSING',
                'reasons': ['LEDGER_ROW_MISSING'], 'reproducible_offline': False}
    if not admission:
        return {'status': 'POST_RUN_NOT_ADMISSION_VERIFIED', 'reasons': ['NO_ADMISSION_BLOCK'], 'reproducible_offline': False}
    reasons = [] if ledger_verified else ['LEDGER_UNVERIFIED']
    if run.get('evidence_version') != 2:
        reasons.append('NOT_EVIDENCE_VERSION_2')
    # Bind the admission to what this run executed: parameter_hash and source_hash are computed from the run's own config and code files
    # when it is saved, so a copied admission_record cannot vouch for a run made with other parameters or code.
    if run.get('parameter_hash') != ledger_row.get('config_sha256'):
        reasons.append('RUN_PARAMETERS_NOT_ADMITTED')
    if not ledger_row.get('code_sha256') or run.get('source_hash') != canonical_hash(ledger_row.get('code_sha256')):
        reasons.append('RUN_CODE_NOT_ADMITTED')
    if not (admission.get('admission_id') == trial == ledger_row.get('experiment_id')):
        reasons.append('ADMISSION_ID_MISMATCH')
    if not (ledger_row.get('hypothesis_id') == admission.get('hypothesis_id') == run.get('hypothesis_id')):
        reasons.append('HYPOTHESIS_MISMATCH')
    try:
        if not _when(ledger_row['at']) < _when(run['created_at']):
            reasons.append('ADMITTED_AFTER_RUN')
    except (KeyError, TypeError, ValueError):
        reasons.append('ADMISSION_TIME_UNKNOWN')
    if admission.get('admitted_at') != ledger_row.get('at'):
        reasons.append('ADMITTED_AT_MISMATCH')
    if ledger_row.get('config_sha256') != canonical_hash(admission.get('config')):
        reasons.append('CONFIG_HASH_MISMATCH')
    if not ledger_row.get('code_sha256') or ledger_row.get('code_sha256') != admission.get('code_sha256'):
        reasons.append('CODE_HASH_MISMATCH')
    ledger_inputs, admitted = ledger_row.get('inputs_sha256') or {}, admission.get('inputs_sha256') or {}
    for role in PINNED_ROLES:
        names = (admission.get('pinned') or {}).get(role)
        for name in ([names] if isinstance(names, str) else names or [None]):
            if name not in ledger_inputs:
                reasons.append(f'PINNED_INPUT_MISSING:{role}')
            elif ledger_inputs[name] != admitted.get(name):
                reasons.append(f'PINNED_INPUT_MISMATCH:{role}:{name}')
    if reasons:
        return {'status': 'POST_RUN_NOT_ADMISSION_VERIFIED', 'reasons': reasons, 'reproducible_offline': False}
    changed = [name for name, path in (admission.get('input_paths') or {}).items()
               if hash_input is None or not Path(path).exists() or hash_input(path) != ledger_inputs.get(name)]
    return {'status': 'ADMISSION_VERIFIED', 'reasons': [], 'reproducible_offline': not changed,
            'reproducibility_reasons': ['INPUTS_NOT_CHECKED'] if hash_input is None else [f'INPUT_CHANGED:{n}' for n in changed]}


def repair_pointers(*, root=None) -> list:
    """Point each single-strategy pointer at its newest verified successful run when an interrupted save left it older; never creates a run."""
    root = Path(root or ROOT)
    repairs = []
    for run in sorted(history(root=root), key=lambda r: (r.get('created_at', ''), r['run_id'])):
        ids = run.get('strategy_ids') or [run.get('strategy_id')]
        if not run['verified'] or run.get('status') != 'SUCCESS' or len(ids) != 1:
            continue
        for kind in ('successful', 'reference') if run.get('reference') else ('successful',):
            current = latest(ids[0], kind, root=root)
            if current is None or (current.get('created_at', ''), current['run_id']) < (run['created_at'], run['run_id']):
                _pointer(root, kind, ids[0], run['run_id'])
                repairs.append({'strategy_id': ids[0], 'kind': kind, 'run_id': run['run_id'],
                                'previous': None if current is None else current['run_id']})
    return repairs


def latest(strategy_id, kind='successful', *, root=None):
    """Verified run named by the pointer, or None."""
    pointer = Path(root or ROOT) / 'latest' / kind / f'{_safe(strategy_id)}.json'
    if not pointer.is_file():
        return None
    try:
        return load_run(json.loads(pointer.read_text(encoding='utf-8'))['run_id'], root=root)['run']
    except (ManifestMismatch, OSError, ValueError, KeyError):
        return None
