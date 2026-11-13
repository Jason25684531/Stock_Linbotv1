"""Validate provenance before writing research platform results."""

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

OUTPUT_ROOT = Path(__file__).resolve().parents[3] / 'outputs' / 'platform_v2'

_COMMON = {'data_class', 'as_of_date', 'source_timestamp', 'data_trust', 'identity', 'labels'}
_BY_KIND = {
    'backtest': {'run_id', 'snapshot_id', 'engine_version', 'rule_version'},
    'portfolio': {'run_id', 'snapshot_id', 'engine_version', 'rule_version'},
    'prediction': {'model_version', 'evaluation_id', 'snapshot_id'},
    'selection': {'strategy_id', 'source_table', 'read_time'},
    'readiness': {'snapshot_id', 'evaluation_id'},
}


def validate_evidence_fields(evidence, kind):
    """Check provenance fields and sealed dates; does not authorize writing."""
    if not isinstance(evidence, dict):
        raise ValueError('evidence must be an object')
    if kind not in _BY_KIND:
        raise ValueError(f'unknown evidence kind: {kind}')
    required = _COMMON | _BY_KIND[kind]
    missing = sorted(key for key in required if not evidence.get(key))
    if kind in ('backtest', 'portfolio') and not (evidence.get('strategy_id') or evidence.get('members')):
        missing.append('strategy_id or members')
    if missing:
        raise ValueError('missing evidence: ' + ', '.join(missing))
    for key in required - {'labels'}:
        if not isinstance(evidence[key], str) or not evidence[key].strip():
            raise ValueError(f'invalid {key}')
    if kind in ('backtest', 'portfolio'):
        for key in ('strategy_id', 'members'):
            if key in evidence and evidence[key] is not None:
                value = evidence[key]
                valid = isinstance(value, str) and bool(value.strip()) if key == 'strategy_id' else (
                    isinstance(value, list) and bool(value) and all(isinstance(x, str) and x.strip() for x in value))
                if not valid:
                    raise ValueError(f'invalid {key}')
    try:
        date.fromisoformat(evidence['as_of_date'])
        datetime.fromisoformat(evidence['source_timestamp'].replace('Z', '+00:00'))
        if kind == 'selection':
            datetime.fromisoformat(evidence['read_time'].replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('invalid evidence date or timestamp') from exc
    if evidence['data_class'] not in ('REAL_RESEARCH', 'DEMO'):
        raise ValueError('invalid data_class')
    if evidence['data_class'] == 'REAL_RESEARCH' and date.fromisoformat(evidence['as_of_date']) > date(2025, 12, 31):
        raise ValueError('research date exceeds 2025-12-31')
    if evidence['data_trust'] not in ('VERIFIED', 'PARTIAL', 'DATA_BLOCKED', 'GOVERNED_ONLY', 'IDENTITY_STALE'):
        raise ValueError('data_trust requires verified V1 source')
    if not isinstance(evidence['labels'], list) or not all(isinstance(x, str) for x in evidence['labels']) or not {'RESEARCH_ONLY', 'NOT_CERTIFIED'}.issubset(evidence['labels']):
        raise ValueError('missing research labels')


def validate_evidence(evidence, kind):
    """Reject real records when no authoritative V1 check has been performed."""
    validate_evidence_fields(evidence, kind)
    if evidence['data_class'] == 'REAL_RESEARCH':
        raise ValueError('trusted V1 data_trust source not integrated')
    if evidence['data_trust'] == 'VERIFIED':
        raise ValueError('data_trust requires verified V1 source')


def write_result(path, evidence, result, kind):
    """Public writer: real results require a dedicated source-verified path."""
    validate_evidence(evidence, kind)
    root = OUTPUT_ROOT.resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(root):
        raise ValueError('result path outside output root')
    is_demo = path.is_relative_to(root / 'demo')
    if is_demo != (evidence['data_class'] == 'DEMO'):
        raise ValueError('DEMO and REAL_RESEARCH paths must be separate')
    content = json.dumps({'evidence': evidence, 'result': result}, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
