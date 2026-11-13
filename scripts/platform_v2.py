"""Platform V2 runs: parameter checks, RID2 identity, storage, the baseline batch and the characterization (change 2026-11-12, D3).

Runs live in outputs/platform_v2/runs (core.backtest.run_store layout, comparison group PLATFORM_V2:...). A run whose fill ledger does
not reconcile on either cost track is stored with publishable=false and its reason; pages show the reason, never its performance.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.platform.params import BOUNDS, CUTOFF, check_params, readiness  # noqa: E402  shared with the web form
OUT = ROOT / 'outputs/platform_v2'
RUNS = OUT / 'runs'
GROUP = 'PLATFORM_V2:'
LABELS = ['RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL', 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES']
ENGINE_FILES = ('core/research/platform/engine_v2.py', 'core/research/platform/ledger_v2.py', 'scripts/platform_v2_engine.py')


def defaults() -> dict:
    from scripts import legacy_baseline_run as legacy
    return {'capital': float(legacy.INITIAL_CAPITAL), 'costs': legacy._costs(), 'rule': 'LOT_ENGINE_V1'}


def identity(params, snapshot_id) -> str:
    """RID2: the V1 run identity of the same window (strategy, data, code) plus the V2 engine code, rule, capital and costs."""
    from core.backtest.run_store import canonical_hash
    from core.research.reference.catalog import capabilities
    from scripts import historical_foundation
    from scripts.research_workflow import sha256
    cap = {c.strategy_id: c for c in capabilities()}[params['strategy_id']]
    v1 = historical_foundation.history_identity(cap, snapshot_id, params['start'], params['end'])
    code = {name: sha256(ROOT / name) for name in ENGINE_FILES}
    return 'RID2-' + canonical_hash({'v1': v1, 'v2_code': code, 'rule': params['rule'], 'capital': params['capital'], 'costs': params['costs']})[:24]


def lookup(rid, root=None) -> str | None:
    from core.backtest import run_store
    root = Path(root or RUNS)
    for folder in sorted(root.glob('2*'), reverse=True):
        try:
            record = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
            if record.get('run_identity') == rid and record.get('status') == 'SUCCESS':
                run_store.load_run(folder.name, root=root)
                return folder.name
        except (OSError, ValueError):
            continue
    return None


def _metrics(nav, capital) -> dict:
    from core.research import trade_analytics
    out = {}
    for track, column in (('base', 'nav_base'), ('x2', 'nav_stress')):
        perf = trade_analytics.performance(pd.Series(nav[column].astype(float).to_numpy(), index=pd.to_datetime(nav['date'])), initial_cash=capital)
        out[track] = {name: perf[name] for name in ('total_return', 'sharpe', 'max_drawdown')}
    return out


def build_record(params, rid, run_id, snapshot_id, result) -> tuple[dict, dict]:
    """run.json record and frames; reconciliation on both tracks decides publishable."""
    from core.backtest.run_store import canonical_hash
    from core.research.platform.engine_v2 import ENGINE_VERSION, RULES
    from core.research.platform.evidence import validate_evidence_fields
    from core.research.platform.ledger_v2 import daily_ledger, reconcile
    tracks = result['tracks']
    checks = {name: reconcile(params['capital'], t['nav'], t['fills'], t['corporate_action_events'], t['positions_daily'], t['open_positions'])
              for name, t in tracks.items()}
    reasons = [f"{name}: {c['reason']} ({c['first_mismatch']})" for name, c in checks.items() if c['status'] != 'PUBLISHABLE']
    if result['feature_cache']['mismatches']:
        reasons.append('FEATURE_CACHE_MISMATCH')
    if tracks['base'].get('engine_error') or tracks['stress'].get('engine_error'):
        reasons.append('ENGINE_ERROR')
    base, stress = tracks['base'], tracks['stress']
    nav = pd.DataFrame({'date': [d for d, _ in base['nav']], 'nav_base': [v for _, v in base['nav']], 'nav_stress': [v for _, v in stress['nav']]})
    evidence = {'data_class': 'REAL_RESEARCH', 'as_of_date': params['end'], 'source_timestamp': datetime.now(timezone.utc).isoformat(),
                'data_trust': 'PARTIAL', 'identity': rid, 'labels': LABELS, 'strategy_id': params['strategy_id'], 'run_id': run_id,
                'snapshot_id': snapshot_id, 'engine_version': ENGINE_VERSION, 'rule_version': params['rule']}
    validate_evidence_fields(evidence, 'backtest')
    group = GROUP + canonical_hash({'snapshot_id': snapshot_id, 'rule': params['rule'], 'capital': params['capital'], 'costs': params['costs']})[:12]
    record = {'strategy_id': params['strategy_id'], 'strategy_ids': [params['strategy_id']], 'strategy_type': f"platform_v2_{result['family']}",
              'parameters': params, 'parameter_hash': canonical_hash(params), 'run_identity': rid, 'source_hash': rid, 'data_version': snapshot_id,
              'snapshot_id': snapshot_id, 'comparison_group': group, 'certification_status': 'NOT_CERTIFIED', 'status_class': 'EXPERIMENTAL',
              'labels': LABELS, 'period': [params['start'], params['end']], 'engine_version': ENGINE_VERSION, 'rule_version': params['rule'],
              'comparable_with_v1': RULES[params['rule']], 'platform_evidence_version': 2, 'publishable': not reasons, 'not_publishable_reasons': reasons,
              'reconciliation': checks, 'evidence': evidence, 'feature_cache': result['feature_cache'], 'accounting': base['accounting'],
              'ledger_version': result['ledger_version'], 'engine_error': base.get('engine_error') or stress.get('engine_error'),
              'data_trust_note': 'run level PARTIAL: per-year trust comes from the V1 run_trust in the analysis, never raised here'}
    holdings = pd.DataFrame([r for r in base['positions_daily'] if r['stock_id'] != 'CASH'], columns=['date', 'stock_id', 'shares', 'value'])
    frame = lambda rows, cols=None: pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols or ['date'])
    frames = {'nav': nav, 'trades': frame(base['trades'], ['stock_id', 'buy_date', 'sell_date']), 'trades_stress': frame(stress['trades'], ['stock_id', 'buy_date', 'sell_date']),
              'holdings': holdings, 'picks': pd.DataFrame(columns=['date', 'stock_id']), 'metrics': _metrics(nav, params['capital']),
              'positions_daily': frame(base['positions_daily'], ['date', 'stock_id', 'shares', 'value']),
              'positions_daily_stress': frame(stress['positions_daily'], ['date', 'stock_id', 'shares', 'value']),
              'fills': frame(base['fills'], ['fill_id']), 'fills_stress': frame(stress['fills'], ['fill_id']),
              'daily_ledger': pd.DataFrame(daily_ledger(base['nav'], base['positions_daily'])),
              'daily_ledger_stress': pd.DataFrame(daily_ledger(stress['nav'], stress['positions_daily'])),
              'corporate_actions': frame(base['corporate_action_events'], ['date', 'stock_id', 'event_id', 'kind', 'status']),
              'open_positions': base['open_positions'], 'open_positions_stress': stress['open_positions'],
              'costs': {'parameters': params['costs'], 'base': base['cost_summary'], 'stress': stress['cost_summary']},
              'valuation_events': {'base': base['valuation_events'], 'stress': stress['valuation_events']},
              'execution_events': {'base': base['execution_events'], 'stress': stress['execution_events']}}
    return record, frames


def run_one(params, snapshot_id, root=None) -> str:
    """Reuse an identical run, else execute and store (a failure is stored as FAILED and re-raised)."""
    from core.backtest import run_store
    from scripts.platform_v2_engine import execute
    root = Path(root or RUNS)
    rid = identity(params, snapshot_id)
    if existing := lookup(rid, root):
        return existing
    run_id = run_store.new_run_id()
    try:
        result = execute(params['strategy_id'], snapshot_id, params['start'], params['end'], params['capital'], params['costs'], params['rule'])
        record, frames = build_record(params, rid, run_id, snapshot_id, result)
    except Exception as error:
        run_store.save_failure({'strategy_id': params['strategy_id'], 'run_identity': rid, 'parameters': params, 'snapshot_id': snapshot_id}, error, root=root)
        raise
    return run_store.save_run(record, frames, root=root, run_id=run_id)


def baseline_params() -> list[dict]:
    """Default rule, V1 capital and costs, V1 window: one row per strategy with a stored V1 history run."""
    base = defaults()  # not check_params: two V1 runs (NO_VERIFIED_TRACK) are not web-runnable but still get a baseline for comparison
    return [{'strategy_id': sid, 'start': row['backtest_window'][0], 'end': row['backtest_window'][1], 'capital': base['capital'],
             'costs': base['costs'], 'rule': base['rule']} for sid, row in readiness().items() if row['state'] in ('RUN', 'REUSE') and row['backtest_window']]


def _worker(params, snapshot_id):
    try:
        return {'strategy_id': params['strategy_id'], 'state': 'DONE', 'run_id': run_one(params, snapshot_id)}
    except Exception as error:  # one strategy's failure is recorded; the batch continues
        return {'strategy_id': params['strategy_id'], 'state': 'FAILED', 'reason': f'{type(error).__name__}: {str(error)[:300]}'}


def baseline(workers=4) -> list[dict]:
    """The V2 baseline batch in isolated worker processes (spawn: no monkeypatch state is shared)."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from scripts import historical_foundation
    snapshot_id = historical_foundation.latest_evaluation()['snapshot_id']
    todo = sorted(baseline_params(), key=lambda p: (p['start'], p['strategy_id']))
    rows = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = []
        for params in todo:
            historical_foundation._wait_for_memory()
            futures.append(pool.submit(_worker, params, snapshot_id))
        for future in as_completed(futures):
            rows.append(future.result())
            print(json.dumps(rows[-1]), flush=True)
    return rows


