"""Read-only, fail-closed V4 publication. No backtest imports or GET computations."""
import json
from pathlib import Path

from core.backtest import run_store

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/strategy_v4'
NAMES = {'mad_distance': 'MAD Distance', 'volatility_adjusted_momentum': 'Volatility Adjusted Momentum'}


def load(strategy_id):
    if strategy_id not in NAMES:
        raise KeyError(strategy_id)
    blocked = {'strategy_id': strategy_id, 'name': NAMES[strategy_id], 'status': 'BLOCKED', 'reason': 'VALIDATION_EVIDENCE_MISSING'}
    try:
        path = OUT / 'results' / f'{strategy_id}.json'
        marker = json.loads((OUT / 'publication' / f'{strategy_id}.json').read_text(encoding='utf-8'))
        if not isinstance(marker, dict) or marker.get('status') != 'PASS' or marker.get('strategy_id') != strategy_id:
            return blocked
        hashes = marker.get('sha256')
        if not isinstance(hashes, dict):
            return blocked
        if hashes.get(path.relative_to(ROOT).as_posix()) != run_store.file_hash(path):
            return blocked | {'reason': 'RESULT_HASH_MISMATCH'}
        for name, digest in hashes.items():
            source = (ROOT / name).resolve()
            if not source.is_relative_to(ROOT.resolve()) or run_store.file_hash(source) != digest:
                return blocked | {'reason': 'SOURCE_HASH_MISMATCH'}
        result = json.loads(path.read_text(encoding='utf-8'))
        if result['strategy_id'] != strategy_id or result['status'] != 'COMPARABLE':
            return blocked | {'reason': 'NO_TRUSTED_COMPARISON'}
        for label in ('original', 'fixed'):
            track = result[label]
            folder = (ROOT / track['source']).resolve()
            allowed = ROOT / ('outputs/platform_v2/runs' if label == 'original' else 'outputs/strategy_v4/runs')
            if folder.parent != allowed.resolve() or folder.name != track['run_id']:
                return blocked | {'reason': 'INVALID_RUN_SOURCE'}
            loaded = run_store.load_run(folder.name, root=folder.parent)
            audit_path = OUT / 'acceptance' / f'ledger_{folder.name}.json'
            if hashes.get(audit_path.relative_to(ROOT).as_posix()) != run_store.file_hash(audit_path):
                return blocked | {'reason': 'LEDGER_HASH_MISSING'}
            audit = json.loads(audit_path.read_text(encoding='utf-8'))
            if (not track['publishable'] or not loaded['run']['publishable']
                    or audit['manifest_sha256'] != run_store.file_hash(folder / 'manifest.json')
                    or track['manifest_sha256'] != audit['manifest_sha256']
                    or any(audit['tracks'].get(t, {}).get('status') != 'PUBLISHABLE' for t in ('base', 'stress'))):
                return blocked | {'reason': 'NOT_PUBLISHABLE'}
        return result | {'name': NAMES[strategy_id], 'reason': None}
    except (OSError, ValueError, KeyError, TypeError):
        return blocked


def percentage(cell):
    if not cell or cell.get('value') is None:
        return '無法計算：' + ((cell or {}).get('reason') or 'DATA_MISSING')
    return f"{cell['value'] * 100:.2f}%"
