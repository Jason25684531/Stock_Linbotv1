"""A191_132:H2-LIQUIDITY: incremental IC over amihud_20d, liquidity attribution and capacity from archived V6 ledgers (no backtest)."""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

RESEARCH = ROOT / 'outputs/research_mode'
OUT = RESEARCH / 'h2_a191_liquidity_v1'
RUNS = RESEARCH / 'day3_runs_v6'
GRID = ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv'
H2_ID, PRIMARY = 'A191_132:H2-LIQUIDITY', 'A191_132__EQUAL__TOP10__REB60__EQUAL'
TERCILES = ('T1_LIQUID', 'T2_MID', 'T3_ILLIQUID')


def inputs():
    from scripts.prepare_h1_signals import frames_of
    from scripts.research_validation_v2 import _prepared
    bars, _, sha = _prepared()
    universe = pd.DataFrame(json.loads((RESEARCH / 'prepared_v6/universe.json').read_text(encoding='utf-8')))
    membership = universe.pivot(index='asof_date', columns='asset_id', values='member').fillna(False).astype(bool)
    membership.index = pd.to_datetime(membership.index)
    return frames_of({'price_basis': 'RAW', 'bars': bars}), membership, sha


def factor_panels(frames, membership):
    """A191_132 exactly as V6 Day3 computes it, amihud_20d as V6 factor charts compute it, and the per-session residual of
    rank(A191_132) on rank(amihud_20d) over eligible members."""
    from core.research import strategy_search as search
    from core.research.d5_research_variant import raw_factor_panels
    from scripts.prepare_day3_signals import MASTER
    a191 = search.compute_factor_panels(frames, MASTER)['A191_132']
    amihud = raw_factor_panels(frames)['amihud_20d']
    eligible = membership.reindex(index=a191.index, columns=a191.columns).fillna(False).astype(bool)
    y, x = a191.where(eligible).rank(axis=1, pct=True), amihud.where(eligible).rank(axis=1, pct=True)
    both = y.notna() & x.notna()
    yc, xc = y.where(both).sub(y.where(both).mean(axis=1), axis=0), x.where(both).sub(x.where(both).mean(axis=1), axis=0)
    beta = (xc * yc).sum(axis=1) / (xc ** 2).sum(axis=1)
    return {'A191_132': a191, 'amihud_20d': amihud, 'A191_132_resid_amihud': yc - xc.mul(beta, axis=0)}, eligible


def ic(frames, membership, panels):
    """V6 factor_tables on raw closes: labels are close(t+h)/close(t)-1 and bars stop at 2025-12-31, so no label reaches 2026."""
    from scripts.research_mode_report import factor_tables
    tables, _ = factor_tables(frames, membership, panels)
    series = tables['ic_timeseries'].dropna(subset=['raw_ic'])
    sessions = list(frames['close'].index)
    summary, yearly = [], []
    for (factor, h), part in series.groupby(['factor_id', 'horizon']):
        values, last = part['raw_ic'], part['asof_date'].max()
        summary.append({'factor_id': factor, 'horizon': h, 'mean_ic': values.mean(), 'icir': values.mean() / values.std(ddof=1),
                        'positive_ic_ratio': (values > 0).mean(), 'days': len(values), 'last_signal_date': str(last.date()),
                        'last_label_end': str(sessions[sessions.index(last) + h].date())})
        for year, chunk in part.groupby(part['asof_date'].dt.year):
            yearly.append({'factor_id': factor, 'horizon': h, 'year': year, 'mean_ic': chunk['raw_ic'].mean(),
                           'positive_ic_ratio': (chunk['raw_ic'] > 0).mean(), 'days': len(chunk)})
    summary = pd.DataFrame(summary)
    if (summary['last_label_end'] > '2025-12-31').any():
        raise ValueError('OOS_LABEL_LEAK')
    return summary, pd.DataFrame(yearly), tables['factor_correlation']


def _signed(row):
    if row['kind'] == 'FILL':
        return -(row['notional'] + row['fee']) if row['side'] == 'buy' else row['notional'] - row['fee'] - row['tax']
    if row['kind'] == 'FORCED_EXIT':
        return row['notional'] - row['fee'] - row['tax']
    return row['amount'] if row['kind'] == 'CASH_CREDIT' else None