def compare(v1, v2) -> list[str]:
    """Differences between a V1 result (nav frame with nav_base/nav_stress, trades frames) and a V2 run's frames (rtol 1e-9, atol 1e-12)."""
    import numpy as np
    problems = []
    a, b = v1['nav'].reset_index(drop=True), v2['nav'].reset_index(drop=True)
    if list(a.date.astype(str)) != list(b.date.astype(str)):
        problems.append('NAV_DATES')
    else:
        for column in ('nav_base', 'nav_stress'):
            if not np.allclose(a[column].astype(float), b[column].astype(float), rtol=1e-9, atol=1e-12):
                problems.append(f'NAV_VALUES:{column}')
    for name in ('trades', 'trades_stress'):
        x, y = v1[name].reset_index(drop=True), v2[name].reset_index(drop=True)
        if len(x) != len(y):
            problems.append(f'{name.upper()}_COUNT {len(x)} != {len(y)}')
            continue
        for column in x.columns:
            if column not in y:
                problems.append(f'{name.upper()}_COLUMN:{column}')
            elif pd.api.types.is_numeric_dtype(x[column]) and pd.api.types.is_numeric_dtype(y[column]):
                if not np.allclose(x[column].astype(float), y[column].astype(float), rtol=1e-9, atol=1e-12, equal_nan=True):
                    problems.append(f'{name.upper()}_VALUES:{column}')
            elif list(x[column].astype(str)) != list(y[column].astype(str)):
                problems.append(f'{name.upper()}_VALUES:{column}')
    return problems


