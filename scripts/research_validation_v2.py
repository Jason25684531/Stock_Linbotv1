"""research-validation-execution-v1: integrity evidence, V2 benchmark, H1 near_high_252d (offline, development window only)."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

RESEARCH = ROOT / 'outputs/research_mode'
EVIDENCE = RESEARCH / 'validation_execution_v1'
SEALED = ('report_v6', 'report_v7_hypcost', 'day3_runs_v6', 'd5_runs_v5', 'prepared_v6', 'day3_signals_v6',
          'h1_signals_v1', 'h1_runs_v1', 'report_h1_v2', 'report_v6_v2_diagnostic', 'h2_a191_liquidity_v1', 'g2g3_factor_dev_v1')
WATCHED_CODE = ('core/research/repair_runner.py', 'core/research/research_mode.py', 'core/research/research_screen.py',
                'scripts/research_mode_run.py', 'scripts/research_mode_report.py')


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False, default=str) + '\n', encoding='utf-8')
    return value


def integrity(name):
    """Hash sealed research trees, watched code, registry and both ledgers; written before and after the round."""
    registry_rows = list(csv.DictReader(workflow.REGISTRY.open(encoding='utf-8-sig')))
    results = workflow.results_path()
    return _write(EVIDENCE / f'integrity_{name}.json', {
        'label': 'DIAGNOSTIC_ONLY',
        'sealed_tree_sha256': {d: workflow._tree_sha(RESEARCH / d) for d in SEALED},
        'code_sha256': {p: workflow.sha256(ROOT / p) for p in WATCHED_CODE},
        'registry': workflow.verify_registry(), 'registry_rows': len(registry_rows),
        'registry_ids_tail': [r['hypothesis_id'] for r in registry_rows[-5:]],
        'results_rows': sum(1 for _ in csv.DictReader(results.open(encoding='utf-8-sig'))) if results.is_file() else 0,
        'formal_ledger': workflow.verify_trials(), 'formal_effective_trials': workflow.effective_trials(),
        'research_ledger': workflow.verify_trials(workflow.RESEARCH_MODE_LEDGER),
        'research_rows': len(workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER)),
        'research_trials': workflow.research_trials()})


def _prepared():
    """prepared_v6 bars and actions, after checking every file against its input manifest."""
    prepared = RESEARCH / 'prepared_v6'
    manifest = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
    if any(workflow.sha256(prepared / name) != digest for name, digest in manifest['files'].items()):
        raise ValueError('INPUT_CHECKPOINT_UNVERIFIED')
    market = json.loads((prepared / 'bars.json').read_text(encoding='utf-8'))
    events = json.loads((prepared / 'actions.json').read_text(encoding='utf-8'))
    if market.get('price_basis') != 'RAW' or max(market['bars']) > '2025-12-31':
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    return market['bars'], events, {name: digest for name, digest in manifest['files'].items()}


def benchmark_v2(bars, events):
    from core.research.benchmark_v2 import equal_weight_total_return
    from scripts.research_mode_run import COSTS
    # 2x research cost per unit of one-side turnover: buy (fee+slippage) plus sell (fee+tax+slippage), every term doubled
    per_turnover = 2 * (2 * COSTS['fee_rate'] + COSTS['tax_rate'] + 2 * COSTS['slippage'])
    return equal_weight_total_return(bars, events['actions'], events['unresolved_events'], cost_per_turnover=per_turnover), per_turnover


def benchmark():
    import pandas as pd
    from scripts.research_mode_report import benchmark_returns
    bars, events, inputs = _prepared()
    out, per_turnover = benchmark_v2(bars, events)
    series = pd.DataFrame({'v1_price': benchmark_returns(bars), 'v2_total': out['returns'], 'v2_price_check': out['price_returns'],
                           'v2_net_of_turnover_cost': out['net_of_cost'], 'v2_turnover': out['turnover']})
    series = series.loc[series.index >= '2023-01-03']
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    series.to_csv(EVIDENCE / 'benchmark_series.csv', index_label='date')
    yearly = {str(y): {c: float((1 + g[c]).prod() - 1) for c in ('v1_price', 'v2_total', 'v2_net_of_turnover_cost')}
              for y, g in series.groupby(series.index.year)}
    full = {c: float((1 + series[c]).prod() - 1) for c in ('v1_price', 'v2_total', 'v2_net_of_turnover_cost')}
    return _write(EVIDENCE / 'benchmark_v2.json', {
        'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'window': ['2023-01-03', '2025-12-31'],
        'inputs_sha256': inputs, 'benchmark_v2_sha256': workflow.sha256(ROOT / 'core/research/benchmark_v2.py'),
        'cost_per_one_side_turnover_2x': per_turnover, 'diagnostics': out['diagnostics'],
        'sessions_price_component_differs_from_v1': int(((series['v1_price'] - series['v2_price_check']).abs() > 1e-12).sum()),
        'sessions': len(series), 'yearly_return': yearly, 'full_window_return': full,
        'mean_daily_turnover': float(series['v2_turnover'].mean()), 'max_date': str(series.index.max().date())})


H1_GRID = ROOT / 'outputs/strategy_research/h1_near_high_252d_v1/strategy_grid.csv'
H1_ID, H2_ID = 'near_high_252d:H1', 'A191_132:H2-LIQUIDITY'
H1_PRIMARY = 'near_high_252d__EQUAL__TOP10__REB60__EQUAL'


def h1_grid():
    """The archived single-factor grid shape for near_high_252d: 3 Top-N x 2 cadences x 2 weightings, written once."""
    import hashlib
    import itertools
    import pandas as pd
    from core.research import strategy_search as search
    from scripts.research_mode_run import COSTS
    composite = 'near_high_252d__EQUAL'
    rows = []
    for top_n, cadence, weighting in itertools.product(search.TOP_N_VALUES, search.REBALANCE_DAYS_VALUES, search.PORTFOLIO_WEIGHTINGS):
        identity = {'factor_set': ['near_high_252d'], 'factor_weighting': 'equal', 'factor_weights': {'near_high_252d': 1.0},
                    'factor_direction': 1, 'factor_source': 'd5_research_variant.raw_factor_panels', 'top_n': top_n,
                    'rebalance_days': cadence, 'portfolio_weighting': weighting, 'universe_id': search.UNIVERSE_ID, **COSTS,
                    'start_date': '2023-01-03', 'end_date': '2025-12-31'}
        rows.append({'strategy_id': f'{composite}__TOP{top_n}__REB{cadence}__{weighting.upper()}', 'composite_id': composite,
                     'factor_set': 'near_high_252d', 'factor_weighting': 'equal', 'top_n': top_n, 'rebalance_days': cadence,
                     'portfolio_weighting': weighting, 'implementation_risk': 'NONE',
                     'config_hash': hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()})
    text = pd.DataFrame(rows).sort_values('strategy_id', kind='stable').to_csv(index=False, lineterminator='\n')
    if len(rows) != 12 or H1_PRIMARY not in text:
        raise ValueError('H1_GRID_SHAPE')
    if H1_GRID.exists() and H1_GRID.read_text(encoding='utf-8') != text:
        raise ValueError('H1_GRID_CHANGED')
    H1_GRID.parent.mkdir(parents=True, exist_ok=True)
    H1_GRID.write_text(text, encoding='utf-8')
    return workflow.sha256(H1_GRID)


def registrations():
    from core.research.research_mode import screen_registration
    window = '2023-01-03 to 2025-12-31; no 2026 OOS'
    return {
        'V2': screen_registration('V2'),
        'H1': {'hypothesis_id': H1_ID, 'strategy': 'near_high_252d',
               'hypothesis': ('near_high_252d single factor (direction +1, raw-bar factor from d5_research_variant.raw_factor_panels, '
                              'V6 Day3 ranking, membership, timing, cost and event rules) earns positive 2x-cost alpha against the '
                              'RESEARCH-SCREEN:V2 proxy. Primary configuration ' + H1_PRIMARY + '; the other 11 are sensitivity only. '
                              'Alpha is the daily OLS intercept on the V2 proxy times 252 with HAC inference (attribution.run_attribution). '
                              'Known before any result: bars start 2021-12-30, so the 252-session factor first exists on 2023-01-09; '
                              'the 2023-01-03 signal date has no scores and every configuration holds cash until its next signal date '
                              '(2023-02-10 for 20D, 2023-04-14 for 60D); those cash sessions stay in every metric.'),
               'falsification_test': ('CONTINUE when the primary configuration has 2x alpha > 0 and r1 (net and excess 2x Sharpe > 0 under V2) '
                                      'passes; REVISE when alpha > 0 and r1 fails; STOP when alpha <= 0; NOT_EVALUABLE when the primary '
                                      'configuration is not computable. No parameter change or rerun after any result.'),
               'data_window': window, 'trial_budget': '12', 'source': 'strategy_grid.csv sha256 ' + h1_grid()},
        'H2': {'hypothesis_id': H2_ID, 'strategy': 'A191_132',
               'hypothesis': ('A191_132 (20-session mean official trading amount) predicts beyond amihud_20d on the full development '
                              'cross-section, and the archived V6 A191_132 configurations do not depend on illiquid stocks. Analysis '
                              'reads archived day3_runs_v6 ledgers and prepared_v6 only; no backtest.'),
               'falsification_test': ('Incremental when the residual of rank(A191_132) on rank(amihud_20d) has mean IC > 0 and positive-IC '
                                      'ratio > 0.5 at both 20 and 60 sessions (labels ending by 2025-12-31). Over-reliant when the lowest '
                                      'liquidity amihud tercile gives >= 50 percent of net profit or fills above 10 percent of the prior '
                                      '20-session mean amount exceed 20 percent of notional, judged on the V6 best configuration '
                                      'A191_132__EQUAL__TOP10__REB60__EQUAL. CONTINUE incremental and not over-reliant; REVISE incremental '
                                      'and over-reliant; STOP not incremental. The V7 erratum verdict is not changed.'),
               'data_window': window, 'trial_budget': '0',
               'source': 'day3_runs_v6 tree sha256 ' + workflow._tree_sha(RESEARCH / 'day3_runs_v6')},
    }


def register(name):
    from core.runtime.operation_policy import ExecutionMode, current_mode
    if current_mode() is not ExecutionMode.RESEARCH:
        raise PermissionError('RESEARCH_MODE_REQUIRED')
    if name == 'H1' and json.loads((EVIDENCE / 'benchmark_v2.json').read_text(encoding='utf-8'))['diagnostics']['status'] != 'PASS':
        raise ValueError('V2_BLOCKED')
    return workflow.register(registrations()[name])


def _same(left, right, path='root'):
    """First structural difference, floats within rtol 1e-9 / atol 1e-12 (the V7 replay rule); None when equal."""
    import math
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys() != right.keys():
            return f'{path}: keys differ'
        return next((d for k in left if (d := _same(left[k], right[k], f'{path}.{k}'))), None)
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return f'{path}: length {len(left)} != {len(right)}'
        return next((d for i, (a, b) in enumerate(zip(left, right)) if (d := _same(a, b, f'{path}[{i}]'))), None)
    if isinstance(left, float) or isinstance(right, float):
        ok = isinstance(left, (int, float)) and isinstance(right, (int, float)) and math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
        return None if ok else f'{path}: {left!r} != {right!r}'
    return None if left == right else f'{path}: {left!r} != {right!r}'


def replay(tag=''):
    """Characterization after a PIT_RAW_CODE edit: rerun V6 configurations 289 and 129 into a new directory against a throwaway
    research ledger (a replay is a test, not a research trial) and compare with the sealed V6 outputs. A tag keeps each edit's evidence."""
    from core.research.repair_runner import run
    from scripts.research_mode_run import FAMILIES, configs
    spec = FAMILIES['day3']
    items = list(configs('day3', spec['prepared'], spec['signals'], spec['output'] / 'runner_evidence.json'))
    result = {}
    for index in (289, 129):
        target = EVIDENCE / ('replay' + (f'_{tag}' if tag else '')) / str(index)
        status = run({**items[index], 'output': str(target)}, research_ledger=EVIDENCE / f"replay_ledger{'_' + tag if tag else ''}.jsonl")['manifest']['status']
        archived = spec['output'] / f'{index:03d}'
        checks = {'status': status, 'targets.csv': 'PASS' if (archived / 'targets.csv').read_bytes() == (target / 'targets.csv').read_bytes() else 'FAIL'}
        for track in ('base', 'stress'):
            error = _same(*(json.loads((d / f'{track}.json').read_text(encoding='utf-8')) for d in (archived, target)))
            checks[track] = 'PASS' if error is None else 'FAIL: ' + error
        result[str(index)] = checks
    passed = all(c['status'] == 'COMPUTABLE' and all(v == 'PASS' for k, v in c.items() if k != 'status') for c in result.values())
    return _write(EVIDENCE / f"replay_comparison{'_' + tag if tag else ''}.json", {'label': 'DIAGNOSTIC_ONLY', 'status': 'PASS' if passed else 'FAIL',
                                                        'repair_runner_sha256': workflow.sha256(ROOT / 'core/research/repair_runner.py'),
                                                        'comparisons': result})


