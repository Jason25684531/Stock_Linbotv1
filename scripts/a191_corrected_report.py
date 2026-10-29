"""A191_132 corrected-fill-rule rerun, Stage B: OLD versus CORRECTED for the 12 archived configurations with the unchanged OD-24 gates g1..g5.

Reads the Stage A outputs (scripts/a191_corrected_rerun.py) and the archived Track B returns and orders; computes no new trade. Statistics are the
existing track_b_evaluate.evaluate_family / apply_statistics with the pooled 743 DSR trials and the Sharpe variance of the full archived
300-configuration family (the 12 series swapped in), exactly as the archived evaluation did. Every result is DIAGNOSTIC_ONLY; nothing is CREDIBLE.
  python scripts/a191_corrected_report.py --stage-a outputs/research_workflow/2026-10-28/a191_corrected
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import track_b_evaluate as tbe  # noqa: E402

MANIFEST = ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv'
KEEP = ['sessions', 'total_return', 'cagr', 'sharpe_net', 'sharpe_net_2x_cost', 'excess_sharpe', 'excess_sharpe_2x_cost', 'max_drawdown', 'trade_count', 'annual_turnover',
        'top5_assets_profit_share', 'best5pct_days_return_share', 'g1_net_sharpe_positive_at_2x_cost', 'g2_bootstrap_lower_bound_positive', 'g3_deflated_sharpe_above_0_95',
        'g4_concentration_below_50pct', 'g5_regimes', 'bootstrap_lower', 'deflated_sharpe', 'statistical_gates_pass', 'total_return_status', 'execution_audit',
        'unfillable_orders', 'over_5pct_value_share', 'BACKTEST', 'EVIDENCE', 'blockers']


def evaluate(returns, orders, shared, tmp, other):
    """tbe.evaluate_family on this returns/orders pair, then the gates with the family-wide Sharpe variance (the 288 other archived series stay)."""
    (tmp / 'track_b_runs/day3').mkdir(parents=True, exist_ok=True)
    pd.read_csv(MANIFEST).to_csv(tmp / 'track_b_runs/day3/strategy_search_manifest.csv', index=False)
    returns.to_parquet(tmp / 'returns_day3.parquet')
    orders.to_parquet(tmp / 'orders_day3.parquet')
    original = tbe.OUT
    tbe.OUT = tmp
    try:
        table, series = tbe.evaluate_family('DAY3', shared)
    finally:
        tbe.OUT = original
    table, variance = tbe.apply_statistics(table, {**other, **series})
    return table.set_index('config_id')[KEEP], variance


def main(stage_a: Path):
    shared = tbe.load_shared()
    bench_return = shared[2].taiex_total_return.pct_change()
    old_returns, old_orders = pd.read_parquet(tbe.OUT / 'returns_day3.parquet'), pd.read_parquet(tbe.OUT / 'orders_day3.parquet')
    new_returns, new_orders = pd.read_parquet(stage_a / 'corrected_returns.parquet'), pd.read_parquet(stage_a / 'corrected_orders.parquet')
    ids = sorted(new_returns.config_id.unique())
    base = old_returns[old_returns.track == 'PROJECT_BASE']
    other = {}
    for cid, g in base[~base.config_id.isin(ids)].groupby('config_id'):
        r = g.set_index('date').ret
        other[cid] = (r - bench_return.reindex(r.index)).dropna()
    work = stage_a / '_eval'
    old_table, old_var = evaluate(old_returns[old_returns.config_id.isin(ids)], old_orders[old_orders.config_id.isin(ids)], shared, work / 'old', other)
    new_table, new_var = evaluate(new_returns, new_orders, shared, work / 'new', other)
    audit = pd.read_csv(stage_a / 'mask_audit.csv')
    base_audit = audit[audit.track == 'PROJECT_BASE'].set_index('config_id')[['rounds', 'converged', 'instructions_dropped', 'unfillable_left', 'over_5pct_orders']]
    compare = old_table.add_prefix('OLD_').join(new_table.add_prefix('CORRECTED_')).join(base_audit)
    compare['label'] = 'DIAGNOSTIC_ONLY'
    compare.to_csv(stage_a / 'old_vs_corrected.csv', encoding='utf-8-sig')
    gates = ['g1_net_sharpe_positive_at_2x_cost', 'g2_bootstrap_lower_bound_positive', 'g3_deflated_sharpe_above_0_95', 'g4_concentration_below_50pct']
    summary = {
        'label': 'DIAGNOSTIC_ONLY', 'credible': False, 'configurations': len(ids), 'window': ['2023-02-13', '2025-12-29'], 'trials_in_dsr': tbe.N_TRIALS_POOLED,
        'sharpe_variance_family_old': old_var, 'sharpe_variance_family_corrected': new_var,
        'configs_passing_g1_g5_old': int(old_table.statistical_gates_pass.sum()), 'configs_passing_g1_g5_corrected': int(new_table.statistical_gates_pass.sum()),
        'per_gate_pass_old': {g: int(old_table[g].sum()) for g in gates} | {'g5_regimes_PASS': int((old_table.g5_regimes == 'PASS').sum())},
        'per_gate_pass_corrected': {g: int(new_table[g].sum()) for g in gates} | {'g5_regimes_PASS': int((new_table.g5_regimes == 'PASS').sum())},
        'median_net_sharpe': {'old': float(old_table.sharpe_net.median()), 'corrected': float(new_table.sharpe_net.median())},
        'median_net_sharpe_2x_cost': {'old': float(old_table.sharpe_net_2x_cost.median()), 'corrected': float(new_table.sharpe_net_2x_cost.median())},
        'median_total_return': {'old': float(old_table.total_return.median()), 'corrected': float(new_table.total_return.median())},
        'median_max_drawdown': {'old': float(old_table.max_drawdown.median()), 'corrected': float(new_table.max_drawdown.median())},
        'median_deflated_sharpe': {'old': float(old_table.deflated_sharpe.median()), 'corrected': float(new_table.deflated_sharpe.median())},
        'instructions_dropped_total': int(base_audit.instructions_dropped.sum()), 'all_converged': bool(base_audit.converged.all() and audit.converged.all()),
        'unfillable_left_total': int(base_audit.unfillable_left.sum()), 'total_return_status': sorted(set(new_table.total_return_status)),
        'blockers_corrected': sorted({b for s in new_table.blockers for b in str(s).split(';') if b})}
    summary['outcome'] = 'REJECTED' if summary['configs_passing_g1_g5_corrected'] == 0 else 'NOT_REJECTED_DIAGNOSTIC_ONLY'
    (stage_a / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    digest = hashlib.sha256(compare.drop(columns=['OLD_blockers', 'CORRECTED_blockers']).round(12).to_csv().encode('utf-8')).hexdigest()
    (stage_a / 'result_sha256.txt').write_text(digest + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    print('result_sha256', digest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage-a', type=Path, required=True)
    main(parser.parse_args().stage_a)