def verify_baseline() -> dict:
    """B.8: every V2 baseline run against the V1 history run of the same strategy and window (both NAV tracks, base trades; V1 runs
    store no stress trades), its two-track reconciliation and the feature cache check."""
    from core.backtest import run_store
    from scripts import historical_foundation
    from scripts import strategy_intelligence as si
    evaluation = historical_foundation.latest_evaluation()
    plan = {r['strategy_id']: r for r in historical_foundation.plan(evaluation)}
    rows = []
    for params in baseline_params():
        sid = params['strategy_id']
        row = {'strategy_id': sid, 'window': [params['start'], params['end']], 'v1_run': plan[sid].get('run_id')}
        run_id = lookup(identity(params, evaluation['snapshot_id']))
        if not run_id:
            rows.append(row | {'status': 'V2_RUN_MISSING'})
            continue
        loaded = run_store.load_run(run_id, root=RUNS)
        record = loaded['run']
        v1 = si.load_run(row['v1_run'])
        v2 = {'nav': run_store.read_frame(loaded, 'nav'), 'trades': run_store.read_frame(loaded, 'trades')}
        v1_frames = {'nav': v1['nav'], 'trades': v1['trades']}
        problems = compare(v1_frames | {'trades_stress': v2['trades'].iloc[:0]}, v2 | {'trades_stress': v2['trades'].iloc[:0]})
        rows.append(row | {'v2_run': run_id, 'sessions': len(v2['nav']), 'trades': len(v2['trades']), 'fills': len(run_store.read_frame(loaded, 'fills')),
                           'identical_to_v1': not problems, 'differences': problems, 'publishable': record['publishable'],
                           'reconciliation': {k: v['status'] for k, v in record['reconciliation'].items()},
                           'not_publishable_reasons': record['not_publishable_reasons'], 'feature_cache': record['feature_cache'],
                           'status': 'PASS' if not problems and record['publishable'] else 'FAIL'})
    result = {'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'snapshot_id': evaluation['snapshot_id'], 'rows': rows,
              'status': 'PASS' if rows and all(r['status'] == 'PASS' for r in rows) else 'FAIL', 'governance': governance(write=False)}
    folder = OUT / 'acceptance' / 'phase_b'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'baseline_verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    return result


def _v1_window(strategy_id, snapshot_id, start, end):
    from core.research.reference.catalog import capabilities
    from scripts import history_engine
    cap = {c.strategy_id: c for c in capabilities()}[strategy_id]
    return history_engine.execute_window(cap, 'CHARACTERIZATION', snapshot_id, start, end)


def _v2_window(strategy_id, snapshot_id, start, end):
    from scripts.platform_v2_engine import execute
    base = defaults()
    result = execute(strategy_id, snapshot_id, start, end, base['capital'], base['costs'], base['rule'])
    return {'tracks': result['tracks'], 'feature_cache': result['feature_cache']}


def characterize(strategy_id, start, end) -> dict:
    """B.2: the V1 engine path and V2 (default rule) in two separate processes on the same short window; equal NAV and trades."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    from core.research.platform.ledger_v2 import reconcile
    from scripts import historical_foundation
    snapshot_id = historical_foundation.latest_evaluation()['snapshot_id']
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as pool:
        v1 = pool.submit(_v1_window, strategy_id, snapshot_id, start, end)
        v2 = pool.submit(_v2_window, strategy_id, snapshot_id, start, end)
        v1, v2 = v1.result(), v2.result()
    tracks = v2['tracks']
    mine = {'nav': pd.DataFrame({'date': [d for d, _ in tracks['base']['nav']], 'nav_base': [v for _, v in tracks['base']['nav']],
                                 'nav_stress': [v for _, v in tracks['stress']['nav']]}),
            'trades': pd.DataFrame(tracks['base']['trades']), 'trades_stress': pd.DataFrame(tracks['stress']['trades'])}
    problems = compare(v1, mine)
    capital = defaults()['capital']
    checks = {name: reconcile(capital, t['nav'], t['fills'], t['corporate_action_events'], t['positions_daily'], t['open_positions'])
              for name, t in tracks.items()}
    return {'strategy_id': strategy_id, 'window': [start, end], 'snapshot_id': snapshot_id, 'sessions': len(mine['nav']),
            'trades': len(mine['trades']), 'fills': len(tracks['base']['fills']), 'status': 'PASS' if not problems else 'FAIL',
            'differences': problems, 'reconciliation': checks, 'feature_cache': v2['feature_cache'],
            'checked_at': datetime.now(timezone.utc).isoformat()}


ANALYSIS = OUT / 'analysis'
ANALYSIS_FILES = ('core/research/platform/analysis.py', 'scripts/strategy_intelligence.py', 'core/research/intelligence/performance.py',
                  'core/research/intelligence/scenarios.py', 'core/research/intelligence/evaluate.py', 'core/research/intelligence/feasibility.py',
                  'scripts/historical_report.py')


def analysis_code() -> str:
    from core.backtest.run_store import canonical_hash
    from scripts.research_workflow import sha256
    return canonical_hash({name: sha256(ROOT / name) for name in ANALYSIS_FILES})[:12]


def analysis_path(run_id) -> Path:
    return ANALYSIS / f'{run_id}__{analysis_code()}.json'


def _positions_rebuilt(positions, nav, publishable) -> dict:
    """V2 daily positions in feasibility.rebuild's shape: count, max weight, HHI and cash share per session."""
    frame = pd.DataFrame(positions)
    navs = dict((d, v) for d, v in nav)
    daily = []
    for day, group in frame.groupby('date', sort=True):
        held = group[group.stock_id != 'CASH']
        total = navs.get(day)
        weights = (held.value / total) if total else pd.Series(dtype=float)
        cash = group[group.stock_id == 'CASH'].value
        daily.append({'date': day, 'count': len(held), 'max_weight': float(weights.max()) if len(held) else 0.0,
                      'hhi': float((weights ** 2).sum()) if len(held) else None,
                      'cash_share': float(cash.iloc[0] / total) if total and len(cash) else None, 'holdings': held.stock_id.tolist()})
    return {'status': 'RECONCILED' if publishable else 'POSITIONS_UNVERIFIABLE', 'daily': daily}


