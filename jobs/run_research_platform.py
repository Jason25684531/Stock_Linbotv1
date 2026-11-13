"""Research pipeline of the platform V2 (change 2026-11-12-integrated-research-platform-v2, design D9).

Steps: data trust check, V2 baseline runs (identical identities reused), analysis of every stored run, model evaluation, prediction
tracking (<= 2025-12-31), equal-weight portfolio baseline, health and governance reconciliation. Each step persists its status, input
identity and outputs under outputs/platform_v2/pipeline/steps; a completed step of identical identity is reused; a failure is classified
CODE, DATA, ENVIRONMENT, GOVERNANCE or TEST and is not retried after three failures; a failed step only skips the steps that depend
on it. Exit 0 when every step completed, 2 when some failed, 1 when none did. --dry-run lists the steps and writes nothing.
It never writes MySQL, never sends LINE and is not an operating-system schedule.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/platform_v2/pipeline'
MAX_ATTEMPTS = 3
CLASSES = ('CODE', 'DATA', 'ENVIRONMENT', 'GOVERNANCE', 'TEST')


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def classify(error: BaseException) -> str:
    text = f'{type(error).__name__}: {error}'
    if any(k in text for k in ('OD-2', 'GOVERNANCE', 'NOT_FROZEN', 'MEMBER_NOT')):
        return 'GOVERNANCE'
    if isinstance(error, AssertionError):
        return 'TEST'
    if isinstance(error, (MemoryError, PermissionError, TimeoutError)) or any(k in text for k in ('GB_FREE', 'INSUFFICIENT_DISK')):
        return 'ENVIRONMENT'
    if isinstance(error, (FileNotFoundError, KeyError)) or any(k in text for k in ('DATA', 'MISSING', 'SNAPSHOT')):
        return 'DATA'
    return 'CODE'


def _code_identity(*names) -> str:
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode('utf-8'))
        digest.update((ROOT / name).read_bytes())
    return digest.hexdigest()[:16]


# step name -> (dependencies, identity inputs, callable); the platform module is imported lazily so --dry-run reads no data
def _baseline_preflight(p2):
    import os
    import shutil
    import psutil
    from core.research.reference import batch_job
    from scripts import historical_foundation

    def check_slot():
        state = batch_job.state()
        if (state.get('kind') != 'platform_pipeline' or state.get('status') != 'RUNNING'
                or state.get('pid') != os.getpid()):
            raise RuntimeError(f"SLOT_CONFLICT: pipeline does not own the active work slot: {state}")
        return {'kind': state['kind'], 'pid': state['pid']}

    def check_resources():
        available_gb = psutil.virtual_memory().available / 2 ** 30
        required_gb = historical_foundation.MIN_FREE_GB * 4
        disk_free_gb = shutil.disk_usage(ROOT).free / 2 ** 30
        if available_gb < required_gb:
            raise MemoryError(f'INSUFFICIENT_MEMORY: {available_gb:.1f} GB free; {required_gb} GB required for 4 workers')
        if disk_free_gb < 1:
            raise OSError(f'INSUFFICIENT_DISK: {disk_free_gb:.1f} GB free; 1 GB required')
        return {'memory_available_gb': round(available_gb, 1), 'memory_required_gb': required_gb,
                'disk_free_gb': round(disk_free_gb, 1), 'disk_required_gb': 1}

    check_slot()
    resources = check_resources()
    characterizations = [p2.characterize(strategy_id, '2024-01-02', '2024-06-28')
                         for strategy_id in ('mean_reversion', 'kd_oversold')]
    failed = [row['strategy_id'] for row in characterizations if row.get('status') != 'PASS']
    if failed:
        raise RuntimeError(f'CHARACTERIZATION_FAILED: {failed}')
    snapshots = {row.get('snapshot_id') for row in characterizations}
    if len(snapshots) != 1 or None in snapshots:
        raise RuntimeError(f'CHARACTERIZATION_SNAPSHOT_MISMATCH: {sorted(str(s) for s in snapshots)}')
    resources = check_resources()
    slot = check_slot()
    return {'characterizations': characterizations, 'resources': resources, 'slot': slot}


def _steps():
    from scripts import platform_v2 as p2

    def data_trust():
        from scripts import historical_foundation
        rows = p2.readiness()
        if len(rows) != 21:
            raise ValueError(f'DATA: readiness has {len(rows)} strategies, expected 21')
        late = [sid for sid, r in rows.items() if r['backtest_window'] and r['backtest_window'][1] > p2.CUTOFF]
        if late:
            raise ValueError(f'OD-2: windows after {p2.CUTOFF}: {late}')
        evaluation = historical_foundation.latest_evaluation()
        if not evaluation:
            raise FileNotFoundError('DATA: latest historical evaluation is missing')
        return {'strategies': len(rows), 'runnable': sum(bool(r['data_eligible']) for r in rows.values()),
                'snapshot_id': evaluation['snapshot_id']}

    def baseline():
        preflight = _baseline_preflight(p2)
        rows = p2.baseline(workers=4)
        failed = [r for r in rows if r['state'] != 'DONE']
        if failed:
            raise RuntimeError(f'{len(failed)} baseline runs failed: {failed[:3]}')
        return {'preflight': preflight, 'runs': {r['strategy_id']: r['run_id'] for r in rows}}

    def analysis():
        from core.backtest import run_store
        from scripts import strategy_intelligence as si
        ctx, done = None, []
        for row in run_store.history(root=p2.RUNS):
            if row.get('status') == 'SUCCESS' and not p2.analysis_path(row['run_id']).exists():
                ctx = ctx or si.load_context()
                p2.analyze(row['run_id'], ctx)
                done.append(row['run_id'])
        return {'analyzed': done}

    def models():
        existing = sorted(p2.MODELS.glob('PRED1-*/model.json')) if p2.MODELS.is_dir() else []
        from core.research.platform import prediction as pr
        frozen = pr.check_frozen(p2.MODEL_FREEZE)
        if len([p for p in existing if json.loads(p.read_text(encoding='utf-8'))['spec_hash'] == frozen]) == len(pr.SPEC['labels']['horizons']):
            return {'reused': [p.parent.name for p in existing]}
        return {'evaluated': p2.models()}

    def prediction_tracking():
        import pandas as pd
        out = {}
        for path in sorted(p2.MODELS.glob('PRED1-*/model.json')):
            model = json.loads(path.read_text(encoding='utf-8'))
            stored = path.parent / 'predictions.parquet'
            if model['status'] != 'DEV_WINDOW_SKILL':
                out[model['model_version']] = 'NOT_VALIDATED: no per-stock predictions stored or shown'
                continue
            dates = pd.read_parquet(stored, columns=['date']).date
            if str(dates.max())[:10] > p2.CUTOFF:
                raise ValueError(f'OD-2: predictions after {p2.CUTOFF} in {model["model_version"]}')
            out[model['model_version']] = {'rows': int(len(dates)), 'last_date': str(dates.max())[:10], 'realized_through': p2.CUTOFF}
        return out

    def portfolio():
        from core.backtest import run_store
        from core.research.platform.portfolio_v2 import check_weights
        from scripts import historical_foundation
        rows = p2.readiness()
        runs = {sid: p2.baseline_run(sid, rows) for sid, r in rows.items() if r['trusted_years']}
        members = [sid for sid, run_id in runs.items() if run_id and run_store.load_run(run_id, root=p2.RUNS)['run']['publishable']]
        if len(members) < 2:
            raise ValueError(f'MEMBER_NOT_PUBLISHABLE: only {len(members)} eligible members')
        base = p2.defaults()
        start = max(rows[sid]['backtest_window'][0] for sid in members)
        params = p2.check_portfolio(members, check_weights(members), start, p2.CUTOFF, base['capital'], base['costs'], base['rule'], 'REBAL_NONE', rows)
        return {'members': members, 'run_id': p2.portfolio_run(params, historical_foundation.latest_evaluation()['snapshot_id'])}

    def health():
        result = p2.governance()
        if result['status'] != 'MATCH':
            raise ValueError(f"GOVERNANCE: {len(result['differences'])} differences: {result['differences'][:3]}")
        return {'governance': result['status'], 'checked': result['checked']}

    code = ('scripts/platform_v2.py', 'scripts/platform_v2_engine.py', 'core/research/platform/engine_v2.py', 'core/research/platform/ledger_v2.py')
    return {
        'data_trust': ((), ('outputs/platform_v2/STRATEGY_READINESS_21.json',
                            'outputs/historical_foundation/evaluations/LATEST.json', 'jobs/run_research_platform.py'), data_trust),
        'baseline': (('data_trust',), code + ('jobs/run_research_platform.py', 'outputs/platform_v2/STRATEGY_READINESS_21.json'), baseline),
        'analysis': (('baseline',), ('scripts/platform_v2.py', 'core/research/platform/analysis.py'), analysis),
        'models': (('data_trust',), ('core/research/platform/prediction.py', 'outputs/platform_v2/model_profile_freeze.json'), models),
        'prediction_tracking': (('models',), ('core/research/platform/prediction.py',), prediction_tracking),
        'portfolio': (('baseline',), code + ('core/research/platform/portfolio_v2.py',), portfolio),
        'health': ((), ('outputs/platform_v2/baseline.json',), health),
    }


STEP_NAMES = ('data_trust', 'baseline', 'analysis', 'models', 'prediction_tracking', 'portfolio', 'health')


def _state(name) -> dict:
    try:
        return json.loads((OUT / 'steps' / f'{name}.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _save(name, value):
    (OUT / 'steps').mkdir(parents=True, exist_ok=True)
    (OUT / 'steps' / f'{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding='utf-8')


def run(job_id, steps=None, dry_run=False) -> int:
    steps = steps if steps is not None else _steps() if not dry_run else None
    if dry_run:
        print(json.dumps({'command': 'research_pipeline', 'dry_run': True, 'steps': list(STEP_NAMES), 'writes': 'nothing'}), flush=True)
        return 0
    results = {}
    for name in STEP_NAMES:
        deps, inputs, fn = steps[name]
        identity_error = None
        try:
            input_identity = _code_identity(*inputs)
        except OSError as error:
            identity_error = error
            input_identity = hashlib.sha256(f'{type(error).__name__}:{error}'.encode('utf-8')).hexdigest()[:16]
        dependencies = {dep: results.get(dep, {}).get('key') for dep in deps}
        key = hashlib.sha256(json.dumps({'inputs': input_identity, 'dependencies': dependencies}, sort_keys=True).encode('utf-8')).hexdigest()[:16]
        previous = _state(name)
        blocked = [d for d in deps if results.get(d, {}).get('status') not in ('COMPLETE', 'REUSED')]
        if blocked:
            results[name] = {'status': 'SKIPPED', 'reason': f'DEPENDENCY_FAILED: {blocked}', 'attempts': previous.get('attempts', 0) if previous.get('key') == key else 0}
        elif previous.get('key') == key and previous.get('status') in ('COMPLETE', 'REUSED') and name not in ('analysis', 'health'):
            results[name] = previous | {'status': 'REUSED'}
        elif previous.get('key') == key and previous.get('attempts', 0) >= MAX_ATTEMPTS and previous.get('status') == 'FAILED':
            results[name] = previous | {'status': 'FAILED', 'reason': f"MAX_ATTEMPTS_REACHED: {previous.get('reason')}"}
        else:
            attempts = (previous.get('attempts', 0) if previous.get('key') == key and previous.get('status') == 'FAILED' else 0) + 1
            started = now()
            try:
                if identity_error:
                    raise identity_error
                outputs = fn()
                results[name] = {'status': 'COMPLETE', 'outputs': outputs, 'attempts': attempts, 'failure_class': None, 'reason': None}
            except Exception as error:
                results[name] = {'status': 'FAILED', 'attempts': attempts, 'failure_class': classify(error), 'reason': f'{type(error).__name__}: {str(error)[:400]}',
                                 'traceback': traceback.format_exc()[-2000:]}
            results[name] |= {'key': key, 'started_at': started, 'finished_at': now(), 'job_id': job_id}
            _save(name, results[name])
        print(json.dumps({'step': name, **{k: results[name].get(k) for k in ('status', 'failure_class', 'attempts', 'reason')}}, default=str), flush=True)
    done = [n for n, r in results.items() if r['status'] in ('COMPLETE', 'REUSED')]
    code = 0 if len(done) == len(STEP_NAMES) else 2 if done else 1
    status = {0: 'DONE', 2: 'PARTIAL', 1: 'FAILED'}[code]
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {'job_id': job_id, 'status': status, 'exit_code': code, 'updated_at': now(),
               'steps': {n: {k: r.get(k) for k in ('status', 'failure_class', 'attempts', 'reason', 'finished_at')} for n, r in results.items()}}
    (OUT / 'LATEST.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({'command': 'research_pipeline', 'status': status, 'exit_code': code}), flush=True)
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--workers', type=int, default=4)  # the job slot always passes it; the baseline uses 4 isolated workers
    parser.add_argument('--job-id', default=None)
    args = parser.parse_args(argv)
    return run(args.job_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-research', dry_run=args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
