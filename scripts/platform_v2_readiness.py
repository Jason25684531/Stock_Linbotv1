"""Produce the readiness report from existing historical and V1 decisions."""

import json
import os
import sys
import tempfile
from pathlib import Path

from core.backtest.run_store import canonical_hash
from core.research.platform.evidence import validate_evidence_fields
from core.research.platform.readiness import build_readiness
from core.research.reference.catalog import capabilities
from scripts import historical_foundation, strategy_intelligence


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'outputs/platform_v2'


def verify_result(sid, result, decision, context):
    """Recalculate V1 trust; stale or tampered analysis is never made runnable."""
    identity = result.get('identity_inputs', {})
    run_id, state = strategy_intelligence.pick_run(sid, decision, context['evaluation']['snapshot_id'])
    if run_id:
        from core.backtest import run_store
        record = run_store.load_run(run_id, root=strategy_intelligence.HISTORY_RUNS)['run']
        if not record.get('period') or str(record['period'][1])[:10] > '2025-12-31':
            raise ValueError('research run exceeds OD-2 cutoff')
    run = strategy_intelligence.load_run(run_id) if run_id else None
    trust, _ = strategy_intelligence.trust_for(context, sid, run, state)
    years = {str(year): status for year, status in trust.items()}
    valid = (result.get('strategy_id') == sid
             and result.get('identity') == 'SI1-' + canonical_hash(identity)[:16]
             and identity.get('snapshot_id') == context['evaluation']['snapshot_id']
             and identity.get('evaluation_id') == context['evaluation']['evaluation_id']
             and identity.get('code') == context['code']
             and identity.get('run_id') == run_id
             and identity.get('identity_status') == state
             and identity.get('state') == decision.get('state')
             and identity.get('manifest_sha256') == (run['manifest_sha256'] if run else None)
             and (result.get('run') is None if run is None else
                  (result.get('run') or {}).get('run_id') == run_id
                  and result['run'].get('identity_status') == state)
             and result.get('year_status') == years)
    updated = result | {'year_status': years}
    if not valid:
        updated |= {'year_status': {}, 'status': 'IDENTITY_STALE'}
        if result.get('run'):
            updated['run'] = result['run'] | {'identity_status': 'IDENTITY_STALE'}
    return updated


def v2_baseline():
    """{strategy_id: publishable} of the stored V2 baseline runs, or None when no baseline run exists yet."""
    from core.backtest import run_store
    from scripts import platform_v2
    found = {}
    for sid in platform_v2.readiness():
        run_id = platform_v2.baseline_run(sid)
        if run_id:
            found[sid] = bool(run_store.load_run(run_id, root=platform_v2.RUNS)['run'].get('publishable'))
    return found or None


def build_verified():
    evaluation = historical_foundation.latest_evaluation()
    if not evaluation:
        raise ValueError('V1 evaluation missing')
    snapshot = historical_foundation.snapshot(evaluation['snapshot_id'])
    if not snapshot or not snapshot.manifest.get('window') or snapshot.manifest['window'][1] > '2025-12-31':
        raise ValueError('research snapshot exceeds OD-2 cutoff')
    context = strategy_intelligence.load_context()
    if context['evaluation']['evaluation_id'] != evaluation['evaluation_id']:
        raise ValueError('evaluation changed during readiness calculation')
    if context['bars']['trade_date'].astype(str).str[:10].max() > '2025-12-31':
        raise ValueError('research bars exceed OD-2 cutoff')
    latest = strategy_intelligence.read_json(strategy_intelligence.OUT / 'LATEST.json')
    if not evaluation or not latest or latest.get('snapshot_id') != evaluation['snapshot_id']:
        raise ValueError('V1 evaluation and result snapshot do not match')
    catalog = [{'strategy_id': c.strategy_id, 'family': c.family} for c in capabilities()]
    decisions = list(context['plan'].values())
    results = {}
    for sid, identity in latest['strategies'].items():
        filename = sid.replace(':', '__') + '.json'
        result = strategy_intelligence.read_json(strategy_intelligence.OUT / 'results' / identity / filename)
        if not result or result.get('identity') != identity or result.get('strategy_id') != sid:
            continue
        results[sid] = verify_result(sid, result, context['plan'][sid], context)
    rows = build_readiness(catalog, decisions, results, v2_baseline())
    evidence = {'data_class': 'REAL_RESEARCH', 'as_of_date': '2025-12-31',
                'source_timestamp': latest['finished_at'], 'data_trust': 'PARTIAL',
                'identity': 'SR1-' + canonical_hash(rows)[:16],
                'labels': ['RESEARCH_ONLY', 'NOT_CERTIFIED'],
                'snapshot_id': evaluation['snapshot_id'], 'evaluation_id': evaluation['evaluation_id']}
    return evidence, {'strategies': rows}


def generate(replace=False):
    """replace=True archives the current file under acceptance/ first (never deleted) and then replaces it atomically."""
    evidence, payload = build_verified()
    # The only real output path computes its own evidence from the pinned V1 inputs.
    validate_evidence_fields(evidence, 'readiness')
    for row in payload['strategies']:
        if any(int(year) > 2025 for year in row['year_status']):
            raise ValueError('research trust years exceed OD-2 cutoff')
        if row['backtest_window'] and any(str(day)[:10] > '2025-12-31' for day in row['backtest_window']):
            raise ValueError('research window exceeds OD-2 cutoff')
    content = json.dumps({'evidence': evidence, 'result': payload}, ensure_ascii=False, indent=2) + '\n'
    OUTPUT.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=OUTPUT, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        target = OUTPUT / 'STRATEGY_READINESS_21.json'
        if replace and target.exists():
            archive = OUTPUT / 'acceptance' / 'readiness_history' / ('STRATEGY_READINESS_21_' + json.loads(target.read_text(encoding='utf-8'))['evidence']['identity'] + '.json')
            archive.parent.mkdir(parents=True, exist_ok=True)
            if not archive.exists():
                archive.write_bytes(target.read_bytes())
            os.replace(temporary, target)
            temporary = None
        else:
            os.link(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    rows = payload['strategies']
    return rows


if __name__ == '__main__':
    print(f"{len(generate(replace='--replace' in sys.argv))} strategies")