def analyze(run_id, ctx=None, root=None) -> Path:
    """B.9: V1 performance, scenarios, health and dimensions on a V2 run, a monthly table and feasibility from V2 daily positions."""
    from core.backtest import run_store
    from core.research.intelligence import evaluate
    from core.research.intelligence import feasibility as fz
    from core.research.platform.analysis import monthly_table
    from core.research.platform.evidence import validate_evidence_fields
    from scripts import strategy_intelligence as si
    loaded = run_store.load_run(run_id, root=Path(root or RUNS))
    record = loaded['run']
    path = analysis_path(run_id)
    if path.exists():
        return path
    ctx = ctx or si.load_context()
    sid, params = record['strategy_id'], record['parameters']
    nav = run_store.read_frame(loaded, 'nav')
    trades = run_store.read_frame(loaded, 'trades')
    trades = trades.astype({'stock_id': str}) if len(trades) else pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'profit_pct'])
    actions = run_store.read_frame(loaded, 'corporate_actions')
    costs = run_store.read_frame(loaded, 'costs')
    run = {'run_id': run_id, 'nav': nav, 'trades': trades, 'actions': actions if len(actions) else None,
           'manifest_sha256': run_store.file_hash(loaded['dir'] / 'manifest.json'),
           'record': record | {'cost_model': {**params['costs'], 'stress_multiple': 2}, 'cost_summary': costs.get('base') or {},
                               'open_positions': run_store.read_frame(loaded, 'open_positions')}}
    current = identity(params, record['snapshot_id']) == record['run_identity']
    stale = 'CURRENT' if current else 'IDENTITY_STALE'
    status, adjustments = si.trust_for(ctx, sid, run, stale)
    out = {'run_id': run_id, 'strategy_id': sid, 'analysis_code': analysis_code(), 'identity_status': stale, 'publishable': record['publishable'],
           'not_publishable_reasons': record['not_publishable_reasons'], 'year_status': {str(k): v for k, v in status.items()},
           'trust_adjustments': adjustments, 'labels': LABELS}
    if record['publishable']:
        prof = ctx['profile']
        performance = si._performance(ctx, run, status)
        scenario = si._scenarios(ctx, run, status, not current)
        ledger = run_store.read_frame(loaded, 'daily_ledger')
        rebuilt = _positions_rebuilt(run_store.read_frame(loaded, 'positions_daily').to_dict('records'), nav[['date', 'nav_base']].values.tolist(), True)
        liq = fz.liquidity(trades, ctx['bars'])
        floor = prof['sample_rules']['min_closed_trades_for_trade_statistics']
        feasible = {'positions': fz.position_summary(rebuilt), 'liquidity': liq, 'capacity': fz.capacity(liq, params['capital'], True, min_trades=floor),
                    'source': 'V2 daily positions (reconciled fill ledger), no rebuild'}
        ptype = prof['strategies'][sid]['primary_type']
        ectx = {'verified': performance['verified'], 'provisional': performance['provisional'], 'trades': performance['trades'],
                'liquidity': liq, 'positions': feasible['positions'], 'capacity': feasible['capacity'], 'scenarios_sample': scenario['check'],
                'year_status': out['year_status'], 'verified_span': performance['verified_span'], 'identity_status': stale, 'primary_type': ptype}
        out |= {'performance': performance, 'scenarios': scenario, 'feasibility': feasible, 'monthly': monthly_table(nav, ledger),
                'dimensions': evaluate.dimensions(ectx, prof), 'health': evaluate.health(ectx, prof), 'primary_type': ptype}
    evidence = record['evidence'] | {'source_timestamp': datetime.now(timezone.utc).isoformat(), 'identity': f'{run_id}__{out["analysis_code"]}'}
    validate_evidence_fields(evidence, 'backtest')
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump({'evidence': evidence, 'result': out}, handle, ensure_ascii=False, indent=1, default=str)  # NaN allowed, as V1 write_json
    return path


PORTFOLIOS = OUT / 'portfolios'
PORTFOLIO_FILES = ENGINE_FILES + ('core/research/platform/portfolio_v2.py',)


def baseline_run(strategy_id, rows=None):
    """The member's stored V2 baseline run (default rule, V1 capital, costs and window), or None."""
    rows = rows if rows is not None else readiness()
    row = rows.get(strategy_id)
    if not row or not row['backtest_window']:
        return None
    from scripts import historical_foundation
    base = defaults()
    params = {'strategy_id': strategy_id, 'start': row['backtest_window'][0], 'end': row['backtest_window'][1], 'capital': base['capital'],
              'costs': base['costs'], 'rule': base['rule']}
    return lookup(identity(params, historical_foundation.latest_evaluation()['snapshot_id']))


def check_portfolio(members, weights, start, end, capital, costs, rule, rebalance, rows=None) -> dict:
    """Members need a VERIFIED V1 track and a publishable V2 baseline run; window inside every member's window."""
    from core.backtest import run_store
    from core.research.platform.portfolio_v2 import REBALANCES, check_weights
    rows = rows if rows is not None else readiness()
    weights = check_weights(list(members), weights)
    if rebalance not in REBALANCES:
        raise ValueError(f'UNKNOWN_REBALANCE: {rebalance}')
    windows = []
    for sid in members:
        row = rows.get(sid)
        if not row or not row['trusted_years']:
            raise ValueError(f'MEMBER_NOT_VERIFIED: {sid}')
        run_id = baseline_run(sid, rows)
        if not run_id or not run_store.load_run(run_id, root=RUNS)['run'].get('publishable'):
            raise ValueError(f'MEMBER_NOT_PUBLISHABLE: {sid}')
        windows.append(row['backtest_window'])
    window = [max(w[0] for w in windows), min(min(w[1] for w in windows), CUTOFF)]
    single = check_params(members[0], start, end, capital, costs, rule, {members[0]: {**rows[members[0]], 'backtest_window': window}})
    return single | {'members': list(members), 'weights': weights, 'rebalance': rebalance, 'strategy_id': None}


