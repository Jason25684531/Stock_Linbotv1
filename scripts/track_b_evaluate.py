"""Execution audit, holdings exposure, statistics and labels for the Track B reruns (OD-23, OD-24, OD-26, OD-27).

The audit only flags: it never changes an engine, an order or a result, and it is not a fill simulation.
Return basis: D3 prices are official-event back-adjusted, so the benchmark is the TAIEX total-return index (like for like).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import track_b_stats as st
from scripts.audit_strategy_history import read, write
from scripts.track_b_data import load_events
from scripts.track_b_data import adjustment_factor
from scripts.track_b_validation import FAMILY_SIZE, OUT

CAPS = (0.01, 0.02, 0.05)  # share of the stock's traded value on the fill date (OD-23); 5 % is the main threshold
UNFILLABLE_MATERIAL = 0.01  # unfillable orders above 1 % of all orders: material (operational definition, reported)
OVER_CAP_MATERIAL = 0.05  # value above the 5 % cap above 5 % of all traded value: material
PRICE_TOLERANCE = 1e-6
NATIVE_GROSS = {'DAY3': 'gross', 'G2G3': 'GROSS', 'D5': None}
STRESS_TRACK, BASE_TRACK = 'PROJECT_STRESS2X', 'PROJECT_BASE'


from core.backtest.execution import limit_prices, tick  # noqa: E402,F401  (single source; re-exported for existing callers)


def official_reference(bars, events):
    """Previous official close of each stock, replaced by the official ex-rights reference price on an event date."""
    bars = bars.sort_values(['stock_id', 'trade_date']).copy()
    bars['reference'] = bars.groupby('stock_id').close.transform(lambda x: x.ffill().shift(1))
    event = events.drop_duplicates(['stock_id', 'ex_date']).rename(columns={'ex_date': 'trade_date', 'ex_reference_price': 'event_reference'})
    bars = bars.merge(event[['stock_id', 'trade_date', 'event_reference']], on=['stock_id', 'trade_date'], how='left')
    bars['reference'] = bars.event_reference.fillna(bars.reference)
    bars['limit_up'], bars['limit_down'] = limit_prices(bars.reference.to_numpy(dtype=float))
    return bars.drop(columns='event_reference')


def audit_orders(orders, bars, events):
    """One row per order with every flag; `unfillable` = no bar, no trade, or a locked open against the order's direction."""
    merged = orders.merge(bars[['trade_date', 'stock_id', 'open', 'high', 'low', 'volume', 'amount', 'limit_up', 'limit_down']],
                          left_on=['date', 'asset'], right_on=['trade_date', 'stock_id'], how='left')
    no_bar = merged.open.isna() & merged.volume.isna()
    no_trade = ~no_bar & (merged.open.isna() | ~(merged.volume > 0))
    locked = (merged.high == merged.low) & (merged.open == merged.high)
    lock_buy = (merged.side == 'buy') & locked & (merged.open == merged.limit_up)
    lock_sell = (merged.side == 'sell') & locked & (merged.open == merged.limit_down)
    expected = merged.open * adjustment_factor(events[events.range.eq('D3')], merged.asset, merged.date)
    price_mismatch = ((merged.raw_price - expected).abs() / expected.abs() > PRICE_TOLERANCE) & expected.notna()
    share = merged.notional / merged.amount.where(merged.amount > 0)
    flags = pd.DataFrame({'config_id': merged.config_id, 'asset': merged.asset, 'date': merged.date, 'side': merged.side, 'notional': merged.notional, 'no_bar': no_bar,
                          'no_trade': no_trade, 'lock_buy': lock_buy, 'lock_sell': lock_sell, 'price_mismatch': price_mismatch, 'share_of_traded_value': share})
    flags['unfillable'] = flags.no_bar | flags.no_trade | flags.lock_buy | flags.lock_sell
    for cap in CAPS:
        flags[f'over_{int(cap * 100)}pct'] = flags.share_of_traded_value > cap
    return flags


