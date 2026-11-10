"""Four fixed OD-29 recovery baselines; each admission is durable and never overwrites old evidence."""
import csv
import json
import sys
import traceback
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research import trade_analytics as ta
from scripts import combination_lab_run as common, legacy_baseline_run as legacy, research_workflow as workflow

OUT = ROOT / 'outputs/research_mode/legacy_od29_recovery_v1'
STRATEGIES = ('hybrid_trend_rank', 'institutional_flow_confirmation', 'mean_reversion', 'quality_value_low_volatility')


def hypothesis(sid):
    return f'LEGACY-OD29-{sid.upper()}-V1'


def register_all():
    existing = {r['hypothesis_id'] for r in csv.DictReader(workflow.REGISTRY.open(encoding='utf-8-sig'))}
    for sid in STRATEGIES:
        hid = hypothesis(sid)
        if hid not in existing:
            workflow.register({'hypothesis_id': hid, 'strategy': sid, 'trial_budget': '1',
                               'hypothesis': 'Original rules-only baseline using approved OD-29 stale accounting marks',
                               'falsification_test': 'Comparison only, uncertified until data and OD-24 gates pass',
                               'data_window': f'{legacy.START}..{legacy.END}', 'source': legacy.RECOVERY_ROUND})


def _frame(rows, columns):
    return pd.DataFrame(rows, columns=columns) if not rows else pd.DataFrame(rows)


def v2_frames(raw, stress_trades):
    """Post-run evidence of evidence_version 2 (complete daily positions, stress trades, open positions, costs, events, PIT detail)."""
    base, tracks = raw['tracks']['base'], raw['tracks']
    return {'trades_stress': stress_trades,
            'positions_daily': _frame(base['positions_daily'], ['date', 'stock_id', 'shares', 'value']),
            'open_positions': _frame(base['open_positions'], ['stock_id', 'shares', 'entry_price', 'last_mark_value']),
            'costs': {name: track['cost_summary'] for name, track in tracks.items()},
            'valuation_events': _frame([{'track': name, **e} for name, track in tracks.items() for e in track['valuation_events']],
                                       ['track', 'date', 'stock_id', 'reason', 'price_source_date', 'stale_days']),
            'pit_detail': _frame(raw['pit_detail'], list(legacy.PIT_DETAIL_COLUMNS)),
            'health': {'engine_error': {name: track['engine_error'] for name, track in tracks.items()},
                       'valuation': {name: track['valuation'] for name, track in tracks.items()},
                       'unfillable_orders': raw['checks']['unfillable_orders'], 'lookahead_violations': raw['checks']['lookahead_violations'],
                       'pit_status': raw['checks']['pit_status']}}