def portfolio_identity(params, snapshot_id) -> str:
    from core.backtest.run_store import canonical_hash
    from core.research.reference.catalog import capabilities
    from scripts import historical_foundation
    from scripts.research_workflow import sha256
    caps = {c.strategy_id: c for c in capabilities()}
    v1 = {sid: historical_foundation.history_identity(caps[sid], snapshot_id, params['start'], params['end']) for sid in params['members']}
    code = {name: sha256(ROOT / name) for name in PORTFOLIO_FILES}
    return 'PID2-' + canonical_hash({'v1': v1, 'code': code, **{k: params[k] for k in ('members', 'weights', 'rule', 'rebalance', 'capital', 'costs')}})[:24]


def portfolio_execute(params, snapshot_id) -> dict:
    """Both cost tracks of one shared-capital portfolio: the V2 sleeve engines built as legacy._run_track builds its engine."""
    import contextlib
    import os
    import random
    from unittest.mock import patch
    import core.backtest.runner as engine_runner
    from core.backtest.costs import CostModel
    from core.research.platform import feature_cache
    from core.research.platform.engine_v2 import rule_engine
    from core.research.platform.portfolio_v2 import Book, SleeveView, run_days, sleeve_engine
    from core.research.reference.catalog import capabilities
    from scripts import history_engine, reference_engine
    from scripts import legacy_baseline_run as legacy
    from scripts import native7_rules_only as n7
    from scripts.native_recovery import isolated
    from scripts.platform_v2_engine import cache_parts
    caps = {c.strategy_id: c for c in capabilities()}
    prep = history_engine._prepared(snapshot_id, params['start'], params['end'])
    key = feature_cache.cache_key(cache_parts(prep, snapshot_id))
    if getattr(prep.data, 'feature_cache_original', None) is None:
        feature_cache.attach(prep.data, key)
    bars = history_engine._tquant_bars(snapshot_id)
    select, memo = engine_runner.select_backtest_candidates, {}

    def shared_select(strategy, platform, name, frame, asof_date, runtime_overrides=None):
        if (name, str(asof_date)) not in memo:
            memo[(name, str(asof_date))] = select(strategy, platform, name, frame, asof_date, runtime_overrides)
        return memo[(name, str(asof_date))].copy()

    tracks = {}
    reference_engine.snapshot_history.cache_clear()
    try:
        for track, multiple in (('base', 1), ('stress', legacy.STRESS_MULTIPLE)):
            costs = {k: v * multiple for k, v in params['costs'].items()}
            book, cls = Book(params['capital']), sleeve_engine(rule_engine(params['rule']))
            with contextlib.ExitStack() as stack:
                stack.enter_context(isolated(prep.data, prep.settings))
                stack.enter_context(contextlib.redirect_stdout(stack.enter_context(open(os.devnull, 'w', encoding='utf-8'))))
                stack.enter_context(patch.object(engine_runner, 'select_backtest_candidates', shared_select))
                stack.enter_context(patch.object(engine_runner.Config, 'SLIPPAGE_MAX_PIPS', costs['slippage_max']))
                stack.enter_context(patch.object(reference_engine, '_bars', lambda: bars))
                if any(caps[sid].engine == 'TQUANT_SNAPSHOT' for sid in params['members']):
                    from core.strategies import _tquant
                    stack.enter_context(patch.object(_tquant, 'load_history', reference_engine.snapshot_history))
                sleeves = []
                for sid, weight in zip(params['members'], params['weights']):
                    engine = cls(mode=sid, start_date=prep.start, end_date=prep.end, initial_capital=params['capital'] * weight, persist_results=False,
                                 persist_to_db=False, execution_contract=legacy.EXECUTION_CONTRACT, trading_sessions=prep.sessions,
                                 research_data=prep.data, rules_only=True, valuation_policy='finite_positive_stale',
                                 corporate_actions=getattr(prep, 'ledger', None))
                    engine.portfolio = SleeveView(book, sid)
                    engine.costs = legacy._CostLog(CostModel(costs['fee_rate'], costs['tax_rate'], costs['minimum_fee']))
                    legacy._record_trades(engine, engine.costs)
                    sleeves.append(engine)
                random.seed(n7.SLIPPAGE_SEED)
                out = run_days(sleeves, params['weights'], book, prep.sessions, params['rebalance'])
            tracks[track] = out | {
                'fills': [f | {'sleeve': e.portfolio.name} for e in sleeves for f in e.fill_rows()],
                'trades': [t | {'sleeve': e.portfolio.name} for e in sleeves for t in e.trades],
                'corporate_action_events': [a for e in sleeves for a in e.corporate_action_events],
                'open_positions': [{'stock_id': sid, 'shares': info['shares'], 'sleeve': book.owner[sid]} for sid, info in book.positions.items()],
                'rejections': list(book.rejections), 'fees_and_tax_paid': sum(e.costs.paid for e in sleeves),
                'valuation_events': [v for e in sleeves for v in e.valuation_events]}
    finally:
        reference_engine.snapshot_history.cache_clear()
    return {'tracks': tracks, 'feature_cache': {'key': key}}


def _series(nav):
    return pd.Series([v for _, v in nav], index=pd.to_datetime([d for d, _ in nav]), dtype='float64')


