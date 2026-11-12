"""Evaluation Profile: per-type questions, metrics and edge checks; thresholds only from four allowed sources (design D3)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from core.backtest.run_store import canonical_hash

TYPES = ('trend_momentum', 'mean_reversion', 'defensive', 'fundamental', 'institutional_flow')
DEFINITIONAL = {('profit_factor', '>', 1), ('expectancy', '>', 0), ('sharpe', '>', 0), ('sortino', '>', 0), ('cagr_stress', '>', 0),
                ('forward_return_5_net', '>', 0), ('forward_return_20_net', '>', 0)}  # break-even boundaries only (net of costs where costs apply)
BENCHMARK_METRICS = {'excess_cagr', 'max_drawdown', 'downside_capture'}  # compared with the same-period total return index


def validate(data: dict) -> dict:
    for name, spec in data['types'].items():
        if name not in TYPES:
            raise ValueError(f'UNKNOWN_TYPE: {name}')
        for check in spec.get('edge_checks', []):
            key = (check['metric'], check['op'], check.get('value'))
            ok = (check['source'] == 'DEFINITIONAL' and key in DEFINITIONAL) or \
                 (check['source'] == 'BENCHMARK_SAME_PERIOD' and check.get('value') == 'benchmark' and check['metric'] in BENCHMARK_METRICS)
            if not ok:
                raise ValueError(f'THRESHOLD_SOURCE: {name} {key} {check["source"]}')
    for sid, row in data['strategies'].items():
        if row.get('primary_type') not in data['types'] or row['primary_type'] in row.get('secondary_types', []):
            raise ValueError(f'PRIMARY_TYPE: {sid}')
    if (data['targets']['cagr_basic'], data['targets']['cagr_priority']) != (0.05, 0.10):
        raise ValueError('THRESHOLD_SOURCE: CAGR targets are the Owner targets 5% and 10%')
    return data


def profile_id(data: dict) -> str:
    return 'EP1-' + canonical_hash(data)[:16]


def load(path) -> dict:
    return validate(json.loads(Path(path).read_text(encoding='utf-8')))


def _versions(path: Path) -> list:
    if not path.is_file():
        return []
    record = json.loads(path.read_text(encoding='utf-8'))
    return record['versions'] if 'versions' in record else [record]  # the first freeze was a single record


def freeze(data: dict, path, runs, reason: str = 'initial freeze') -> dict:
    """Append-only: an unchanged profile returns its existing version; a changed one is appended with the reason and the runs
    stored at that moment, so the record shows that the change was made before any new run was analysed."""
    path = Path(path)
    versions, pid = _versions(path), profile_id(validate(data))
    if versions and versions[-1]['profile_id'] == pid:
        return versions[-1]
    record = {'profile_id': pid, 'frozen_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'runs': list(runs), 'reason': reason}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps({'versions': versions + [record]}, indent=1, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)
    return record


def check_frozen(data: dict, path) -> str:
    """Only the latest frozen version may produce results."""
    versions = _versions(Path(path))
    frozen = versions[-1]['profile_id'] if versions else None
    if frozen != profile_id(data):
        raise ValueError(f'PROFILE_NOT_FROZEN: {profile_id(data)} != {frozen}')
    return frozen