def stock_day_pnl(track):
    """Per stock and session: change in held value (positions, stock and cash receivables at that day's marks) plus that day's
    cash flows. Summed over the run it equals research_mode_report.asset_pnl."""
    flows = {}
    for row in track['result']['ledger']:
        amount = _signed(row)
        if amount is not None:
            day = flows.setdefault(row['date'], {})
            day[row['stock_id']] = day.get(row['stock_id'], 0.0) + amount
    rows, previous = [], {}
    for snap in track['result']['daily']:
        marks, value = snap['marks'], {}
        for stock, shares in snap['positions'].items():
            value[stock] = value.get(stock, 0.0) + shares * marks[stock]
        for claim in snap['stock_receivable']:
            value[claim['stock_id']] = value.get(claim['stock_id'], 0.0) + claim['shares'] * marks[claim['stock_id']]
        for claim in snap['cash_receivable_claims']:
            value[claim['stock_id']] = value.get(claim['stock_id'], 0.0) + claim['amount']
        today = flows.get(snap['date'], {})
        for stock in set(value) | set(previous) | set(today):
            rows.append((snap['date'], stock, value.get(stock, 0.0) - previous.get(stock, 0.0) + today.get(stock, 0.0), value.get(stock, 0.0)))
        previous = value
    frame = pd.DataFrame(rows, columns=['date', 'stock', 'pnl', 'value'])
    frame['date'] = pd.to_datetime(frame['date'])
    return frame


def attribution(track, tercile, adv, nav0):
    from scripts import track_b_stats as stats
    from scripts.research_mode_report import asset_pnl, returns_of
    pnl = stock_day_pnl(track)
    check = pnl.groupby('stock')['pnl'].sum()
    reference = pd.Series(asset_pnl(track))
    gap = float((check.reindex(reference.index).fillna(0) - reference).abs().max())
    if gap > 1e-6 * nav0:
        raise ValueError(f'PNL_RECONCILIATION_GAP:{gap}')
    pnl['tercile'] = [tercile.at[d, s] if d in tercile.index and s in tercile.columns else 'UNRANKED' for d, s in zip(pnl['date'], pnl['stock'])]
    fills = pd.DataFrame([r for r in track['result']['ledger'] if r['kind'] == 'FILL'])
    fills['date'] = pd.to_datetime(fills['date'])
    fills['tercile'] = [tercile.at[d, s] if d in tercile.index and s in tercile.columns else 'UNRANKED' for d, s in zip(fills['date'], fills['stock_id'])]
    fills['adv_ratio'] = fills['notional'] / [adv.at[d, s] if d in adv.index and s in adv.columns else np.nan
                                              for d, s in zip(fills['date'], fills['stock_id'])]
    per_pair = pnl.groupby(['tercile', 'stock'])['pnl'].sum()
    net_total = float(pnl['pnl'].sum())
    terciles = []
    for name in TERCILES + ('UNRANKED',):
        pairs = per_pair.loc[name] if name in per_pair.index.get_level_values(0) else pd.Series(dtype=float)
        held = pnl[(pnl['tercile'] == name) & (pnl['value'] > 0)]
        terciles.append({'tercile': name, 'held_stock_days': len(held), 'mean_held_value': float(held['value'].mean()) if len(held) else 0.0,
                         'traded_notional': float(fills.loc[fills['tercile'] == name, 'notional'].sum()),
                         'gross_profit': float(pairs[pairs > 0].sum()), 'gross_loss': float(pairs[pairs < 0].sum()),
                         'net_profit': float(pairs.sum()), 'net_share': float(pairs.sum()) / net_total if net_total > 0 else None})
    per_stock = check.sort_values(ascending=False)
    gross = float(per_stock[per_stock > 0].sum())
    stress_returns, stress_nav = returns_of(track, nav0)
    notional = fills['notional'].sum()
    weights = fills.groupby('stock_id')['notional'].sum() / notional
    ratio = fills['adv_ratio'].dropna()
    return terciles, {
        'net_profit': net_total, 'gross_profit': gross, 'gross_loss': float(per_stock[per_stock < 0].sum()),
        'top5_share_of_gross_profit': float(per_stock.head(5).sum()) / gross if gross > 0 else None,
        'top5_share_of_net_profit': float(per_stock.head(5).sum()) / net_total if net_total > 0 else None,
        'best5pct_days_return_share': stats.best_days_share(stress_returns.loc[stress_returns.index >= '2023-01-03']),
        'fills': len(fills), 'fills_without_adv': int(fills['adv_ratio'].isna().sum()),
        'adv_ratio_p50': float(ratio.quantile(.5)), 'adv_ratio_p90': float(ratio.quantile(.9)), 'adv_ratio_p99': float(ratio.quantile(.99)),
        **{f'notional_share_above_{int(x * 100)}pct_adv': float(fills.loc[fills['adv_ratio'] > x, 'notional'].sum() / notional) for x in (.01, .05, .10)},
        'notional_hhi': float((weights ** 2).sum()),
        'capital_multiple_for_p90_fill_at_5pct_adv': .05 / float(ratio.quantile(.9)) if len(ratio) else None,
        'pnl_reconciliation_max_gap': gap}


