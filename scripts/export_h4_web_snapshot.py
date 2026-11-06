"""H4 web snapshot (h4-web-integration-v1 design D2): archived 2x-cost run, Benchmark V2 and 12 replayed 60-day signal days.

Everything is recomputed with the archived producers and checked against the archive before a single file is written.
Writes only outputs/research_mode/h4_web_v1; no Ledger, registry, database, LINE or broker path."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research import h4_signal, strategy_signal as ss
from scripts import research_workflow as workflow

RESEARCH = ROOT / 'outputs/research_mode'
OUTPUT = RESEARCH / 'h4_web_v1'
RUN = RESEARCH / 'h4_runs_v1/002'
RESULT = RESEARCH / 'report_h4_v2/h4_result.json'
ATTRIBUTION = RESEARCH / 'h4_attribution_v1/summary.json'
SCORES = RESEARCH / 'h4_signals_v1/scores_00_60.json'
PIT = RESEARCH / 'h4_final_resolution_v1/pit.json'
DECISION = ROOT / 'docs/research/2026-11-05-h4-final-resolution-v1/h4_strategy_decision.md'
PRIMARY = 'H4_GROWTH_MOMENTUM__TOP10__REB60__EQUAL'
INITIAL_CAPITAL = 1_000_000
TOL = dict(rtol=1e-9, atol=1e-12)


def _close(a, b):
    return bool(np.isclose(a, b, **TOL))


def performance():
    """Reuse the screen's own path (load_runs -> build_result on Benchmark V2) so every number is the archived one."""
    from scripts import track_b_stats as stats
    from scripts.research_mode_report import build_result, load_runs
    from scripts.research_validation_v2 import _prepared, benchmark_v2
    archived = json.loads(RESULT.read_text(encoding='utf-8'))
    primary = archived['primary']
    attribution = json.loads(ATTRIBUTION.read_text(encoding='utf-8'))
    for name, expected in attribution['run_outputs_sha256']['H4'].items():
        if workflow.sha256(RUN / name) != expected:
            raise ValueError(f'ARCHIVE_SHA_MISMATCH:{name}')
    bars, events, _ = _prepared()
    proxy, per_turnover = benchmark_v2(bars, events)
    bench = proxy['returns']
    record = next(r for r in load_runs(RESEARCH / 'h4_runs_v1', 'H4') if r['config'].get('config_id') == PRIMARY)
    if record['status'] != 'COMPUTABLE':
        raise ValueError(f"ARCHIVE_NOT_COMPUTABLE:{record.get('reason')}")
    res = build_result(record, bench, stats.regimes((1 + bench).cumprod()))
    net = pd.Series(res['net_returns_2x'], index=res['excess_series'].index)
    yearly = {str(y): float((1 + g).prod() - 1) for y, g in net.groupby(net.index.year)}
    checks = {'total_return_2x': (res['stress']['total_return'], primary['total_return_2x']),
              'max_drawdown_2x': (res['stress']['max_drawdown'], primary['max_drawdown_2x']),
              'total_return_1x': (res['base']['total_return'], primary['total_return_1x']),
              'benchmark_v2_total_return': (res['benchmark_total_return'], primary['benchmark_v2_total_return']),
              **{f'yearly_{y}': (v, primary['yearly'][y]['strategy']) for y, v in yearly.items()}}
    failed = [k for k, (got, want) in checks.items() if not _close(got, want)]
    if failed:
        raise ValueError('ARCHIVE_RECOMPUTE_MISMATCH:' + ','.join(failed))
    tracks = record['_tracks']
    nav = {name: [row['nav'] for row in tracks[name]['result']['daily']] for name in ('stress', 'base')}
    dates = [row['date'] for row in tracks['stress']['result']['daily']]
    fills = [r for r in tracks['stress']['result']['ledger'] if r['kind'] == 'FILL']
    stocks = attribution['stocks']
    return {
        'strategy_id': h4_signal.STRATEGY_ID, 'config_id': PRIMARY, 'label': archived['label'], 'data_label': archived['data_label'],
        'period': {'start': dates[0], 'end': dates[-1]}, 'initial_capital': INITIAL_CAPITAL,
        'cost': {**tracks['stress']['result']['cost'], 'slippage': tracks['stress']['result']['slippage'], 'basis': '2x research cost (stress track), not leverage',
                 'benchmark_cost_per_turnover_diagnostic': per_turnover},
        'benchmark': {'name': 'Benchmark V2', 'definition': 'equal-weight total return of V1 members on raw development bars, cash and stock dividends credited on ex-date, no cost',
                      'status': proxy['diagnostics']['status']},
        'market_cap_weighted_total_return': {'value': None, 'reason': 'NO_TOTAL_RETURN_INDEX_IN_DEVELOPMENT_INPUTS: only the TAIEX price index is cached'},
        'series': {'dates': dates, 'nav_2x': nav['stress'], 'nav_1x': nav['base'],
                   'benchmark_v2_return': [None if pd.isna(v) else float(v) for v in bench.reindex(pd.to_datetime(dates))]},
        'metrics': {k: primary[k] for k in ('total_return_2x', 'cagr_2x', 'sharpe_2x', 'max_drawdown_2x', 'turnover_2x', 'total_return_1x',
                                            'sharpe_1x', 'benchmark_v2_total_return', 'sessions', 'alpha_annual', 'alpha_p_hac', 'beta', 'first_fill')},
        'yearly': primary['yearly'], 'gates': primary['gates'], 'gate_values': primary['values'], 'screen_status': primary['screen_status'],
        'dsr_trials': primary['dsr_trials'], 'trading_cost': {'fees': float(sum(r['fee'] for r in fills)), 'taxes': float(sum(r['tax'] for r in fills)),
                                                              'fills': len(fills)},
        'concentration': {'top5_share_of_net': stocks['top5_share_of_net'], 'top5': stocks['top5'], 'bottom5': stocks['bottom5'],
                          'held': stocks['held'], 'best5pct_days_return_share': primary['values']['best5pct_days_return_share'],
                          'yearly_strategy_return': {y: v['strategy'] for y, v in primary['yearly'].items()}},
        'capacity_multiple': attribution['liquidity']['H4']['capital_multiple_for_p90_fill_at_5pct_adv'],
    }, fills


