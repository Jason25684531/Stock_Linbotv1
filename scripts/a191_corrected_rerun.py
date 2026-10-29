"""A191_132 corrected-fill-rule rerun of the 12 archived Day3 configurations (hypothesis A191_132:CORRECTED-FILL-12CFG, Research mode). Stage A.

Same targets and parameters as the archived run, development window only (2023-02-13..2025-12-29), no new configuration, no OOS. The fill rule is
the shared one (core/backtest/execution through track_b_evaluate.audit_orders): an instruction is dropped when its order could not have filled
(no bar, no trade, or an open locked at the limit against the order direction), then the run repeats until no executed order is flagged.
VectorBT cannot partially fill, so the 5 %-of-traded-value cap is reported (over_5pct), not applied: every result is DIAGNOSTIC_ONLY, never CREDIBLE.
Costs: PROJECT_BASE (1x) and PROJECT_STRESS2X (2x).

Admission goes through research_workflow.admit_experiment with THIS stage's files only (no Owner grant; the same code, config and inputs again is a
REPLAY). Statistics are Stage B (scripts/a191_corrected_report.py): a fix there changes no trade and is not a new trial.
  python scripts/a191_corrected_rerun.py --output outputs/research_workflow/2026-10-28/a191_corrected
"""
import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as rw  # noqa: E402
from scripts import track_b_evaluate as tbe  # noqa: E402
from scripts.track_b_capture import orders_from_vbt, project_costs  # noqa: E402

HYPOTHESIS = 'A191_132:CORRECTED-FILL-12CFG'
WINDOW_END = '2025-12-29'
MAX_ROUNDS = 12  # mask rounds per configuration and cost level; a run still flagged after this is reported as such, never trusted
CODE = ('scripts/a191_corrected_rerun.py', 'scripts/track_b_evaluate.py', 'scripts/track_b_capture.py', 'core/backtest/execution.py',
        'core/research/vectorbt_adapter.py', 'core/research/strategy_search.py')
MANIFEST = ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv'
TRACKS = {1: 'PROJECT_BASE', 2: 'PROJECT_STRESS2X'}
CONFIG_KEYS = ('config_id', 'factor_set', 'factor_weighting', 'top_n', 'rebalance_days', 'portfolio_weighting', 'config_hash')


def build_inputs():
    """The archived Day3 pipeline up to target weights, for the 12 A191_132 configurations only."""
    from core.research import strategy_search as ss
    from jobs import run_strategy_search as job
    pool = pd.DataFrame(ss.preflight_accepted_pool(ROOT / job.DEFAULT_DAY2_DIR)['pool'])
    universe = ss.load_universe(ROOT / job.DEFAULT_D3_ROOT)
    panels = ss.compute_factor_panels(ss.load_market_frames(ROOT / job.DEFAULT_D3_ROOT), ROOT / job.DEFAULT_MASTER_PATH)
    ranks = ss.build_ranks_long(panels, ss.resolve_effective_directions(pool))
    grid, weights = ss.search_grid(pool)
    mine = grid[grid.factor_set == 'A191_132'].reset_index(drop=True)
    targets = ss.build_all_target_weights(mine, ss.build_all_composite_scores(ranks, weights), universe)
    close = ss.build_close_matrix(universe)
    assert len(mine) == 12 and close.index.max() <= pd.Timestamp(WINDOW_END) + pd.Timedelta(days=3), 'not the registered 12 configurations / window'
    return mine, targets, close


def run_masked(close, target, multiple, reference, events, config_id):
    """vectorbt with a fillable mask, repeated until no executed order is unfillable. Returns (result, orders, flags, rounds, dropped, converged)."""
    from core.research.vectorbt_adapter import run_vectorbt
    costs = project_costs(multiple)
    mask = pd.DataFrame(True, index=close.index, columns=close.columns)
    dropped = 0
    for rounds in range(1, MAX_ROUNDS + 1):
        result = run_vectorbt(close, target, initial_capital=1_000_000.0, sparse_rebalance=True, fillable=mask, **costs)
        orders = orders_from_vbt(result['orders'], costs['slippage']).assign(config_id=config_id)
        flags = tbe.audit_orders(orders, reference, events)
        bad = flags[flags.unfillable]
        if bad.empty:
            return result, orders, flags, rounds, dropped, True
        for r in bad.itertuples():
            mask.loc[r.date, r.asset] = False
        dropped += len(bad)
    return result, orders, flags, MAX_ROUNDS, dropped, False


def run(output: Path):
    t0 = time.time()
    output.mkdir(parents=True, exist_ok=True)
    grid, targets, close = build_inputs()
    inputs = {'archived_manifest': MANIFEST, 'official_bars': tbe.OUT / 'official_bars.parquet', 'official_events': tbe.OUT / 'official_events.csv',
              'targets': output / 'targets.parquet'}
    pd.concat(targets.values()).to_parquet(inputs['targets'])  # the rebuilt targets are an input of the run
    ledger = []
    for row in grid.itertuples():
        config = {k: (v.item() if hasattr(v, 'item') else v) for k, v in ((k, getattr(row, 'strategy_id' if k == 'config_id' else k)) for k in CONFIG_KEYS)}
        ledger.append(rw.admit_experiment(HYPOTHESIS, config, [ROOT / p for p in CODE], inputs, WINDOW_END))
    print('admitted', len(ledger), sorted({r['kind'] for r in ledger}), 'effective DSR trials', rw.effective_trials(), round(time.time() - t0), 's', flush=True)

    bars, events = tbe.load_shared()[:2]
    reference = tbe.official_reference(bars, events)
    returns, orders_all, audit = [], [], []
    for cid in grid.strategy_id:
        for multiple, track in TRACKS.items():
            result, orders, flags, rounds, dropped, converged = run_masked(close, targets[cid], multiple, reference, events, cid)
            returns.append(pd.DataFrame({'config_id': cid, 'track': track, 'date': result['returns'].index, 'ret': result['returns'].to_numpy()}))
            if multiple == 1:
                orders_all.append(orders)
            audit.append({'config_id': cid, 'track': track, 'rounds': rounds, 'converged': converged, 'instructions_dropped': dropped, 'orders': len(orders),
                          'unfillable_left': int(flags.unfillable.sum()), 'over_5pct_orders': int(flags.over_5pct.sum()),
                          'over_5pct_value_share': float(flags.loc[flags.over_5pct, 'notional'].sum() / flags.notional.sum()) if len(flags) else 0.0})
        print('config', cid, 'dropped', audit[-2]['instructions_dropped'], '/', audit[-1]['instructions_dropped'], round(time.time() - t0), 's', flush=True)
    pd.concat(returns, ignore_index=True).to_parquet(output / 'corrected_returns.parquet')
    pd.concat(orders_all, ignore_index=True).to_parquet(output / 'corrected_orders.parquet')
    pd.DataFrame(audit).to_csv(output / 'mask_audit.csv', index=False)
    (output / 'admission.json').write_text(json.dumps({'hypothesis': HYPOTHESIS, 'window_end': WINDOW_END, 'rows': ledger}, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print('stage A done', round(time.time() - t0), 's; converged', sum(a['converged'] for a in audit), 'of', len(audit))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