def run():
    from scripts.research_mode_report import returns_of
    from scripts import track_b_stats as stats
    workflow.require_registered(H2_ID)
    OUT.mkdir(parents=True, exist_ok=True)
    frames, membership, input_sha = inputs()
    panels, eligible = factor_panels(frames, membership)
    summary, yearly, correlation = ic(frames, membership, panels)
    summary.to_csv(OUT / 'ic_summary.csv', index=False)
    yearly.to_csv(OUT / 'ic_yearly.csv', index=False)
    correlation.to_csv(OUT / 'ic_factor_correlation.csv', index=False)
    rank = panels['amihud_20d'].where(eligible).rank(axis=1, pct=True)
    tercile = pd.DataFrame(np.select([rank <= 1 / 3, rank <= 2 / 3, rank > 2 / 3], TERCILES, default='UNRANKED'),
                           index=rank.index, columns=rank.columns)
    adv = frames['amount'].rolling(20, min_periods=20).mean().shift(1)  # prior 20-session mean official amount
    grid = list(csv.DictReader(GRID.open(encoding='utf-8-sig')))
    configs, tercile_rows, sources = [], [], {}
    for index, row in enumerate(grid):
        if row['factor_set'] != 'A191_132':
            continue
        folder = RUNS / f'{index:03d}'
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('status') != 'COMPUTABLE' or any(workflow.sha256(folder / n) != d for n, d in manifest['output_sha256'].items()):
            configs.append({'config_id': row['strategy_id'], 'status': manifest.get('status', 'UNVERIFIED')})
            continue
        sources[row['strategy_id']] = {n: d for n, d in manifest['output_sha256'].items()}
        tracks = {n: json.loads((folder / f'{n}.json').read_text(encoding='utf-8')) for n in ('base', 'stress')}
        nav0 = manifest['config']['initial_cash']
        terciles, values = attribution(tracks['stress'], tercile, adv, nav0)
        tercile_rows += [{'config_id': row['strategy_id'], **t} for t in terciles]
        sensitivity = {}
        for name, track in tracks.items():
            returns, nav = returns_of(track, nav0)
            returns = returns.loc[returns.index >= '2023-01-03']
            sensitivity[name] = {'total_return': float(nav.iloc[-1] / nav0 - 1), 'sharpe': stats.sharpe(returns)}
        configs.append({'config_id': row['strategy_id'], 'status': 'COMPUTABLE', **values,
                        'total_return_1x': sensitivity['base']['total_return'], 'total_return_2x': sensitivity['stress']['total_return'],
                        'total_return_3x_linear': 2 * sensitivity['stress']['total_return'] - sensitivity['base']['total_return'],
                        'sharpe_1x': sensitivity['base']['sharpe'], 'sharpe_2x': sensitivity['stress']['sharpe'],
                        'sharpe_3x_linear': 2 * sensitivity['stress']['sharpe'] - sensitivity['base']['sharpe']})
    pd.DataFrame(tercile_rows).to_csv(OUT / 'attribution_terciles.csv', index=False)
    pd.DataFrame(configs).to_csv(OUT / 'attribution_capacity_configs.csv', index=False)
    resid = summary[summary['factor_id'] == 'A191_132_resid_amihud'].set_index('horizon')
    incremental = all(resid.at[h, 'mean_ic'] > 0 and resid.at[h, 'positive_ic_ratio'] > .5 for h in (20, 60))
    primary = next(c for c in configs if c['config_id'] == PRIMARY)
    illiquid = next(t for t in tercile_rows if t['config_id'] == PRIMARY and t['tercile'] == 'T3_ILLIQUID')
    over_reliant = (illiquid['net_share'] is None or illiquid['net_share'] >= .5 or primary['notional_share_above_10pct_adv'] >= .2)
    verdict = 'STOP' if not incremental else ('REVISE' if over_reliant else 'CONTINUE')
    result = {'hypothesis_id': H2_ID, 'label': 'DIAGNOSTIC_ONLY', 'verdict': verdict, 'v7_erratum_verdict': 'CONTINUE (unchanged)',
              'incremental': incremental, 'over_reliant': over_reliant, 'primary_config': PRIMARY,
              'primary_lowest_liquidity_net_share': illiquid['net_share'],
              'primary_notional_share_above_10pct_adv': primary['notional_share_above_10pct_adv'],
              'residual_ic': resid[['mean_ic', 'positive_ic_ratio', 'days', 'last_label_end']].to_dict('index'),
              'inputs_sha256': {**input_sha, 'grid': workflow.sha256(GRID)}, 'run_outputs_sha256': sources,
              'code_sha256': {p: workflow.sha256(ROOT / p) for p in ('scripts/research_h2_a191_liquidity.py', 'scripts/research_mode_report.py',
                                                                       'core/research/strategy_search.py', 'core/research/d5_research_variant.py')},
              'research_ledger_rows': len(workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER))}
    (OUT / 'h2_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1, default=float) + '\n', encoding='utf-8')
    workflow.record_result(H2_ID, verdict, str((OUT / 'h2_result.json').relative_to(ROOT).as_posix()),
                           'incremental=%s over_reliant=%s' % (incremental, over_reliant))
    return result


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, indent=1, default=float))