def portfolio_analysis(params, result, snapshot_id) -> dict:
    """E.2: performance vs total-return benchmark and each member's own V2 run, correlation, overlap, common losses, contention, costs."""
    from collections import Counter
    from core.backtest import run_store
    from core.research import trade_analytics
    from scripts import historical_foundation
    base = result['tracks']['base']
    nav = _series(base['nav'])
    perf = lambda s, cash: {k: v for k, v in trade_analytics.performance(s, initial_cash=cash).items() if k in ('total_return', 'cagr', 'sharpe', 'max_drawdown')}
    bench = historical_foundation.snapshot(snapshot_id).frame('benchmark').assign(date=lambda f: pd.to_datetime(f.date)).set_index('date')
    tri = bench.taiex_total_return.reindex(nav.index)
    members, member_rows = {}, {}
    for sid in params['members']:
        loaded = run_store.load_run(baseline_run(sid), root=RUNS)
        member_nav = run_store.read_frame(loaded, 'nav').assign(date=lambda f: pd.to_datetime(f.date)).set_index('date').nav_base
        member_nav = member_nav[(member_nav.index >= nav.index[0]) & (member_nav.index <= nav.index[-1])]
        members[sid] = member_nav
        member_rows[sid] = {'run_id': loaded['run']['run_id'], **perf(member_nav, member_nav.iloc[0]),
                            'note': 'own V2 baseline run (own capital) sliced to the portfolio window: not rebased'}
        positions = run_store.read_frame(loaded, 'positions_daily')
        member_rows[sid]['_held'] = positions[positions.stock_id != 'CASH'].assign(date=lambda f: pd.to_datetime(f.date))
    returns = pd.DataFrame({sid: s.pct_change() for sid, s in members.items()}).dropna(how='all')
    held = pd.concat([r.pop('_held').assign(member=sid) for sid, r in member_rows.items()])
    held = held[(held.date >= nav.index[0]) & (held.date <= nav.index[-1])]
    shared = held.groupby(['date', 'stock_id']).member.nunique()
    overlap_days = int((shared > 1).groupby(level='date').any().sum()) if len(shared) else 0
    big = (returns <= -0.02).sum(axis=1)
    traded = sum(f['notional'] for f in base['fills'])
    years = len(nav) / 252
    return {'portfolio': {'base': perf(nav, params['capital']), 'stress': perf(_series(result['tracks']['stress']['nav']), params['capital'])},
            'benchmark_total_return': {'total_return': float(tri.iloc[-1] / tri.iloc[0] - 1) if tri.notna().all() else None,
                                       'reason': None if tri.notna().all() else 'BENCHMARK_GAPS'},
            'members': member_rows, 'correlation': returns.corr().round(4).to_dict() if len(params['members']) > 1 else None,
            'overlap': {'standalone_overlap_days': overlap_days, 'sessions': len(nav),
                        'note': 'days on which two members, run alone, held the same stock; inside the portfolio CONTEND_V1 skips the second'},
            'common_large_losses': {'rule': 'sessions where two or more members lost 2% or more (members run alone)', 'count': int((big >= 2).sum()),
                                    'dates': [d.strftime('%Y-%m-%d') for d in big[big >= 2].index[:20]]},
            'contention': dict(Counter(r['reason'] for r in base['rejections'])),
            'costs': {'fees_and_tax_paid': base['fees_and_tax_paid'], 'traded_value': traded,
                      'turnover_annual': traded / nav.mean() / years if years else None},
            'industry_concentration': {'value': None, 'reason': 'NOT_AVAILABLE: the snapshot has no industry classification'}}


def portfolio_run(params, snapshot_id, root=None) -> str:
    """Reuse an identical portfolio run, else execute, reconcile, analyse and store under outputs/platform_v2/portfolios."""
    from core.backtest import run_store
    from core.backtest.run_store import canonical_hash
    from core.research.platform.engine_v2 import ENGINE_VERSION
    from core.research.platform.evidence import validate_evidence_fields
    from core.research.platform.ledger_v2 import daily_ledger, reconcile
    root = Path(root or PORTFOLIOS)
    pid = portfolio_identity(params, snapshot_id)
    if existing := lookup(pid, root):
        return existing
    run_id = run_store.new_run_id()
    result = portfolio_execute(params, snapshot_id)
    checks = {name: reconcile(params['capital'], t['nav'], t['fills'], t['corporate_action_events'], t['positions_daily'], t['open_positions'])
              for name, t in result['tracks'].items()}
    reasons = [f"{n}: {c['reason']} ({c['first_mismatch']})" for n, c in checks.items() if c['status'] != 'PUBLISHABLE']
    base, stress = result['tracks']['base'], result['tracks']['stress']
    evidence = {'data_class': 'REAL_RESEARCH', 'as_of_date': params['end'], 'source_timestamp': datetime.now(timezone.utc).isoformat(),
                'data_trust': 'PARTIAL', 'identity': pid, 'labels': LABELS, 'members': params['members'], 'run_id': run_id,
                'snapshot_id': snapshot_id, 'engine_version': ENGINE_VERSION, 'rule_version': params['rule']}
    validate_evidence_fields(evidence, 'portfolio')
    record = {'strategy_id': 'PORTFOLIO:' + '+'.join(params['members']), 'strategy_ids': params['members'], 'strategy_type': 'platform_v2_portfolio',
              'parameters': params, 'parameter_hash': canonical_hash(params), 'run_identity': pid, 'source_hash': pid, 'data_version': snapshot_id,
              'snapshot_id': snapshot_id, 'comparison_group': GROUP + 'PORTFOLIO', 'certification_status': 'NOT_CERTIFIED', 'status_class': 'EXPERIMENTAL',
              'labels': LABELS + ['RESEARCH_SCENARIO_NOT_A_STRATEGY'], 'period': [params['start'], params['end']], 'engine_version': ENGINE_VERSION,
              'rules': {'allocation': 'ALLOC_V1', 'contention': 'CONTEND_V1', 'rebalance': params['rebalance'], 'lot': params['rule']},
              'platform_evidence_version': 2, 'publishable': not reasons, 'not_publishable_reasons': reasons, 'reconciliation': checks,
              'evidence': evidence, 'analysis': portfolio_analysis(params, result, snapshot_id) if not reasons else None}
    nav = pd.DataFrame({'date': [d for d, _ in base['nav']], 'nav_base': [v for _, v in base['nav']], 'nav_stress': [v for _, v in stress['nav']]})
    frame = lambda rows, cols: pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)
    frames = {'nav': nav, 'trades': frame(base['trades'], ['stock_id', 'buy_date', 'sell_date', 'sleeve']),
              'trades_stress': frame(stress['trades'], ['stock_id', 'buy_date', 'sell_date', 'sleeve']),
              'holdings': frame([r for r in base['positions_daily'] if r['stock_id'] != 'CASH'], ['date', 'stock_id', 'shares', 'value', 'sleeve']),
              'picks': pd.DataFrame(columns=['date', 'stock_id']), 'metrics': _metrics(nav, params['capital']),
              'positions_daily': frame(base['positions_daily'], ['date', 'stock_id', 'shares', 'value', 'sleeve']),
              'fills': frame(base['fills'], ['fill_id']), 'fills_stress': frame(stress['fills'], ['fill_id']),
              'daily_ledger': pd.DataFrame(daily_ledger(base['nav'], base['positions_daily'])),
              'rejections': frame(base['rejections'], ['date', 'sleeve', 'stock_id', 'reason']),
              'corporate_actions': frame(base['corporate_action_events'], ['date', 'stock_id', 'event_id', 'kind', 'status']),
              'open_positions': base['open_positions'], 'valuation_events': {'base': base['valuation_events'], 'stress': stress['valuation_events']}}
    return run_store.save_run(record, frames, root=root, run_id=run_id)


