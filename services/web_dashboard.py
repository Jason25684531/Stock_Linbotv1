"""Read-only Web V3 composition. No backtest, evaluation, or result writes."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The saved V2 analysis identity contract; no import of executable research scripts.
ANALYSIS_FILES = ('scripts/platform_v2.py', 'core/research/platform/analysis.py', 'scripts/strategy_intelligence.py',
                  'core/research/intelligence/performance.py', 'core/research/intelligence/scenarios.py',
                  'core/research/intelligence/evaluate.py', 'core/research/intelligence/feasibility.py', 'scripts/historical_report.py')


def analysis_version():
    from core.backtest.run_store import canonical_hash
    return canonical_hash({name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ANALYSIS_FILES})[:12]


def linked_analysis(record, envelope):
    """Require the current saved analysis and exact run evidence before exposing metrics."""
    if not isinstance(envelope, dict) or not record.get('publishable'):
        return None
    evidence, result = envelope.get('evidence') or {}, envelope.get('result') or {}
    if evidence.get('data_class') != 'REAL_RESEARCH' or record.get('evidence', {}).get('data_class') != 'REAL_RESEARCH':
        return None
    if any(evidence.get(k) != record.get(k) for k in ('run_id', 'strategy_id', 'snapshot_id', 'engine_version', 'rule_version')):
        return None
    if (result.get('run_id') != record.get('run_id') or result.get('strategy_id') != record.get('strategy_id')
            or result.get('identity_status') != 'CURRENT' or not result.get('publishable')
            or result.get('analysis_code') != analysis_version()):
        return None
    performance = result.get('performance') or {}
    series = performance.get('series') or {}
    dates = series.get('date') or []
    if (not dates or [dates[0], dates[-1]] != record.get('period')
            or any(len(series.get(k) or []) != len(dates) for k in ('nav_base', 'nav_stress', 'drawdown'))):
        return None
    return result


def clean(value):
    """JSON has no NaN; preserve unknown values as null, including chart gaps."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def strategies(routes, intelligence):
    """Join by catalog ID, retaining blocked cards and one current reviewed report per ID."""
    readiness, runs = routes._readiness(), routes._runs(routes.RUNS)
    rows = []
    for sid in intelligence._catalog():
        card = intelligence.result(sid) or {}
        ready = readiness.get(sid) or {}
        related = [r for r in runs if r.get('strategy_id') == sid]
        selected, report = None, None
        for run in related:
            if not run.get('publishable'):
                continue
            try:
                loaded = routes._loaded(routes.RUNS, run['run_id'])
                candidate = linked_analysis(loaded['run'], routes.analysis(run['run_id']))
            except Exception as error:
                from werkzeug.exceptions import HTTPException
                if not isinstance(error, (HTTPException, OSError, ValueError, KeyError)):
                    raise
                continue
            if candidate:
                selected, report = loaded['run'], candidate
                break
        state = ready.get('state') or card.get('status') or 'DATA_MISSING'
        trust = state if state in ('DATA_BLOCKED', 'GOVERNED_ONLY') else ('VERIFIED' if (report or {}).get('performance', {}).get('verified') else 'PROVISIONAL')
        dimensions = (report or {}).get('dimensions') or {}
        track = ((report or {}).get('performance') or {}).get('verified') or ((report or {}).get('performance') or {}).get('provisional') or {}
        fields = (card.get('definition') or {}).get('fields') or {}
        rows.append({'strategy_id': sid, 'name': card.get('display_name') or sid,
                     'type': (card.get('profile') or {}).get('primary_type', 'DATA_MISSING'),
                     'type_label': ((card.get('profile') or {}).get('type') or {}).get('label', 'DATA_MISSING'),
                     'card': card, 'readiness': ready, 'trust': trust, 'state': state,
                     'reason': ready.get('reason') or card.get('reason'), 'runs': related, 'selected': selected,
                     'report': report, 'track': track, 'target': dimensions.get('return_target') or {'status': 'INSUFFICIENT_DATA'},
                     'edge': dimensions.get('type_edge') or {}, 'fields_present': sum(bool(f.get('text')) for f in fields.values()),
                     'fields_total': len(fields)})
    return rows


def overview(rows, runs, models):
    return {'total': len(rows), 'runnable': sum(bool(r['readiness'].get('web_runnable')) for r in rows),
            'trust': dict(Counter(r['trust'] for r in rows)),
            'basic': sum(r['target']['status'] in ('RETURN_TARGET_BASIC', 'RETURN_TARGET_PRIORITY') for r in rows),
            'advanced': sum(r['target']['status'] == 'RETURN_TARGET_PRIORITY' for r in rows),
            'completed_runs': sum(r.get('status') == 'SUCCESS' for r in runs),
            'publishable_runs': sum(bool(r.get('publishable')) for r in runs),
            'models': [{'version': m['model_version'], 'status': m['status'], 'horizon': m['horizon']} for m in models]}


def resources():
    import psutil
    return {'available_memory_gib': round(psutil.virtual_memory().available / 2**30, 2),
            'free_disk_gib': round(shutil.disk_usage(ROOT).free / 2**30, 2),
            'logical_cpus': psutil.cpu_count(),
            'cache_present': (ROOT / 'outputs/platform_v2/feature_cache').is_dir()}


def operations_status():
    """Captured acceptance evidence, never a live health or activation assertion."""
    try:
        value = json.loads((ROOT / 'outputs/final_system_acceptance/operations_summary.json').read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not isinstance(value.get('captured_at'), str):
            raise ValueError('invalid acceptance summary')
        return {k: v for k, v in value.items() if k in (
            'captured_at', 'status', 'scope', 'last_success', 'last_failure', 'next_run',
            'recent_error', 'retry_count', 'recovery', 'backup', 'attention')}
    except (OSError, ValueError):
        return {'status': 'UNAVAILABLE', 'scope': 'Acceptance evidence unavailable; live operations unverified'}


def report_meta(record, envelope, card):
    return {'Definition ID (V1 card)': (card.get('definition') or {}).get('definition_id'),
            'Profile Version (V1 card)': (card.get('profile') or {}).get('profile_id'),
            'Evaluation ID (V1 card)': (card.get('versions') or {}).get('evaluation_id'),
            'V2 analysis version': ((envelope or {}).get('result') or {}).get('analysis_code'),
            'Report Generated At': ((envelope or {}).get('evidence') or {}).get('source_timestamp'),
            'Source Files': f"outputs/platform_v2/runs/{record['run_id']}/manifest.json; outputs/platform_v2/analysis/{record['run_id']}__{((envelope or {}).get('result') or {}).get('analysis_code', 'DATA_MISSING')}.json"}