SCREENED = {  # family -> (hypothesis, runs folder, family label, report folder, primary configuration, grid file)
    'h1': (H1_ID, 'h1_runs_v1', 'H1', 'report_h1_v2', H1_PRIMARY, H1_GRID),
    'h4': ('H4:GROWTH-MOMENTUM-V2', 'h4_runs_v1', 'H4', 'report_h4_v2', 'H4_GROWTH_MOMENTUM__TOP10__REB60__EQUAL',
           ROOT / 'outputs/strategy_research/h4_growth_momentum_v1/strategy_grid.csv'),
}


def screen_v2(family='h1'):
    """Screen H1 under RESEARCH-SCREEN:V2. Trial Sharpe variance is taken over the same batch V1 used (every computable V6 Day3 and
    D5 configuration) plus the H1 configurations; V6 rows are written once more under V2 as DIAGNOSTIC_ONLY and never replace V1."""
    import math
    import numpy as np
    import pandas as pd
    from core.research.attribution import run_attribution
    from core.research.research_screen import screen
    from scripts import track_b_stats as stats
    from scripts.research_mode_report import build_result, load_runs
    hid, runs, label, report, primary_id, grid = SCREENED[family]
    workflow.require_registered(hid)  # one verdict per registration
    bars, events, inputs = _prepared()
    proxy, _ = benchmark_v2(bars, events)
    if proxy['diagnostics']['status'] != 'PASS':
        raise ValueError('V2_BLOCKED')
    bench = proxy['returns']
    regime = stats.regimes((1 + bench).cumprod())
    records = (load_runs(RESEARCH / runs, label) + load_runs(RESEARCH / 'day3_runs_v6', 'DAY3')
               + load_runs(RESEARCH / 'd5_runs_v5', 'D5_VARIANT'))
    results = {id(r): build_result(r, bench, regime) for r in records if r['status'] == 'COMPUTABLE'}
    sharpes = [stats.sharpe(res['excess_returns_2x'], periods=1) for res in results.values()]
    variance = float(np.var([s for s in sharpes if math.isfinite(s)], ddof=1))
    rows = []
    for record in records:
        config = record['config']
        row = {'family': record['_family'], 'strategy': config.get('strategy'), 'config_id': config.get('config_id'),
               'status': record['status'], 'reason': record.get('reason')}
        if record['status'] == 'COMPUTABLE':
            res = results[id(record)]
            verdict = screen(res, trial_sharpe_variance=variance, version='V2')
            net = pd.Series(res['net_returns_2x'], index=res['excess_series'].index)
            fit = run_attribution(row['config_id'], net, bench)
            years = {str(y): {'strategy': float((1 + g).prod() - 1), 'benchmark_v2': float((1 + bench.reindex(g.index)).prod() - 1)}
                     for y, g in net.groupby(net.index.year)}
            fills = [r['date'] for r in record['_tracks']['stress']['result']['ledger'] if r['kind'] == 'FILL']
            row.update(screen_status=verdict['status'], gates=verdict.get('gates'), values=verdict.get('values'),
                       research_trials=verdict.get('research_trials'), dsr_trials=verdict.get('dsr_trials'),
                       total_return_2x=res['stress']['total_return'], cagr_2x=(1 + res['stress']['total_return']) ** (252 / res['sessions']) - 1,
                       sharpe_2x=res['stress']['sharpe'], max_drawdown_2x=res['stress']['max_drawdown'], turnover_2x=res['stress']['turnover'],
                       total_return_1x=res['base']['total_return'], sharpe_1x=res['base']['sharpe'], turnover_1x=res['base']['turnover'],
                       benchmark_v2_total_return=res['benchmark_total_return'], sessions=res['sessions'],
                       alpha_annual=fit['alpha'] * 252, alpha_p_hac=fit['alpha_p_hac'], beta=fit['beta'],
                       first_fill=min(fills) if fills else None, yearly=years)
        rows.append(row)
    h1 = [r for r in rows if r['family'] == label]
    primary = next((r for r in h1 if r['config_id'] == primary_id), None)
    if primary is None or primary['status'] != 'COMPUTABLE' or primary.get('gates') is None:
        decision = 'NOT_EVALUABLE'
    elif primary['alpha_annual'] <= 0:
        decision = 'STOP'
    else:
        decision = 'CONTINUE' if primary['gates']['r1'] else 'REVISE'
    common = {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'screen_version': 'RESEARCH-SCREEN:V2',
              'benchmark_v2_sha256': workflow.sha256(ROOT / 'core/research/benchmark_v2.py'), 'inputs_sha256': inputs,
              'trial_sharpe_variance': variance, 'variance_population': len([s for s in sharpes if math.isfinite(s)]),
              'research_trials': workflow.research_trials(), 'formal_effective_trials': workflow.effective_trials()}
    sensitivity = None
    if primary and primary.get('gates'):  # DSR of the primary configuration at several trial counts (diagnostic)
        excess = next(results[id(r)]['excess_returns_2x'] for r in records if r['_family'] == label and r['config']['config_id'] == primary_id)
        sensitivity = {str(n): float(stats.deflated_sharpe(excess, n, variance))
                       for n in (1, 12, common['variance_population'], common['research_trials'], 743)}
    written = [(report, h1)] + ([('report_v6_v2_diagnostic', [r for r in rows if r['family'] != label])] if family == 'h1' else [])
    for name, part in written:
        target = RESEARCH / name
        target.mkdir(parents=True, exist_ok=True)
        _write(target / 'screen_results.json', {**common, 'rows': part})
        flat = [{k: (json.dumps(v, default=float) if isinstance(v, dict) else v) for k, v in r.items()} for r in part]
        pd.DataFrame(flat).to_csv(target / 'screen_results.csv', index=False, encoding='utf-8-sig')
    result = _write(RESEARCH / report / f'{family}_result.json', {
        **common, 'hypothesis_id': hid, 'primary_config': primary_id, 'decision': decision, 'primary': primary,
        'primary_dsr_by_trial_count': sensitivity,
        'configurations': len(h1), 'computable': sum(r['status'] == 'COMPUTABLE' for r in h1),
        'grid_sha256': workflow.sha256(grid)})
    workflow.record_result(hid, decision, f'outputs/research_mode/{report}/{family}_result.json',
                           f"alpha_annual={primary and primary.get('alpha_annual')} r1={primary and (primary.get('gates') or {}).get('r1')}")
    return {k: v for k, v in result.items() if k != 'primary'} | {'primary_summary': {
        k: primary.get(k) for k in ('status', 'alpha_annual', 'alpha_p_hac', 'total_return_2x', 'sharpe_2x', 'gates')} if primary else None}