def governance(write=True) -> dict:
    """9.2: ledgers, effective_trials(), H4, both identity file sets, V1 runs and V1 results against baseline.json; no research output after
    2025-12-31. Differences mark the platform results as not accepted."""
    from scripts import data_readiness_checkpoint as drc
    from scripts import research_workflow as rw
    from scripts import strategy_intelligence as si
    from scripts.research_workflow import sha256
    base = json.loads((OUT / 'baseline.json').read_text(encoding='utf-8'))
    diffs = drc.compare_baseline(OUT)
    tree = lambda root: {p.relative_to(ROOT).as_posix(): sha256(p) for p in sorted(Path(root).rglob('*')) if p.is_file()}
    current = {'historical_identity_files': si.identity_files(), 'intelligence_identity_files': si.code_hashes(),
               'historical_run_files': tree(ROOT / 'outputs/historical_foundation/runs'),
               'intelligence_result_files': tree(ROOT / 'outputs/strategy_intelligence/results')}
    for name, now in current.items():
        diffs += [f'{name} changed or missing: {key}' for key, digest in base[name].items() if now.get(key) != digest]
    late = []
    for root in (RUNS, PORTFOLIOS):
        for folder in sorted(root.glob('2*')) if root.is_dir() else []:
            record = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
            if str((record.get('period') or [None, ''])[1])[:10] > CUTOFF:
                late.append(f'research output after {CUTOFF}: {folder.name}')
    for path in MODELS.glob('*/model.json') if MODELS.is_dir() else []:
        folds = json.loads(path.read_text(encoding='utf-8'))['training']['folds']
        late += [f'model test year after {CUTOFF}: {path.parent.name}' for f in folds if f.get('year', 0) > int(CUTOFF[:4])]
    diffs += late
    h4 = sum(len(drc._tree_hashes(ROOT / 'outputs/research_mode' / t)) for t in base['h4_sha256'])
    result = {'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'status': 'MATCH' if not diffs else 'DIFFERENT',
              'differences': diffs, 'baseline_created_at': base['created_at'],
              'current': {'effective_trials': rw.effective_trials(), 'trial_ledger_verify': rw.verify_trials(), 'registry_verify': rw.verify_registry(),
                          'h4_files': h4},
              'checked': {name: len(base[name]) for name in current} | {'ledger_files': len(base['ledger_files'])}}
    if write:
        folder = OUT / 'governance'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = result['checked_at'].replace(':', '').replace('-', '')[:15]
        for target in (folder / f'governance_{stamp}.json', folder / 'LATEST.json'):
            target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result


MODELS = OUT / 'models'
MODEL_FREEZE = OUT / 'model_profile_freeze.json'


def model_version(horizon, frozen, snapshot_id, code):
    from core.backtest.run_store import canonical_hash
    identity = canonical_hash({'horizon': horizon, 'spec_hash': frozen, 'snapshot_id': snapshot_id, 'code': code})
    return f'PRED1-{horizon}d-{identity[:10]}'


def _event_mask(snap, close):
    """sessions x stocks: corporate-action dates (TWT49U/TWTAUU/TWTB8U) plus the V1 event marks and unexplained jumps."""
    from scripts import historical_report as hr
    mask = pd.DataFrame(False, index=close.index, columns=close.columns)
    marks, jumps = hr.event_marks(snap)
    days = [(str(r.stock_id), r.ex_date) for r in snap.frame('events').itertuples()]
    days += [(str(r.stock_id), r.date) for r in snap.frame('capital_changes').itertuples()]
    days += [(sid, d) for source in (marks, jumps) for sid, index in source.items() for d in index]
    for sid, day in days:
        day = pd.Timestamp(day)
        if sid in mask.columns and day in mask.index:
            mask.at[day, sid] = True
    return mask


