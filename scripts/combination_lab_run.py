"""Round 2026-11-06-msv orchestrator: qualify -> freeze queue -> register -> run the queue under the 23-trial cap -> finalize.

  python scripts/combination_lab_run.py --plan       qualification, combinations, frozen queue, registrations (no returns read)
  python scripts/combination_lab_run.py --run        execute the frozen queue in order (resumable: a finished item is skipped,
                                                     a re-admitted identical configuration is a free REPLAY)
  python scripts/combination_lab_run.py --finalize   avg_score, heatmap, Top 3 deep analysis, legacy summary, record_result
Research mode only; every date is inside 2023-01-03..2025-12-31.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import traceback

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.backtest import run_store
from core.research import benchmark_tri, combination_lab as lab, condition_pool as cp, strategy_signal as ss, trade_analytics as ta
from core.runtime import operation_policy as policy
from scripts import legacy_baseline_run as legacy
from scripts import research_workflow as workflow
from scripts import track_b_stats as stats
from scripts.research_mode_report import asset_pnl

OUT = lab.OUTPUT
ITEMS = OUT / 'items'
TRIAL_VARIANCE = 0.00270516167404164  # trial Sharpe variance of the H4-V2 screen, reused for the comparison-only DSR (design D1)
TQ = ('volatility_adjusted_momentum', 'mad_distance')


def _safe(item_id):
    return re.sub(r'[^A-Za-z0-9_.+-]', '_', item_id)


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str, allow_nan=False), encoding='utf-8')


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def require_research():
    if policy.current_mode() is not policy.ExecutionMode.RESEARCH:
        raise PermissionError('RESEARCH_MODE_REQUIRED')
    ss.check_window(lab.START, lab.END)


# ---------------------------------------------------------------- benchmark

def benchmark_levels():
    """Official total-return index levels (READY provider) on the research calendar; None with the reason otherwise."""
    calendar = benchmark_tri.research_calendar()
    try:
        return benchmark_tri.research_series(calendar), 'TWSE total-return index (MI_INDEX local files; MFI94U-equivalent)'
    except benchmark_tri.BenchmarkBlocked as exc:
        return None, f'TOTAL_RETURN_INDEX_UNAVAILABLE: {exc}'


def comparison_gates(net_2x, pnl_by_asset, levels):
    """OD-24 g1..g5 mathematics on the 2x-cost track, COMPARISON ONLY (data is not certified)."""
    if levels is None:
        return {'status': 'NOT_COMPUTABLE', 'reason': 'BENCHMARK_UNAVAILABLE'}
    bench = levels.pct_change()
    joined = pd.concat([net_2x.rename('net'), bench.rename('bench')], axis=1).dropna()
    if len(joined) < 40:
        return {'status': 'NOT_COMPUTABLE', 'reason': 'INSUFFICIENT_RETURN_SERIES'}
    excess = joined['net'] - joined['bench']
    n_trials = workflow.effective_trials()
    values = {'net_sharpe_2x': stats.sharpe(joined['net']), 'excess_sharpe_2x': stats.sharpe(excess),
              'bootstrap_lower': stats.block_bootstrap_sharpe(excess)['lower'],
              'deflated_sharpe': stats.deflated_sharpe(excess, n_trials, TRIAL_VARIANCE),
              'top5_assets_profit_share': stats.top_assets_share(pnl_by_asset), 'best5pct_days_return_share': stats.best_days_share(joined['net']),
              'regime': stats.regime_excess(excess, stats.regimes(levels))}
    regime = values['regime']
    gates = {'g1': values['net_sharpe_2x'] > 0 and values['excess_sharpe_2x'] > 0, 'g2': values['bootstrap_lower'] > 0,
             'g3': values['deflated_sharpe'] > .95,
             'g4': values['top5_assets_profit_share'] is not None and values['top5_assets_profit_share'] < .5 and values['best5pct_days_return_share'] < .5,
             'g5': all(regime[n]['sessions'] >= 60 for n in ('bull', 'bear')) and [regime[n]['status'] for n in ('bull', 'bear')] != ['NEGATIVE', 'NEGATIVE']}
    return _clean({'status': 'COMPARISON_ONLY_DATA_UNCERTIFIED', 'gates': gates, 'values': values, 'dsr_trials': n_trials})


# ---------------------------------------------------------------- items

def _nav_frame(base, stress):
    return pd.DataFrame({'date': [d.strftime('%Y-%m-%d') for d in base.index], 'nav_base': base.values, 'nav_stress_2x': stress.reindex(base.index).values})


def run_research_engine_item(item, levels, registry):
    """Lab single / weighted / intersection, or a ported TQuant factor, on the research engine; returns evidence."""
    if item['kind'] == 'tq_baseline':
        rows, statuses = tq_rows(item['strategy_id'])
        hypothesis, config = f"TQ-{item['strategy_id'].upper()}-V1", tq_config(item)
    else:
        rows, statuses = lab.score_rows(item, registry=registry)
        hypothesis, config = lab.HYPOTHESES[item['kind']], lab.run_config(item)
    admitted = lab.admit(hypothesis, config, lab.code_paths() + [Path(__file__)], lab.input_paths(), registry=registry)
    try:
        return _finish_research_item(item, rows, statuses, hypothesis, config, admitted, levels)
    except Exception as exc:
        return {'status': 'FAILED', 'reason': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc()[-3000:],
                'hypothesis_id': hypothesis, 'experiment_id': admitted['experiment_id'], 'admission': admitted['kind'], 'config': config}


def _finish_research_item(item, rows, statuses, hypothesis, config, admitted, levels):
    result, bars = lab.simulate(item, rows)
    if result['status'] != 'COMPUTABLE':
        return {'status': 'NOT_COMPUTABLE', 'reason': result.get('reason'), 'admission': admitted['kind'], 'experiment_id': admitted['experiment_id']}
    tracks = result['tracks']
    from scripts.research_mode_report import returns_of
    stress_ret, stress_nav = returns_of(tracks['stress'], lab.COSTS['initial_cash'])
    _, base_nav = returns_of(tracks['base'], lab.COSTS['initial_cash'])
    trades = lab.trades_from_fills(tracks['base']['result']['ledger'])
    traded = sum(r['notional'] for r in tracks['base']['result']['ledger'] if r['kind'] == 'FILL')
    perf = ta.performance(base_nav, initial_cash=lab.COSTS['initial_cash'], benchmark=levels, trades=trades, turnover_notional=traded)
    perf_2x = ta.performance(stress_nav, initial_cash=lab.COSTS['initial_cash'], benchmark=levels, trades=lab.trades_from_fills(tracks['stress']['result']['ledger']))
    targets = result['targets']
    return {'status': 'SUCCESS', 'hypothesis_id': hypothesis, 'experiment_id': admitted['experiment_id'], 'admission': admitted['kind'],
            'config': config, 'period_status': statuses, 'skipped_rebalance_dates': result.get('skipped_rebalance_dates'),
            'metrics': perf, 'metrics_2x': perf_2x, 'gates_comparison': comparison_gates(stress_ret, asset_pnl(tracks['stress']), levels),
            'forfeits': result.get('forfeits'), '_frames': {'nav': _nav_frame(base_nav, stress_nav), 'trades': trades,
            'holdings': pd.DataFrame([{'date': d['date'], 'stock_id': s, 'shares': n} for d in tracks['base']['result']['daily'][::20] for s, n in d['positions'].items()],
                                     columns=['date', 'stock_id', 'shares']),
            'picks': pd.DataFrame(targets).rename(columns={'asset_id': 'stock_id'}) if len(targets) else pd.DataFrame(columns=['stock_id'])}}


def tq_config(item):
    return {'round': lab.ROUND, 'item_id': item['item_id'], 'kind': 'tq_baseline', 'strategy': item['strategy_id'],
            'factor': TQ_FACTORS[item['strategy_id']], 'top_n': lab.TOP_N, 'rebalance_days': lab.REBALANCE, 'stock_weighting': 'equal',
            **lab.COSTS, 'start': lab.START, 'end': lab.END, 'reproduction': 'REPRODUCTION_DIFFERS: notebook factor on the lab engine and H4 universe'}


TQ_FACTORS = {'volatility_adjusted_momentum': '21-session return / 21-session daily-return std (TQ_VAM), top 10',
              'mad_distance': 'MA21 / MA200 above 1 (TQ_MAD moving-average distance), top 10'}


def tq_rows(strategy_id):
    frames = cp.inputs()['frames']
    close = frames['close']
    if strategy_id == 'volatility_adjusted_momentum':
        panel = (close / close.shift(21) - 1) / close.pct_change().rolling(21, min_periods=21).std()
    else:
        ratio = close.rolling(21, min_periods=21).mean() / close.rolling(200, min_periods=200).mean()
        panel = ratio.where(ratio > 1)
    digest = workflow.sha256(cp.PREPARED / 'bars.json')
    rows, statuses = [], {}
    for day in cp.inputs()['days']:
        stamp = pd.Timestamp(day)
        values = panel.loc[stamp].reindex(cp.inputs()['members'][day]).dropna() if stamp in panel.index else pd.Series(dtype=float)
        top = values.sort_values(ascending=False, kind='stable').head(lab.TOP_N)
        statuses[day] = 'NO_ELIGIBLE_STOCKS' if top.empty else ('INSUFFICIENT_DIVERSIFICATION' if len(top) < lab.MIN_NAMES else 'OK')
        rows += [{'asof_date': day, 'asset_id': str(a), 'composite_score': float(v), 'signal_at': f'{day}T15:00:00+08:00',
                  'score_available_at': f'{day}T13:30:00+08:00', 'score_source_sha256': digest} for a, v in top.items()]
    return rows, statuses


def run_legacy_item(item, levels, registry):
    sid, overrides = item['strategy_id'], item.get('overrides')
    hypothesis = f'LEGACY-{sid.upper()}-V1'
    config = legacy.config_for(sid, overrides)
    admitted = lab.admit(hypothesis, config, legacy.code_paths() + [Path(__file__)], legacy.input_paths(), registry=registry)
    try:
        return _finish_legacy_item(sid, overrides, hypothesis, config, admitted, levels)
    except Exception as exc:
        return {'status': 'FAILED', 'reason': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc()[-3000:],
                'hypothesis_id': hypothesis, 'experiment_id': admitted['experiment_id'], 'admission': admitted['kind'], 'config': config}


def _finish_legacy_item(sid, overrides, hypothesis, config, admitted, levels):
    raw = legacy.run_legacy(sid, overrides)
    out = {'status': 'SUCCESS', 'hypothesis_id': hypothesis, 'experiment_id': admitted['experiment_id'], 'admission': admitted['kind'],
           'config': config, 'checks': raw['checks'], 'data_version': raw.get('data_version')}
    navs, trades = {}, {}
    for name in ('base', 'stress'):
        track = raw['tracks'][name]
        if track.get('engine_error'):
            return {**out, 'status': 'FAILED', 'reason': track['engine_error']}
        navs[name] = pd.Series([v for _, v in track['nav']], index=pd.to_datetime([d for d, _ in track['nav']]))
        trades[name] = pd.DataFrame(track['trades'])
    if not len(trades['base']):
        trades['base'] = pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'buy_price', 'sell_price', 'shares', 'cost'])
    pnl = {}
    for row in trades['stress'].to_dict('records'):
        pnl[row['stock_id']] = pnl.get(row['stock_id'], 0.0) + (row['sell_price'] - row['buy_price']) * row['shares'] - row.get('cost', 0.0)
    stress_ret = navs['stress'] / navs['stress'].shift(1).fillna(legacy.INITIAL_CAPITAL) - 1
    out.update(metrics=ta.performance(navs['base'], initial_cash=legacy.INITIAL_CAPITAL, benchmark=levels, trades=trades['base']),
               metrics_2x=ta.performance(navs['stress'], initial_cash=legacy.INITIAL_CAPITAL, benchmark=levels, trades=trades['stress']),
               gates_comparison=comparison_gates(stress_ret, pnl, levels),
               _frames={'nav': _nav_frame(navs['base'], navs['stress']), 'trades': trades['base'],
                        'holdings': trades['base'][[c for c in ('stock_id', 'buy_date', 'sell_date') if c in trades['base'].columns]],
                        'picks': pd.DataFrame(raw['picks'], columns=['date', 'stock_id', 'rank'])})
    return out


def sensitivity_eligible(sid):
    """D3 execution conditions from the baseline diagnostics (no returns): >= 30 trades, no degenerate factor, no PIT violation."""
    path = ITEMS / (_safe(f'LEGACY:{sid}') + '.json')
    if not path.is_file():
        return False, 'BASELINE_NOT_COMPLETE'
    base = json.loads(path.read_text(encoding='utf-8'))
    checks = base.get('checks') or {}
    if base.get('status') != 'SUCCESS':
        return False, 'BASELINE_NOT_SUCCESS'
    if (checks.get('trade_count') or 0) < 30:
        return False, 'FEWER_THAN_30_TRADES'
    if checks.get('degenerate'):
        return False, 'DEGENERATE_FACTOR'
    if checks.get('pit_status') == 'PIT_VIOLATION':
        return False, 'PIT_VIOLATION'
    return True, ''


def save(item, evidence, levels_note):
    frames = evidence.pop('_frames', None)
    sid = item.get('strategy_id') or item['item_id']
    status_class = 'RESEARCH' if item['kind'] == 'legacy_baseline' else 'EXPERIMENTAL'
    group = ('LEGACY_ENGINE' if item['kind'].startswith('legacy') else 'LAB_ENGINE') + ':' + run_store.canonical_hash(
        {'engine': 'legacy_next_session_open_v2' if item['kind'].startswith('legacy') else 'research_tracks_prepared_v6',
         'period': [lab.START, lab.END], 'costs': lab.COSTS, 'benchmark': levels_note,
         'universe': 'native7 snapshot' if item['kind'].startswith('legacy') else 'h4_signals_v1/universe_20.json',
         'rebalance': 'daily rules' if item['kind'].startswith('legacy') else f'every {lab.REBALANCE} sessions TOP{lab.TOP_N}'})[:12]
    record = {'strategy_id': sid, 'strategy_ids': [sid], 'strategy_type': item['kind'], 'item_id': item['item_id'],
              'condition_ids': item.get('conditions') or sorted((item.get('weights') or {})), 'hypothesis_id': evidence['hypothesis_id'],
              'trial_id': evidence['experiment_id'], 'admission': evidence['admission'], 'parameters': evidence['config'],
              'parameter_hash': run_store.canonical_hash(evidence['config']),
              'source_hash': run_store.canonical_hash({p.as_posix(): workflow.sha256(p) for p in (lab.code_paths() if not item['kind'].startswith('legacy') else legacy.code_paths())}),
              'data_version': evidence.get('data_version') or workflow.sha256(cp.PREPARED / 'input_manifest.json'),
              'period': [lab.START, lab.END], 'cost_model': {**lab.COSTS, 'stress_multiple': 2}, 'benchmark': levels_note,
              'comparison_group': group, 'status_class': status_class, 'reference': item['kind'] == 'legacy_baseline',
              'certification_status': 'NOT_CERTIFIED', 'oos_status': 'OOS_NOT_AVAILABLE',
              'gate_result': 'DATA_BLOCKED' if item['kind'].startswith('legacy') else 'RESEARCH_CANDIDATE_UNCERTIFIED'}
    record.update(evidence.pop('_record_extra', None) or {})  # evidence_version 2 fields (admission_record)
    evidence['comparison_group'] = group
    if frames is None:
        return None
    return run_store.save_run(record, {**frames, 'metrics': _clean({k: evidence.get(k) for k in ('metrics', 'metrics_2x', 'gates_comparison', 'checks', 'period_status')})})


def run_queue(registry=workflow.REGISTRY):
    require_research()
    plan = json.loads((OUT / 'queue.json').read_text(encoding='utf-8'))
    levels, levels_note = benchmark_levels()
    for item in plan['queue']:
        target = ITEMS / (_safe(item['item_id']) + '.json')
        if target.is_file():
            continue
        if item.get('skip'):
            _json(target, {'item_id': item['item_id'], 'status': 'NOT_TESTED', 'reason': item['skip']})
            continue
        if item['kind'] == 'legacy_sensitivity':
            ok, reason = sensitivity_eligible(item['strategy_id'])
            if not ok:
                _json(target, {'item_id': item['item_id'], 'status': 'NOT_TESTED', 'reason': reason})
                continue
        if lab.round_trials() >= lab.ROUND_CAP:
            _json(target, {'item_id': item['item_id'], 'status': 'NOT_TESTED_BUDGET', 'reason': f'round cap {lab.ROUND_CAP} reached'})
            continue
        print(f"[{item['position']}] {item['item_id']} ...", flush=True)
        evidence = run_item(item, levels, levels_note, registry)
        print(f"    -> {evidence.get('status')} {evidence.get('reason') or ''} round trials {lab.round_trials()}", flush=True)


def run_item(item, levels, levels_note, registry=workflow.REGISTRY):
    """Run one queue item, save SUCCESS to the Run Store and any admitted failure as FAILED, write its evidence file."""
    target = ITEMS / (_safe(item['item_id']) + '.json')
    evidence = {}
    try:
        evidence = (run_legacy_item if item['kind'].startswith('legacy') else run_research_engine_item)(item, levels, registry)
        try:
            evidence['run_id'] = save(item, evidence, levels_note) if evidence.get('status') == 'SUCCESS' else None
        except Exception as exc:
            raise RuntimeError(f'SAVE_FAILED: {type(exc).__name__}: {exc}') from exc
    except lab.BudgetExhausted as exc:
        evidence = {'status': 'NOT_TESTED_BUDGET', 'reason': str(exc)}
    except Exception as exc:  # ponytail: one failed item is recorded and the queue continues; its trial (if admitted) stays counted
        evidence = {**evidence, 'status': 'FAILED', 'reason': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc()[-3000:]}
    evidence.pop('_frames', None)
    record = _clean({'item_id': item['item_id'], 'item': item, **evidence})
    if record['status'] in ('FAILED', 'NOT_COMPUTABLE') and record.get('experiment_id'):
        try:
            record['failure_run_id'] = save_failed(record)
        except Exception as exc:
            record['save_status'] = f'SAVE_FAILED: {type(exc).__name__}: {exc}'
    _json(target, record)
    return record


def save_failed(evidence):
    """FAILED Run Store record of an admitted item (error.json, no pointer move); returns its run ID."""
    item = evidence['item']
    sid = item.get('strategy_id') or item['item_id']
    record = {'strategy_id': sid, 'strategy_ids': [sid], 'strategy_type': item['kind'], 'item_id': item['item_id'],
              'hypothesis_id': evidence.get('hypothesis_id') or hypothesis_of(item), 'trial_id': evidence.get('experiment_id'),
              'status_class': 'RESEARCH' if item['kind'] == 'legacy_baseline' else 'EXPERIMENTAL', 'period': [lab.START, lab.END],
              'certification_status': 'NOT_CERTIFIED', 'outcome': evidence['status']}
    return run_store.save_failure(record, RuntimeError(f"{evidence['status']}: {evidence.get('reason')}"))


# ---------------------------------------------------------------- plan

def plan(registry=workflow.REGISTRY):
    require_research()
    rows, sets = cp.qualify()
    qualified = [r['condition_id'] for r in rows if r['status'] == 'QUALIFIED']
    combos = cp.combinations(qualified, sets)
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{k: (';'.join(v) if isinstance(v, list) else v) for k, v in r.items()} for r in rows]).to_csv(OUT / 'conditions.csv', index=False, encoding='utf-8')
    pd.DataFrame([{**c, 'conditions': '+'.join(c['conditions'])} for c in combos]).to_csv(OUT / 'combinations.csv', index=False, encoding='utf-8')
    sensitivity, not_injectable = [], {}
    for sid in legacy.RUNNABLE:
        overrides = legacy.SENSITIVITY[sid]
        check = legacy.check_injectable(sid, overrides)
        if check is True:
            sensitivity.append((sid, overrides))
        else:
            not_injectable[sid] = str(check)
    queue = lab.build_queue(qualified, combos, list(legacy.RUNNABLE), sensitivity, list(TQ))
    body, digest = lab.freeze_queue(queue, {'qualified': qualified, 'blocked_legacy': legacy.BLOCKED, 'sensitivity_not_injectable': not_injectable,
                                            'conditions_sha256': workflow.sha256(OUT / 'conditions.csv'),
                                            'combinations_sha256': workflow.sha256(OUT / 'combinations.csv')})
    _json(OUT / 'queue.sha256.json', {'queue_sha256': digest})
    register_all(body['queue'], registry)
    return body, digest


def register_all(queue, registry):
    existing = {r['hypothesis_id'] for r in workflow.csv.DictReader(Path(registry).open(encoding='utf-8-sig'))} if Path(registry).is_file() else set()
    rows = []
    for sid in legacy.RUNNABLE:
        rows.append({'hypothesis_id': f'LEGACY-{sid.upper()}-V1', 'strategy': sid,
                     'hypothesis': f'legacy rules-only baseline plus at most one fixed doubled-holding sensitivity (round {lab.ROUND})',
                     'falsification_test': 'gate result capped at DATA_BLOCKED (uncertified data); D3 KEEP/REVISE/STOP_RESEARCH rule on 2x-cost net Sharpe',
                     'data_window': f'{lab.START}..{lab.END}', 'trial_budget': '2', 'source': 'design.md D2 D3'})
    for key in TQ:
        rows.append({'hypothesis_id': f'TQ-{key.upper()}-V1', 'strategy': key, 'hypothesis': f'ported TQuant factor once on the lab engine: {TQ_FACTORS[key]}',
                     'falsification_test': 'exploratory; REPRODUCTION_DIFFERS; never a certification', 'data_window': f'{lab.START}..{lab.END}',
                     'trial_budget': '1', 'source': 'design.md D6'})
    for kind in ('single', 'weighted', 'intersection'):
        items = [q for q in queue if q['kind'] == kind and not q.get('skip')]
        if items:
            rows.append(lab.registration(kind, items, min(12, len(items))))
    for row in rows:
        if row['hypothesis_id'] not in existing:
            workflow.register(row, path=registry)


# ---------------------------------------------------------------- finalize

def finalize(registry=workflow.REGISTRY):
    require_research()
    items = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(ITEMS.glob('*.json'))]
    stored = {r.get('trial_id') for r in run_store.history() if r.get('status') == 'FAILED'}
    for e in items:
        if e['status'] in ('FAILED', 'NOT_COMPUTABLE') and e.get('experiment_id') and not e.get('failure_run_id') and e['experiment_id'] not in stored:
            e['failure_run_id'] = save_failed(e)
            _json(ITEMS / (_safe(e['item_id']) + '.json'), e)
    plan_body = json.loads((OUT / 'queue.json').read_text(encoding='utf-8'))
    order = {q['item_id']: i for i, q in enumerate(plan_body['queue'])}
    items.sort(key=lambda e: order.get(e['item_id'], 10**6))
    lab_runs = [{'run_id': e.get('run_id') or e['item_id'], 'item_id': e['item_id'], 'comparison_group': e.get('comparison_group'),
                 'status': e['status'], 'metrics': e.get('metrics') or {}}
                for e in items if e.get('item', {}).get('kind') in ('single', 'weighted', 'intersection', 'tq_baseline')]
    successful = [r for r in lab_runs if r['status'] == 'SUCCESS']
    groups = {r['comparison_group'] for r in successful}
    scored = ta.avg_scores(successful) if len(groups) == 1 else []
    summary = {'round': lab.ROUND, 'cap': lab.ROUND_CAP, 'round_trials': lab.round_trials(), 'effective_trials': workflow.effective_trials(),
               'queue_sha256': workflow.sha256(OUT / 'queue.json'), 'items': [{k: v for k, v in e.items() if k != 'traceback'} for e in items],
               'avg_scores': scored, 'heatmap': ta.heatmap_frame(scored) if scored else None,
               'top': ta.top_n(scored, 3) if scored else [], 'score_note': ta.SCORE_NOTE,
               'oos_status': 'OOS_NOT_AVAILABLE', 'certified': 'NO'}
    summary['top_analysis'] = {t['run_id']: deep_analysis(t['run_id']) for t in summary['top']}
    _json(OUT / 'lab_summary.json', _clean(summary))
    _json(OUT.parent / 'legacy_validation_v1/summary.json', legacy_summary(items))
    used = {e.get('hypothesis_id') for e in items if e.get('hypothesis_id')}
    done = workflow.results_path(registry)
    recorded = {r['hypothesis_id'] for r in workflow.csv.DictReader(done.open(encoding='utf-8-sig'))} if done.is_file() else set()
    used |= {hypothesis_of(e['item']) for e in items if e.get('experiment_id') and e.get('item')}
    outcomes = {hid: outcome_of([e['status'] for e in items if e.get('experiment_id') and hypothesis_of(e.get('item') or {}) == hid]) for hid in used}
    rows = list(workflow.csv.DictReader(done.open(encoding='utf-8-sig'))) if done.is_file() else []
    for hid in sorted(used):
        if hid not in recorded:
            workflow.record_result(hid, outcomes[hid], str(OUT / 'lab_summary.json'),
                                   note=f'round {lab.ROUND}; OOS_NOT_AVAILABLE; not a certification', path=registry)
        elif outcomes[hid] != 'EXPLORATORY_COMPLETE_UNCERTIFIED' and not any(r['hypothesis_id'] == hid and r['outcome'].startswith('CORRECTION') for r in rows):
            workflow.record_result(hid, 'CORRECTION_' + outcomes[hid], str(OUT / 'lab_summary.json'),
                                   note='the earlier EXPLORATORY_COMPLETE_UNCERTIFIED row was too generic: see items/*.json', path=registry)
    return summary


def hypothesis_of(item):
    """Registered hypothesis of a queue item (failed evidence may not carry it)."""
    kind = item.get('kind', '')
    if kind.startswith('legacy'):
        return f"LEGACY-{item['strategy_id'].upper()}-V1"
    if kind == 'tq_baseline':
        return f"TQ-{item['strategy_id'].upper()}-V1"
    return lab.HYPOTHESES.get(kind)


def outcome_of(statuses):
    """Per-hypothesis result: complete only when every admitted run succeeded."""
    if statuses and all(s == 'SUCCESS' for s in statuses):
        return 'EXPLORATORY_COMPLETE_UNCERTIFIED'
    if statuses and all(s in ('FAILED', 'NOT_COMPUTABLE') for s in statuses):
        return 'FAILED_ENGINE_ERROR' if 'FAILED' in statuses else 'NOT_COMPUTABLE_DATA_BLOCKED'
    return 'PARTIAL_' + '_'.join(sorted(set(statuses)))


def deep_analysis(run_id):
    """Top-run deep dive: time split (diagnostic), MAE/MFE, liquidity, factor analysis of its conditions."""
    loaded = run_store.load_run(run_id)
    nav = run_store.read_frame(loaded, 'nav')
    trades = run_store.read_frame(loaded, 'trades')
    series = pd.Series(nav['nav_base'].values, index=pd.to_datetime(nav['date']))
    split = {}
    for label, part in (('2023-2024', series[:'2024-12-31']), ('2025', series['2025-01-01':])):
        if len(part) > 1:
            split[label] = ta.performance(part / part.iloc[0] * lab.COSTS['initial_cash'], initial_cash=lab.COSTS['initial_cash'])
    market = json.loads((cp.PREPARED / 'bars.json').read_text(encoding='utf-8'))['bars']
    bars = {d: v for d, v in market.items() if lab.START <= d <= lab.END}
    conditions = loaded['run'].get('condition_ids') or []
    return _clean({'time_split': split, 'time_split_label': 'DIAGNOSTIC_TIME_SPLIT (not out of sample)',
                   'mae_mfe': ta.mae_mfe(trades, bars).to_dict('records') if len(trades) else [],
                   'liquidity': ta.liquidity(trades, bars) if len(trades) else {'status': 'NOT_COMPUTABLE', 'reason': 'NO_TRADES'},
                   'factor_analysis': factor_analysis(conditions, bars) if conditions else {'status': 'NOT_COMPUTABLE', 'reason': 'NO_CONDITIONS'}})


def factor_analysis(conditions, bars):
    days = cp.inputs()['days']
    calendar = sorted(bars)
    panel, forward = [], []
    for day in days:
        index = calendar.index(day) if day in calendar else None
        end = calendar[index + 20] if index is not None and index + 20 < len(calendar) else None
        for cid in conditions:
            frame = cp.day_scores(cid, day)
            panel += [{'asof_date': day, 'asset_id': a, 'factor_id': cid, 'value': float(v)} for a, v in frame['score'].items()]
        if end:
            for asset, bar in bars[day].items():
                later = bars[end].get(asset)
                if later and bar.get('close') and later.get('close'):
                    forward.append({'asof_date': day, 'asset_id': asset, 'horizon': 20, 'window_end': end,
                                    'forward_return': float(later['close']) / float(bar['close']) - 1})
    return ta.factor_analysis(pd.DataFrame(panel), pd.DataFrame(forward))


def d3_recommendation(base, sens, net2x, exc2x):
    """design D3: KEEP on a positive base; REVISE / STOP_RESEARCH only with an executed candidate; otherwise NOT_EVALUATED."""
    b, s = net2x(base), net2x(sens)
    if base.get('status') != 'SUCCESS' or b is None or exc2x(base) is None:
        return 'NOT_EVALUATED'
    if b > 0 and exc2x(base) > 0:
        return 'KEEP'
    if sens.get('status') != 'SUCCESS' or s is None:
        return 'NOT_EVALUATED'  # baseline not positive and no executed candidate: the rule cannot decide
    if b <= 0 and s > 0 and ((sens.get('gates_comparison') or {}).get('gates') or {}).get('g4'):
        return 'REVISE'
    if b <= 0 and s <= 0 and exc2x(base) <= 0 and (exc2x(sens) or 0) <= 0:
        return 'STOP_RESEARCH'
    return 'NOT_EVALUATED'


def legacy_summary(items):
    """Per legacy strategy: gate result, data health, D3 recommendation, runs (feeds the Strategy Center)."""
    by_id = {e['item_id']: e for e in items}
    out = {}
    for sid in list(legacy.RUNNABLE) + list(legacy.BLOCKED):
        if sid in legacy.BLOCKED:
            out[sid] = {'gate_result': 'DATA_BLOCKED', 'data_health': legacy.BLOCKED[sid], 'recommendation': 'NOT_EVALUATED', 'baseline': None}
            continue
        base, sens = by_id.get(f'LEGACY:{sid}', {}), by_id.get(f'LEGACY-SENS:{sid}', {})
        checks = base.get('checks') or {}
        def net2x(e):
            return ((e.get('gates_comparison') or {}).get('values') or {}).get('net_sharpe_2x')
        def exc2x(e):
            return ((e.get('gates_comparison') or {}).get('values') or {}).get('excess_sharpe_2x')
        rec = d3_recommendation(base, sens, net2x, exc2x)
        health = 'raw prices, no corporate-action adjustment in the legacy engine; ' + str(checks.get('pit_status', 'PIT_UNKNOWN'))
        if checks.get('degenerate'):
            health += '; DEGENERATE_FACTOR'
        out[sid] = {'gate_result': 'DATA_BLOCKED', 'data_health': health, 'recommendation': rec,
                    'baseline': {k: base.get(k) for k in ('status', 'run_id', 'reason')}, 'sensitivity': {k: sens.get(k) for k in ('status', 'run_id', 'reason')}}
    return _clean(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--plan', action='store_true')
    group.add_argument('--run', action='store_true')
    group.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    if args.plan:
        body, digest = plan()
        print(json.dumps({'queue_sha256': digest, 'items': len(body['queue']), 'qualified': body['qualified'],
                          'not_injectable': body['sensitivity_not_injectable']}, ensure_ascii=False))
    elif args.run:
        run_queue()
    else:
        print(json.dumps({k: v for k, v in finalize().items() if k in ('round_trials', 'effective_trials', 'top')}, ensure_ascii=False, default=str))