def h1_table(path, family='h1'):
    """Flatten report_h1_v2 into one row per registered configuration for the delivery folder."""
    import pandas as pd
    _, _, _, folder, primary_id, _ = SCREENED[family]
    report = json.loads((RESEARCH / folder / 'screen_results.json').read_text(encoding='utf-8'))
    rows = []
    for r in report['rows']:
        values, gates = r.get('values') or {}, r.get('gates') or {}
        rows.append({'config_id': r['config_id'], 'role': 'PRIMARY' if r['config_id'] == primary_id else 'SENSITIVITY', 'status': r['status'],
                     'reason': r.get('reason'), 'screen_status': r.get('screen_status'), 'first_fill': r.get('first_fill'),
                     **{k: r.get(k) for k in ('total_return_2x', 'cagr_2x', 'sharpe_2x', 'max_drawdown_2x', 'total_return_1x', 'sharpe_1x',
                                              'turnover_1x', 'turnover_2x', 'benchmark_v2_total_return', 'alpha_annual', 'alpha_p_hac', 'beta', 'sessions')},
                     **{k: values.get(k) for k in ('net_sharpe_2x', 'excess_sharpe_2x', 'bootstrap_lower', 'deflated_sharpe',
                                                   'top5_assets_profit_share', 'best5pct_days_return_share')},
                     **{g: gates.get(g) for g in ('r1', 'r2', 'r3', 'r4', 'r5')},
                     'regime_bull_excess': ((values.get('regime') or {}).get('bull') or {}).get('annualized_excess'),
                     'regime_bear_excess': ((values.get('regime') or {}).get('bear') or {}).get('annualized_excess'),
                     **{f'{y}_{k}': v for y, d in (r.get('yearly') or {}).items() for k, v in d.items()},
                     'dsr_trials': r.get('dsr_trials'), 'research_trials': r.get('research_trials')})
    pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')
    return len(rows)


if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'h1-table':
        print(h1_table(Path(sys.argv[2]), *sys.argv[3:]))
        raise SystemExit(0)
    if command == 'screen':
        print(json.dumps(screen_v2(*sys.argv[2:]), ensure_ascii=False, indent=1, default=float))
        raise SystemExit(0)
    if command == 'replay':
        print(json.dumps(replay(*sys.argv[2:]), ensure_ascii=False, indent=1))
        raise SystemExit(0)
    if command == 'register':
        print(json.dumps(register(sys.argv[2]), ensure_ascii=False, indent=1))
        raise SystemExit(0)
    if command == 'integrity':
        print(json.dumps(integrity(sys.argv[2]), ensure_ascii=False, indent=1))
    elif command == 'benchmark':
        print(json.dumps(benchmark(), ensure_ascii=False, indent=1))
    else:
        raise SystemExit(f'unknown command {command}')