def config_audit(flags):
    rows = []
    for config_id, g in flags.groupby('config_id'):
        total, value = len(g), g.notional.sum()
        row = {'config_id': config_id, 'orders': total, 'order_value': float(value), 'unfillable_orders': int(g.unfillable.sum()), 'no_bar': int(g.no_bar.sum()),
               'no_trade': int(g.no_trade.sum()), 'locked_buy': int(g.lock_buy.sum()), 'locked_sell': int(g.lock_sell.sum()), 'price_mismatch': int(g.price_mismatch.sum()),
               'share_unknown': int(g.share_of_traded_value.isna().sum())}
        for cap in CAPS:
            name = f'over_{int(cap * 100)}pct'
            row[f'{name}_orders'], row[f'{name}_value'] = int(g[name].sum()), float(g.loc[g[name], 'notional'].sum())
            row[f'{name}_value_share'] = float(row[f'{name}_value'] / value) if value else 0.0
        row['execution_audit'] = ('FLAGGED_MATERIAL' if total and (row['unfillable_orders'] / total > UNFILLABLE_MATERIAL or row['over_5pct_value_share'] > OVER_CAP_MATERIAL)
                                  else 'PASS_FLAG_ONLY')
        rows.append(row)
    return pd.DataFrame(rows)


class Holdings:
    """Shares held per asset over time, rebuilt from one configuration's orders (orders on a date happen at that date's open)."""

    def __init__(self, orders):
        signed = orders.assign(delta=np.where(orders.side == 'buy', orders.shares, -orders.shares)).sort_values(['asset', 'date'], kind='stable')
        signed['held'] = signed.groupby('asset').delta.cumsum()
        self.by_asset = {a: (g.date.to_numpy(), g.held.to_numpy()) for a, g in signed.groupby('asset')}

    def shares(self, asset, date, strictly_before=False):
        if asset not in self.by_asset:
            return 0.0
        dates, held = self.by_asset[asset]
        i = np.searchsorted(dates, np.datetime64(date), side='left' if strictly_before else 'right')
        return float(held[i - 1]) if i else 0.0


def exposures(orders, events, delist, nonofficial, window):
    """Held-through counts: mixed rights/dividend events, delistings, and untradable filled valuation prices."""
    held = Holdings(orders)
    inside = events[(events.ex_date >= window[0]) & (events.ex_date <= window[1]) & events.stock_id.isin(held.by_asset)]
    mixed = [(r.stock_id, r.ex_date) for r in inside.itertuples() if r.action_type in ('權', '權息') and held.shares(r.stock_id, r.ex_date, strictly_before=True) > 0]
    pure = sum(held.shares(r.stock_id, r.ex_date, strictly_before=True) > 0 for r in inside.itertuples() if r.action_type == '息')
    delisted = [(r.stock_id, r.last_traded) for r in delist.itertuples() if r.stock_id in held.by_asset and window[0] <= r.last_traded <= window[1]
                and held.shares(r.stock_id, r.last_traded) > 0]
    filled = [(r.asset_id, r.execution_date) for r in nonofficial.itertuples() if r.asset_id in held.by_asset and window[0] <= r.execution_date <= window[1]
              and held.shares(r.asset_id, r.execution_date) > 0]
    return {'held_through_mixed_events': len(mixed), 'held_through_pure_dividends': int(pure), 'held_delisted_at_last_trade': len(delisted),
            'delisted_assets': sorted({a for a, _ in delisted}), 'held_on_nonofficial_prices': len(filled),
            'total_return_status': 'TOTAL_RETURN_BLOCKED' if mixed else 'TOTAL_RETURN_PURE_CASH_ONLY',
            'delisting_status': 'DELISTING_EXIT_UNVERIFIED' if delisted else 'NO_DELISTED_HOLDING'}


def asset_pnl(orders, last_prices):
    """Cash flows of the orders plus the final holding at the last adjusted price (fees ignored: this is for concentration only)."""
    sign = np.where(orders.side == 'buy', -1.0, 1.0)
    pnl = pd.Series(sign * orders.notional.to_numpy(), index=orders.asset.to_numpy()).groupby(level=0).sum()
    held = Holdings(orders)
    final = pd.Series({a: held.by_asset[a][1][-1] * last_prices.get(a, np.nan) for a in held.by_asset})
    return pnl.add(final.fillna(0.0), fill_value=0.0)


def metrics(returns, benchmark, trades, orders_notional, gross=None):
    r = returns.dropna()
    b = benchmark.reindex(r.index)
    excess = (r - b).dropna()
    equity = 1_000_000.0 * (1 + r).cumprod()
    years = len(r) / st.PERIODS
    return {'sessions': len(r), 'start': str(r.index.min().date()), 'end': str(r.index.max().date()), 'total_return': float((1 + r).prod() - 1), 'cagr': st.cagr(r),
            'annualized_volatility': float(r.std(ddof=1) * np.sqrt(st.PERIODS)), 'sharpe_net': st.sharpe(r), 'max_drawdown': st.max_drawdown(r),
            'daily_win_rate': float((r > 0).mean()), 'trade_count': int(trades), 'annual_turnover': float(orders_notional / equity.mean() / years) if years else np.nan,
            'gross_total_return': None if gross is None else float((1 + gross.dropna()).prod() - 1),
            'benchmark_total_return': float((1 + b.dropna()).prod() - 1), 'excess_total_return': float((1 + r).prod() - (1 + b.dropna()).prod()),
            'excess_sharpe': st.sharpe(excess), 'annualized_excess': float(excess.mean() * st.PERIODS)}, excess