def models() -> dict:
    """D.5: walk-forward evaluation of the frozen spec on snapshot bars (<= 2025-12-31) and the model registry.
    Per-stock predictions are stored only for a DEV_WINDOW_SKILL horizon."""
    from core.backtest.run_store import canonical_hash
    from core.research.platform import prediction as pr
    from scripts import historical_foundation
    from scripts.research_workflow import sha256
    frozen = pr.check_frozen(MODEL_FREEZE)  # before reading any data
    evaluation = historical_foundation.latest_evaluation()
    snap = historical_foundation.snapshot(evaluation['snapshot_id'])
    bars = snap.frame('bars')
    if bars.trade_date.max() > pd.Timestamp(CUTOFF):
        raise ValueError('OD-2: snapshot bars after 2025-12-31')
    calendar = snap.frame('calendar')
    sessions = pd.DatetimeIndex(calendar.loc[calendar.kind == 'TRADING', 'date']).sort_values()
    close, volume = pr.wide(bars, sessions)
    feats, events = pr.features(close, volume), _event_mask(snap, close)
    code = {name: sha256(ROOT / name) for name in ('core/research/platform/prediction.py', 'scripts/platform_v2.py')}
    versions = {h: model_version(h, frozen, snap.snapshot_id, code) for h in pr.SPEC['labels']['horizons']}
    if any((MODELS / version).exists() for version in versions.values()):
        raise FileExistsError('MODEL_IDENTITY_ALREADY_EXISTS: existing research results are preserved')
    summary = {}
    for horizon in pr.SPEC['labels']['horizons']:
        labels = pr.labels(close, horizon, events)
        data = feats.join(labels, how='inner')
        pred, folds = pr.walk_forward(data, horizon, pr.SPEC['split']['test_years'], pr.SPEC['split']['purge_sessions'], sessions=sessions)
        result = pr.evaluate(pred, horizon)
        version = versions[horizon]
        evaluation_id = 'MEV1-' + canonical_hash({'version': version, 'snapshot': snap.snapshot_id, 'result': result})[:16]
        folder = MODELS / version
        folder.mkdir(parents=True, exist_ok=False)
        evidence = {'data_class': 'REAL_RESEARCH', 'as_of_date': CUTOFF, 'source_timestamp': datetime.now(timezone.utc).isoformat(),
                    'data_trust': 'PARTIAL', 'identity': evaluation_id, 'labels': LABELS + ['DEV_WINDOW_WALK_FORWARD_NOT_INDEPENDENT_OOS'],
                    'model_version': version, 'evaluation_id': evaluation_id, 'snapshot_id': snap.snapshot_id}
        from core.research.platform.evidence import validate_evidence_fields
        validate_evidence_fields(evidence, 'prediction')
        record = {'model_version': version, 'spec_hash': frozen, 'spec': pr.SPEC, 'horizon': horizon, 'snapshot_id': snap.snapshot_id,
                  'training': {'scheme': pr.SPEC['split'], 'folds': folds}, 'label_rows': int(len(labels)),
                  'excluded_event_windows': labels.attrs.get('excluded_events'), 'evaluation': result, 'status': result['status'],
                  'evidence': evidence, 'today_prediction': 'GOVERNANCE_BLOCKED (OD-2: no 2026 data in research)'}
        (folder / 'model.json').write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        if result['status'] == 'DEV_WINDOW_SKILL':
            pred.reset_index().to_parquet(folder / 'predictions.parquet', index=False)
        summary[version] = {'status': result['status'], 'reasons': result['reasons'], 'evaluation_id': evaluation_id}
        print(json.dumps({version: summary[version]}, default=str), flush=True)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('characterize')
    c.add_argument('strategy')
    c.add_argument('start')
    c.add_argument('end')
    c.add_argument('--out', required=True)
    b = sub.add_parser('baseline')
    b.add_argument('--workers', type=int, default=4)
    r = sub.add_parser('run')
    r.add_argument('--params', required=True, help='JSON file with strategy_id, start, end, capital, costs, rule')
    r.add_argument('--workers', type=int, default=1)  # the job slot always passes it
    f = sub.add_parser('portfolio')
    f.add_argument('--params', required=True, help='JSON file with members, weights, start, end, capital, costs, rule, rebalance')
    f.add_argument('--workers', type=int, default=1)
    sub.add_parser('freeze-model')
    sub.add_parser('models')
    sub.add_parser('verify')
    sub.add_parser('governance')
    a = sub.add_parser('analyze')
    a.add_argument('run_ids', nargs='*')
    args = parser.parse_args(argv)
    if args.command in ('verify', 'governance'):
        result = verify_baseline() if args.command == 'verify' else governance()
        print(json.dumps({'command': args.command, 'status': result['status']}), flush=True)
        return 0 if result['status'] in ('PASS', 'MATCH') else 1
    if args.command == 'analyze':
        from core.backtest import run_store
        from scripts import strategy_intelligence as si
        ids = args.run_ids or [r['run_id'] for r in run_store.history(root=RUNS) if r.get('status') == 'SUCCESS']
        ctx = si.load_context()
        for run_id in ids:
            print(json.dumps({'analyzed': run_id, 'path': str(analyze(run_id, ctx))}), flush=True)
        return 0
    if args.command == 'freeze-model':
        from core.research.platform import prediction as pr
        print(json.dumps({k: v for k, v in pr.freeze(MODEL_FREEZE).items() if k != 'spec'}), flush=True)
        return 0
    if args.command == 'models':
        summary = models()
        return 0 if summary else 1
    if args.command == 'characterize':
        report = characterize(args.strategy, args.start, args.end)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        print(json.dumps({k: report[k] for k in ('strategy_id', 'status', 'differences', 'sessions', 'trades', 'fills')}), flush=True)
        return 0 if report['status'] == 'PASS' else 1
    if args.command == 'baseline':
        rows = baseline(args.workers)
        failed = [r for r in rows if r['state'] != 'DONE']
        partial = failed and len(failed) < len(rows)
        print(json.dumps({'command': 'baseline', 'status': 'PARTIAL' if partial else 'FAILED' if failed else 'DONE', 'failed': failed}), flush=True)
        return 2 if partial else (1 if failed else 0)
    from scripts import historical_foundation
    raw = json.loads(Path(args.params).read_text(encoding='utf-8'))
    snapshot_id = historical_foundation.latest_evaluation()['snapshot_id']
    if args.command == 'portfolio':
        params = check_portfolio(raw.get('members') or [], raw.get('weights'), raw.get('start'), raw.get('end'), raw.get('capital'),
                                 raw.get('costs') or {}, raw.get('rule'), raw.get('rebalance', 'REBAL_NONE'))
        run_id = portfolio_run(params, snapshot_id)
        print(json.dumps({'command': 'portfolio', 'status': 'DONE', 'run_id': run_id}), flush=True)
        return 0
    params = check_params(raw.get('strategy_id'), raw.get('start'), raw.get('end'), raw.get('capital'), raw.get('costs') or {}, raw.get('rule'))
    run_id = run_one(params, snapshot_id)
    analyze(run_id)  # the report page reads only stored results
    print(json.dumps({'command': 'run', 'status': 'DONE', 'run_id': run_id}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