def run_one(sid, levels, levels_note):
    if sid not in STRATEGIES:
        raise ValueError('STRATEGY_NOT_APPROVED')
    path = OUT / f'{sid}.json'
    if path.exists():
        evidence = json.loads(path.read_text(encoding='utf-8'))
        run_id = evidence.get('run_id') or evidence.get('failure_run_id')
        if not run_id or evidence.get('hypothesis_id') != hypothesis(sid) or not evidence.get('experiment_id'):
            raise ValueError('UNVERIFIED_RESULT_WITHOUT_RUN')
        from core.backtest import run_store
        saved = run_store.load_run(run_id)['run']
        if (saved.get('trial_id') != evidence['experiment_id'] or saved.get('status') != evidence['status']
                or saved.get('hypothesis_id') != evidence['hypothesis_id'] or saved.get('strategy_id') != sid):
            raise ValueError('RESULT_RUN_MISMATCH')
        result_file = workflow.results_path(workflow.REGISTRY)
        recorded = result_file.is_file() and any(r['hypothesis_id'] == evidence['hypothesis_id']
                                                 for r in csv.DictReader(result_file.open(encoding='utf-8-sig')))
        if not recorded:
            workflow.record_result(evidence['hypothesis_id'], evidence['status'], str(path), note='OD-29 recovery; not certified')
        return evidence
    hid, config = hypothesis(sid), legacy.config_for(sid) | {'round': legacy.RECOVERY_ROUND}
    # A pre-existing admission without a result may have been interrupted. Do not silently replay it.
    if any(row['hypothesis_id'] == hid for row in workflow._trial_rows(workflow.TRIAL_LEDGER)):
        raise RuntimeError(f'ADMITTED_RESULT_MISSING:{hid}')
    admission = workflow.admit_experiment(hid, config, legacy.code_paths() + [Path(__file__), Path(common.__file__)],
                                          legacy.input_paths(), legacy.END)
    evidence = {'hypothesis_id': hid, 'experiment_id': admission['experiment_id'], 'admission': admission['kind'],
                'config': config, 'input_sha256': admission['inputs_sha256'], 'code_sha256': admission['code_sha256']}
    # evidence_version 2: a copy of the ledger admission; run_store.evidence_status still verifies it against the ledger itself
    record_extra = {'evidence_version': 2, 'admission_record': {
        'admission_id': admission['experiment_id'], 'trial_id': admission['experiment_id'], 'hypothesis_id': hid,
        'code_sha256': admission['code_sha256'], 'inputs_sha256': admission['inputs_sha256'],
        'input_paths': {name: str(path) for name, path in legacy.input_paths().items()}, 'config': config, 'pinned': legacy.PINNED_INPUTS,
        'valuation_policy': config['valuation_policy'], 'research_window': [legacy.START, legacy.END], 'admitted_at': admission.get('at')}}
    item = {'item_id': f'OD29:{sid}', 'kind': 'legacy_baseline', 'strategy_id': sid}
    try:
        raw = legacy.run_legacy(sid)
        for track in raw['tracks'].values():
            if track['engine_error']:
                raise RuntimeError(track['engine_error'])
        navs = {name: pd.Series([v for _, v in track['nav']], index=pd.to_datetime([d for d, _ in track['nav']]))
                for name, track in raw['tracks'].items()}
        trades = {name: pd.DataFrame(track['trades']) for name, track in raw['tracks'].items()}
        for name in trades:
            if trades[name].empty:
                trades[name] = pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'buy_price', 'sell_price', 'shares', 'cost'])
        pnl = {}
        for trade in trades['stress'].to_dict('records'):
            pnl[trade['stock_id']] = pnl.get(trade['stock_id'], 0.0) + (trade['sell_price'] - trade['buy_price']) * trade['shares'] - trade['cost']
        returns = navs['stress'] / navs['stress'].shift(1).fillna(legacy.INITIAL_CAPITAL) - 1
        checks = {**raw['checks'], 'valuation': {name: {'events': track['valuation_events'], 'quality': track['valuation']}
                                                for name, track in raw['tracks'].items()}, 'input_digests': raw['input_digests']}
        evidence.update(status='SUCCESS', checks=checks, data_version=raw['data_version'],
                        metrics=ta.performance(navs['base'], initial_cash=legacy.INITIAL_CAPITAL, benchmark=levels, trades=trades['base']),
                        metrics_2x=ta.performance(navs['stress'], initial_cash=legacy.INITIAL_CAPITAL, benchmark=levels, trades=trades['stress']),
                        gates_comparison=common.comparison_gates(returns, pnl, levels),
                        _frames={'nav': common._nav_frame(navs['base'], navs['stress']), 'trades': trades['base'],
                                 'holdings': trades['base'][['stock_id', 'buy_date', 'sell_date']],
                                 'picks': pd.DataFrame(raw['picks'], columns=['date', 'stock_id', 'rank']), **v2_frames(raw, trades['stress'])},
                        _record_extra=record_extra)
        # the code this process ran, hashed now from the files (run_store.evidence_status binds it to the ledger code hashes)
        from core.backtest import run_store
        record_extra['source_hash'] = run_store.canonical_hash({path: workflow.sha256(path) for path in admission['code_sha256']})
        try:
            evidence['run_id'] = common.save(item, evidence, levels_note)
        except Exception as exc:
            raise RuntimeError(f'SAVE_FAILED: {type(exc).__name__}: {exc}') from exc
    except Exception as exc:
        evidence.update(status='FAILED', reason=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc()[-4000:])
        try:
            evidence['failure_run_id'] = common.save_failed({**evidence, 'item': item})
        except Exception as save_error:
            evidence['save_status'] = f'SAVE_FAILED: {type(save_error).__name__}: {save_error}'
    evidence.pop('_frames', None)
    OUT.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(common._clean(evidence), stream, ensure_ascii=False, indent=1, allow_nan=False)
    workflow.record_result(hid, evidence['status'], str(path), note='OD-29 recovery; not certified')
    return evidence


def main():
    common.require_research()
    register_all()
    levels, note = common.benchmark_levels()
    if levels is not None:
        note = 'TWSE MI_INDEX total-return index; BENCHMARK_SOURCE_NOT_CROSS_VERIFIED against MFI94U'
    for sid in STRATEGIES:
        print(sid, flush=True)
        result = run_one(sid, levels, note)
        print(result['status'], result.get('run_id') or result.get('failure_run_id'), flush=True)


if __name__ == '__main__':
    main()