# ---- trial counts for the deflated Sharpe: every configuration of this study plus the searches that preceded it (a lower bound) ----
PRIOR_SEARCHES = {'strategy_configurations_384': 384, 'factor_inventory_candidates': 335, 'fundamental_factor_scoreboard': 12, 'd4_factor_acceptance_scoreboard': 12}
N_TRIALS_POOLED = sum(PRIOR_SEARCHES.values())
FAMILY_BOUNDARY = {'DAY3': '2025-12-31', 'G2G3': '2026-07-28', 'D5': '2026-07-28'}  # last session a development run may contain
SPEC = {'DAY3': 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv'}


def family_table(family):
    """config_id -> (family name, top_n, rebalance_days, portfolio_weighting, factor_set) from the rerun's own grid file."""
    run = OUT / 'track_b_runs' / family.lower()
    if family == 'DAY3':
        g = pd.read_csv(run / 'strategy_search_manifest.csv')
        return g.assign(config_id=g.strategy_id, family_name=g.factor_set, portfolio=g.portfolio_weighting.str.lower())[['config_id', 'family_name', 'top_n', 'rebalance_days', 'portfolio', 'factor_set']]
    if family == 'G2G3':
        g = pd.read_csv(run / 'strategy_grid.csv')
        names = {'G2': 'G2_OPERATING_INCOME_YOY', 'G3': 'G3_EPS_YOY', 'G2_PLUS_G3': 'G2_OPERATING_INCOME_YOY+G3_EPS_YOY'}
        return g.assign(config_id=g.strategy_id, family_name=g.factor_set_id.map(names), portfolio=g.portfolio_weighting.str.lower(), factor_set=g.factor_set_id)[
            ['config_id', 'family_name', 'top_n', 'rebalance_days', 'portfolio', 'factor_set']]
    g = pd.read_csv(run / 'parameter_grid.csv')
    return g.assign(family_name='D5_PORTFOLIO_5_FACTORS', portfolio=g.stock_weighting, factor_set=g.combination_method)[['config_id', 'family_name', 'top_n', 'rebalance_days', 'portfolio', 'factor_set']]


def load_shared():
    bars = pd.read_parquet(OUT / 'official_bars.parquet')
    events = load_events()
    bench = pd.read_csv(OUT / 'benchmarks.csv', parse_dates=['date']).set_index('date').sort_index()
    delist = pd.read_csv(OUT / 'delistings.csv', parse_dates=['delisting_date', 'first_traded', 'last_traded'])
    delist = delist[delist.last_traded.notna()].assign(stock_id=lambda d: d.stock_id.astype(str))
    nonofficial = pd.read_csv(OUT / 'd3_nonofficial_prices.csv', parse_dates=['execution_date']).assign(asset_id=lambda d: d.asset_id.astype(str))
    return bars, events, bench, delist, nonofficial


def evaluate_family(family, shared):
    bars, events, bench, delist, nonofficial = shared
    returns = pd.read_parquet(OUT / f'returns_{family.lower()}.parquet')
    orders = pd.read_parquet(OUT / f'orders_{family.lower()}.parquet')
    table = family_table(family).set_index('config_id')
    flags = audit_orders(orders, official_reference(bars, events), events)
    audit = config_audit(flags).set_index('config_id')
    bench_return = bench.taiex_total_return.pct_change()
    regime = st.regimes(bench.taiex_price)
    close = bars.pivot(index='trade_date', columns='stock_id', values='close').sort_index().ffill()
    rows, series = [], {}
    for config_id, g in returns.groupby('config_id'):
        track = {name: t.set_index('date').ret for name, t in g.groupby('track')}
        base, stress = track[BASE_TRACK], track[STRESS_TRACK]
        cfg_orders = orders[orders.config_id == config_id]
        end = base.index.max()
        last = close.loc[:end].iloc[-1]
        factor = pd.Series(adjustment_factor(events[events.range.eq('D3')], last.index.astype(str), [end] * len(last)), index=last.index)
        gross = track.get(NATIVE_GROSS[family]) if NATIVE_GROSS[family] else None
        m, excess = metrics(base, bench_return, len(cfg_orders), cfg_orders.notional.sum(), gross)
        _, excess2 = metrics(stress, bench_return, len(cfg_orders), cfg_orders.notional.sum())
        exposure = exposures(cfg_orders, events, delist, nonofficial, (base.index.min(), end))
        pnl = asset_pnl(cfg_orders, (last * factor).dropna().to_dict())
        series[config_id] = excess
        rows.append({'family': family, 'config_id': config_id, **m, 'sharpe_net_2x_cost': st.sharpe(stress), 'excess_sharpe_2x_cost': st.sharpe(excess2),
                     'total_return_2x_cost': float((1 + stress).prod() - 1), 'top5_assets_profit_share': st.top_assets_share(pnl), 'best5pct_days_return_share': st.best_days_share(base),
                     'regime': st.regime_excess(excess, regime), **exposure, **audit.loc[config_id].drop('orders').to_dict(),
                     'family_name': table.loc[config_id, 'family_name'], 'beyond_boundary': bool(end > pd.Timestamp(FAMILY_BOUNDARY[family]))})
    return pd.DataFrame(rows), series


def paired_comparisons(results, series):
    """A multi-factor configuration against the best matching single-factor configuration (same Top N, rebalance days, portfolio weighting)."""
    rows = []
    for family in ('DAY3', 'G2G3'):
        table = family_table(family)
        table['is_single'] = ~table.factor_set.str.contains(r'\+|G2_PLUS_G3')
        score = results.set_index('config_id').excess_sharpe
        for combo in table[~table.is_single].itertuples():
            factors = combo.factor_set.split('+') if '+' in combo.factor_set else ['G2', 'G3']
            matching = table[table.is_single & table.top_n.eq(combo.top_n) & table.rebalance_days.eq(combo.rebalance_days) & table.portfolio.eq(combo.portfolio)
                             & table.factor_set.isin(factors)]
            if matching.empty or combo.config_id not in series:
                continue
            best = matching.assign(s=matching.config_id.map(score)).sort_values('s', ascending=False).iloc[0]
            a, b = series[combo.config_id], series[best.config_id]
            idx = a.index.intersection(b.index)
            ci = st.paired_block_bootstrap_sharpe_difference(a.loc[idx], b.loc[idx])
            verdict = 'COMBINATION_BETTER' if ci['lower'] > 0 else ('COMBINATION_WORSE' if ci['upper'] < 0 else 'CANNOT_BE_DISTINGUISHED')
            rows.append({'family': family, 'family_name': combo.family_name, 'combination': combo.config_id, 'best_single': best.config_id, 'sharpe_difference': ci['difference'],
                         'ci_lower': ci['lower'], 'ci_upper': ci['upper'], 'verdict': verdict})
    return pd.DataFrame(rows)


def apply_statistics(results, series):
    """Bootstrap interval, deflated Sharpe with the pooled trial count, the five gates and the labels."""
    daily = pd.Series({k: v.mean() / v.std(ddof=1) for k, v in series.items() if v.std(ddof=1) > 1e-12})
    variance = float(daily.var(ddof=1))
    out = []
    for r in results.itertuples():
        x = series[r.config_id]
        ci = st.block_bootstrap_sharpe(x)
        dsr = st.deflated_sharpe(x, N_TRIALS_POOLED, variance)
        regimes = r.regime
        statuses = [regimes['bull']['status'], regimes['bear']['status']]
        g5 = 'UNDETERMINED' if any(s.startswith('UNDETERMINED') for s in statuses) else ('FAIL' if statuses == ['NEGATIVE', 'NEGATIVE'] else 'PASS')
        gate = {'g1_net_sharpe_positive_at_2x_cost': bool(r.sharpe_net_2x_cost > 0 and r.excess_sharpe_2x_cost > 0), 'g2_bootstrap_lower_bound_positive': bool(ci['lower'] > 0),
                'g3_deflated_sharpe_above_0_95': bool(dsr > 0.95), 'g4_concentration_below_50pct': bool(r.top5_assets_profit_share is not None and r.best5pct_days_return_share is not None
                                                                                                         and r.top5_assets_profit_share < 0.5 and r.best5pct_days_return_share < 0.5),
                'g5_regimes': g5}
        statistical_pass = all(v for k, v in gate.items() if k != 'g5_regimes') and g5 == 'PASS'
        data_ok = r.family != 'G2G3'  # fundamentals are only sample-audited, so G2/G3 cannot be DATA_PASS
        clean = (r.execution_audit == 'PASS_FLAG_ONLY' and r.held_on_nonofficial_prices == 0 and r.delisting_status == 'NO_DELISTED_HOLDING' and not r.beyond_boundary)
        label = 'DEVELOPMENT' if data_ok and clean else 'DIAGNOSTIC'
        candidate = label == 'DEVELOPMENT' and statistical_pass and r.total_return_status != 'TOTAL_RETURN_BLOCKED'
        blockers = [name for name, ok in (('fundamentals_sample_only', data_ok), ('execution_audit_material', r.execution_audit == 'PASS_FLAG_ONLY'),
                                          ('nonofficial_valuation_price_held', r.held_on_nonofficial_prices == 0), ('DELISTING_EXIT_UNVERIFIED', r.delisting_status == 'NO_DELISTED_HOLDING'),
                                          ('TOTAL_RETURN_BLOCKED', r.total_return_status != 'TOTAL_RETURN_BLOCKED'), ('beyond_family_boundary', not r.beyond_boundary),
                                          ('statistical_gates', statistical_pass)) if not ok]
        out.append({**gate, 'bootstrap_lower': ci['lower'], 'bootstrap_upper': ci['upper'], 'deflated_sharpe': dsr, 'statistical_gates_pass': statistical_pass, 'BACKTEST': label,
                    'EVIDENCE': 'CANDIDATE_FOR_INDEPENDENT_VALIDATION' if candidate else 'INSUFFICIENT', 'blockers': ';'.join(blockers)})
    return pd.concat([results.reset_index(drop=True), pd.DataFrame(out)], axis=1), variance


def evaluate_all():
    shared = load_shared()
    parts, series = [], {}
    for family in FAMILY_SIZE:
        if not (OUT / f'returns_{family.lower()}.parquet').exists():
            print(family, 'not rerun yet; skipped')
            continue
        frame, s = evaluate_family(family, shared)
        parts.append(frame)
        series.update(s)
        print(family, len(frame), 'configurations evaluated')
    results, variance = apply_statistics(pd.concat(parts, ignore_index=True), series)
    results.assign(regime=results.regime.map(json.dumps)).to_csv(OUT / 'backtest_results.csv', index=False, encoding='utf-8-sig')
    comparisons = paired_comparisons(results, series)
    comparisons.to_csv(OUT / 'factor_combination_comparison.csv', index=False, encoding='utf-8-sig')
    write(OUT / 'statistical_evidence.json', {
        'evidence_series': 'daily net excess return over the TAIEX total-return index; strategy returns are official-event adjusted prices after project costs',
        'costs': {'base': 'fee 0.1425 %, tax 0.3 %, slippage 0.1 % (config/settings.py)', 'stress': 'twice each'}, 'benchmark': 'TAIEX total-return index (MI_INDEX 發行量加權股價報酬指數)',
        'bootstrap': st.BOOTSTRAP, 'sharpe': 'annualized with 252 sessions, no risk-free rate', 'deflated_sharpe': {
            'reference': 'Bailey and Lopez de Prado', 'trial_count': N_TRIALS_POOLED, 'trial_sources': PRIOR_SEARCHES,
            'trial_count_note': 'known searches only: a lower bound, so the deflation is if anything too small', 'trial_sharpe_variance_daily': variance,
            'variance_from': f'{len(series)} configurations of this study'},
        'regimes': {'definition': 'previous-session TAIEX price index above its 200-session average = bull', 'minimum_sessions': st.MIN_REGIME_SESSIONS},
        'gates': ['g1 net and excess Sharpe > 0 at 2x costs', 'g2 bootstrap lower bound > 0', 'g3 DSR > 0.95', 'g4 top-5 assets and best-5%-days profit share < 50 % (literal reading)',
                  'g5 bull and bear excess not both negative and each regime has >= 60 sessions'],
        'execution_audit': {'caps': list(CAPS), 'material': f'unfillable orders > {UNFILLABLE_MATERIAL:.0%} of orders or value above the 5 % cap > {OVER_CAP_MATERIAL:.0%} of order value',
                            'scope': 'flags only; no partial or unfilled fills were simulated, so strict executability is NOT verified'},
        'counts': {'configurations': len(results), 'BACKTEST': results.BACKTEST.value_counts().to_dict(), 'EVIDENCE': results.EVIDENCE.value_counts().to_dict()}})
    print(results.groupby(['family', 'BACKTEST', 'EVIDENCE']).size().to_dict())


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    argparse.ArgumentParser(description=__doc__).parse_args()
    evaluate_all()