def status():
    """Validation and period status from the evidence files; nothing here is a template constant on the web side."""
    archived = json.loads(RESULT.read_text(encoding='utf-8'))
    registration = [r for r in csv.DictReader(workflow.results_path().open(encoding='utf-8-sig')) if r['hypothesis_id'] == h4_signal.STRATEGY_ID]
    hist = [r for r in csv.DictReader(workflow.REGISTRY.open(encoding='utf-8-sig')) if r['hypothesis_id'] == 'H4:HIST-2016-2022-V1']
    pit = json.loads(PIT.read_text(encoding='utf-8'))
    decision = DECISION.read_text(encoding='utf-8')
    if '研究建議維持 REVISE' not in decision or len(registration) != 1 or len(hist) != 1:
        raise ValueError('STATUS_EVIDENCE_MISSING')
    return {'certification': 'RESEARCH_UNCERTIFIED', 'registration_decision': registration[0]['outcome'], 'research_advice': 'REVISE',
            'gates': {k: 'PASS' if v else 'FAIL' for k, v in archived['primary']['gates'].items()},
            'periods': [{'range': '2023-2025', 'status': 'ARCHIVED', 'reason': 'development window, fixed H4-V2 configuration'},
                        {'range': '2016-2022', 'status': 'DATA_BLOCKED',
                         'reason': f"PIT {pit['status']}: {pit['correction_dependent_member_days']} of {pit['member_days']} signal-day member-days depend on "
                                   f"corrected filings without a recoverable first version; {hist[0]['hypothesis_id']} {hist[0]['status']}"},
                        {'range': '2026', 'status': 'SEALED', 'reason': f'out-of-sample sealed after {ss.DEV_END}; no Owner authorization'}],
            'evidence_sha256': {str(p.relative_to(ROOT)).replace('\\', '/'): workflow.sha256(p) for p in (RESULT, PIT, DECISION)}}


def signal_days():
    archived = pd.DataFrame(json.loads(SCORES.read_text(encoding='utf-8')))
    targets = pd.read_csv(RUN / 'targets.csv', dtype={'asset_id': str}, encoding='utf-8-sig')
    days = sorted(targets['asof_date'].astype(str).str[:10].unique())
    out, previous = {}, None
    for day in days:
        signals = h4_signal.H4Executor().execute(h4_signal.dev_context(day), h4_signal.SPEC).metadata['signals']
        scored = signals.loc[signals['eligibility_status'].isin(['ELIGIBLE', 'EVENT_EXCLUDED'])].set_index('ticker')['final_score'].sort_index()
        expected = archived.loc[archived['asof_date'] == day].set_index('asset_id')['composite_score'].sort_index()
        chosen = signals.loc[signals['target_weight'] > 0].sort_values('ticker')
        want = targets.loc[targets['asof_date'].astype(str).str[:10] == day].sort_values('asset_id')
        if (list(scored.index) != list(expected.index) or not np.allclose(scored, expected, **TOL)
                or list(chosen['ticker']) != list(want['asset_id']) or not np.allclose(chosen['target_weight'], want['target_weight'], **TOL)):
            raise ValueError(f'SIGNAL_REPLAY_MISMATCH:{day}')
        target = ss.target_portfolio(signals, h4_signal.SPEC.portfolio_policy['max_weight'])
        out[day] = (signals, ss.rebalance(target, previous))
        previous = target
    return out


def write(perf, fills, state, days, output=OUTPUT):
    output = Path(output).resolve()
    if not output.is_relative_to(RESEARCH.resolve()) or output == RESEARCH.resolve():
        raise ss.ResearchOnlyViolation('OUTPUT_OUTSIDE_RESEARCH_DIRECTORY')
    (output / 'signals').mkdir(parents=True, exist_ok=True)
    (output / 'rebalance').mkdir(exist_ok=True)
    (output / 'performance.json').write_text(json.dumps({**perf, 'status': state}, ensure_ascii=False, default=str) + '\n', encoding='utf-8')
    pd.DataFrame(fills)[['date', 'order_id', 'stock_id', 'side', 'shares', 'price', 'notional', 'fee', 'tax']].to_csv(output / 'trades.csv', index=False, encoding='utf-8')
    for day, (signals, change) in days.items():
        signals.to_csv(output / 'signals' / f'{day}.csv', index=False, encoding='utf-8')
        change.to_csv(output / 'rebalance' / f'{day}.csv', index=False, encoding='utf-8')
    files = sorted(p for p in output.rglob('*') if p.is_file() and p.name != 'manifest.json')
    sources = [RUN / n for n in ('targets.csv', 'base.json', 'stress.json')] + [RESULT, ATTRIBUTION, SCORES, PIT, DECISION]
    manifest = {'kind': 'H4_WEB_SNAPSHOT_V1', 'strategy_id': h4_signal.STRATEGY_ID, 'research_only': True, 'publish': 'DISABLED',
                'signal_dates': list(days), 'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'producer_sha256': workflow.sha256(Path(__file__)),
                'sources_sha256': {str(p.relative_to(ROOT)).replace('\\', '/'): workflow.sha256(p) for p in sources},
                'files_sha256': {p.relative_to(output).as_posix(): workflow.sha256(p) for p in files}}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    perf, fills = performance()
    state = status()
    days = signal_days()
    manifest = write(perf, fills, state, days)
    print(json.dumps({'signal_dates': manifest['signal_dates'], 'files': len(manifest['files_sha256'])}))
