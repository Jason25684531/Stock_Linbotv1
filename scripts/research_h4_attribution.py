"""H4:GROWTH-MOMENTUM-V2 attribution and reliability diagnostics from archived ledgers (no backtest, no new trial, no 2026 data)."""
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

RM = ROOT / 'outputs/research_mode'
DOCS = ROOT / 'docs/research/2026-11-04-h4-attribution-reliability-v1'
RUNS = {'H4': (RM / 'h4_runs_v1/002', 'H4_GROWTH_MOMENTUM__TOP10__REB60__EQUAL'),
        'H1': (RM / 'h1_runs_v1/002', 'near_high_252d__EQUAL__TOP10__REB60__EQUAL'),
        'A191_132': (RM / 'day3_runs_v6/134', 'A191_132__EQUAL__TOP10__REB60__EQUAL')}
NAV0 = 1_000_000
CAPTION = '2x 成本軌｜RESEARCH_UNCERTIFIED／DIAGNOSTIC_ONLY'


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def tracks(name):
    folder, config = RUNS[name]
    manifest = load(folder / 'manifest.json')
    if manifest['config']['config_id'] != config or manifest['status'] != 'COMPUTABLE':
        raise ValueError(f'UNEXPECTED_RUN:{name}')
    if any(workflow.sha256(folder / n) != d for n, d in manifest['output_sha256'].items()):
        raise ValueError(f'ARCHIVE_CHANGED:{name}')
    return {t: load(folder / f'{t}.json') for t in ('stress', 'base')}, manifest


def nav_returns(track):
    from scripts.research_mode_report import returns_of
    returns, nav = returns_of(track, NAV0)
    return returns.loc[returns.index >= '2023-01-03'], nav / NAV0


def drawdown(nav):
    peak = nav.cummax()
    dd = nav / peak - 1
    trough = dd.idxmin()
    start = nav.loc[:trough].idxmax()
    after = nav.loc[trough:]
    recovered = after[after >= nav.loc[start]]
    return {'max_drawdown': float(dd.min()), 'peak_date': str(start.date()), 'trough_date': str(trough.date()),
            'peak_to_trough_sessions': int(((nav.index > start) & (nav.index <= trough)).sum()),
            'recovery_date': str(recovered.index[0].date()) if len(recovered) else None,
            'trough_to_recovery_sessions': int(((nav.index > trough) & (nav.index <= recovered.index[0])).sum()) if len(recovered) else None}, dd


def fills_of(track):
    slip = float(track['result']['slippage'])
    fills = pd.DataFrame([r for r in track['result']['ledger'] if r['kind'] == 'FILL'])
    fills['date'] = pd.to_datetime(fills['date'])
    # ponytail: fill prices already include slippage; the slippage cost is backed out from the recorded rate, not from a reference quote
    fills['slippage_cost'] = np.where(fills['side'] == 'buy', fills['notional'] - fills['notional'] / (1 + slip), fills['notional'] / (1 - slip) - fills['notional'])
    fills['cost'] = fills['fee'] + fills['tax'] + fills['slippage_cost']
    return fills


def style_regression(net, bench, liq):
    """net = a + b1 * V2 proxy + b2 * (liquid tercile - illiquid tercile), OLS with HAC errors (diagnostic)."""
    import statsmodels.api as sm
    from core.research.attribution import hac_maxlags
    frame = pd.concat([net.rename('y'), bench.rename('v2'), liq.rename('liq')], axis=1).dropna()
    fit = sm.OLS(frame['y'], sm.add_constant(frame[['v2', 'liq']])).fit(cov_type='HAC', cov_kwds={'maxlags': hac_maxlags(len(frame))})
    return {'alpha_annual': float(fit.params['const'] * 252), 'alpha_p_hac': float(fit.pvalues['const']), 'beta_v2': float(fit.params['v2']),
            'beta_liquidity': float(fit.params['liq']), 'beta_liquidity_p': float(fit.pvalues['liq']), 'r_squared': float(fit.rsquared), 'n': len(frame)}


def main():
    from core.research.attribution import run_attribution
    from core.research.d5_research_variant import raw_factor_panels
    from scripts import track_b_stats as stats
    from scripts.prepare_h1_signals import frames_of
    from scripts.prepare_h4_signals import _winsor_rank
    from scripts.research_h2_a191_liquidity import attribution, stock_day_pnl
    from scripts.research_mode_report import asset_pnl
    from scripts.research_validation_v2 import _prepared
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / 'chart_data').mkdir(exist_ok=True)
    out = {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_ledger_rows': len(workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER))}
    bars, events, inputs = _prepared()
    frames = frames_of({'price_basis': 'RAW', 'bars': bars})
    universe = pd.DataFrame(load(RM / 'prepared_v6/universe.json'))
    member = universe.pivot(index='asof_date', columns='asset_id', values='member').fillna(False).astype(bool)
    member.index = pd.to_datetime(member.index)
    eligible = member.reindex(index=frames['close'].index, columns=frames['close'].columns).fillna(False).astype(bool)
    amihud = raw_factor_panels(frames)['amihud_20d']
    rank = amihud.where(eligible).rank(axis=1, pct=True)
    tercile = pd.DataFrame(np.select([rank <= 1 / 3, rank <= 2 / 3, rank > 2 / 3], ['T1_LIQUID', 'T2_MID', 'T3_ILLIQUID'], default='UNRANKED'),
                           index=rank.index, columns=rank.columns)
    adv = frames['amount'].rolling(20, min_periods=20).mean().shift(1)
    adv_pct = adv.where(eligible).rank(axis=1, pct=True)
    bench = pd.read_csv(RM / 'validation_execution_v1/benchmark_series.csv', index_col='date', parse_dates=True)['v2_total']
    data, net, navs, pnl = {}, {}, {}, {}
    for name in RUNS:
        data[name], manifest = tracks(name)
        net[name], navs[name] = nav_returns(data[name]['stress'])
        pnl[name] = stock_day_pnl(data[name]['stress'])
        out.setdefault('run_outputs_sha256', {})[name] = manifest['output_sha256']
    navs['V2'] = (1 + bench.loc['2023-01-03':]).cumprod()  # same window and base as the strategies
    s4, b4 = data['H4']['stress'], data['H4']['base']

    # A1 stock contribution, reconciled to the archived per-stock profit
    sdp = pnl['H4']
    per = sdp.groupby('stock').agg(net_pnl=('pnl', 'sum'), held_days=('value', lambda v: int((v > 0).sum())),
                                   mean_held_value=('value', lambda v: float(v[v > 0].mean()) if (v > 0).any() else 0.0))
    reference = pd.Series(asset_pnl(s4))
    gap = float((per['net_pnl'].reindex(reference.index).fillna(0) - reference).abs().max())
    if gap > 1e-6 * NAV0:
        raise ValueError(f'PNL_RECONCILIATION_GAP:{gap}')
    held = sdp[sdp['value'] > 0]
    per['first_held'] = held.groupby('stock')['date'].min().dt.date.astype(str)
    per['last_held'] = held.groupby('stock')['date'].max().dt.date.astype(str)
    fills = fills_of(s4)
    per = per.join(fills.groupby('stock_id').agg(traded_notional=('notional', 'sum'), trading_cost=('cost', 'sum'), fills=('notional', 'size')))
    for year in (2023, 2024, 2025):
        per[f'pnl_{year}'] = sdp[sdp['date'].dt.year == year].groupby('stock')['pnl'].sum()
    per = per.fillna({'traded_notional': 0, 'trading_cost': 0, 'fills': 0}).sort_values('net_pnl', ascending=False)
    gross, loss, total = float(per.net_pnl[per.net_pnl > 0].sum()), float(per.net_pnl[per.net_pnl < 0].sum()), float(per.net_pnl.sum())
    per.reset_index(names='stock').to_csv(DOCS / 'h4_stock_contribution.csv', index=False, encoding='utf-8-sig')
    out['stocks'] = {'held': len(per), 'winners': int((per.net_pnl > 0).sum()), 'losers': int((per.net_pnl < 0).sum()), 'gross_profit': gross,
                     'gross_loss': loss, 'net_profit': total, 'reconciliation_max_gap': gap,
                     'top5_share_of_gross': float(per.net_pnl.head(5).sum() / gross), 'top5_share_of_net': float(per.net_pnl.head(5).sum() / total),
                     'top5': per.head(5)[['net_pnl']].round(0).reset_index(names='stock').to_dict('records'),
                     'bottom5': per.tail(5)[['net_pnl']].round(0).reset_index(names='stock').to_dict('records')}

    # A1b growth rank at selection against the realized holding-period result of each held position
    targets = pd.read_csv(RUNS['H4'][0] / 'targets.csv', dtype={'asset_id': str})
    dates = sorted(targets['asof_date'].unique())
    matrix = pd.read_parquet(ROOT / 'data/processed/fundamental_pit_v3_2_verified/fundamental_matrix.parquet')
    matrix['asof_date'], matrix['ticker'] = pd.to_datetime(matrix['asof_date']), matrix['ticker'].astype(str)
    growth = {m: matrix.pivot(index='asof_date', columns='ticker', values=m) for m in ('operating_income_yoy', 'eps_yoy')}
    episodes = []
    for k, day in enumerate(dates):
        stamp = pd.Timestamp(day)
        members = member.columns[member.loc[stamp]] if stamp in member.index else []
        g = pd.concat([_winsor_rank(growth[m].loc[stamp].reindex(members).dropna()) for m in growth], axis=1).mean(axis=1)
        end = pd.Timestamp(dates[k + 1]) if k + 1 < len(dates) else sdp['date'].max()
        window = sdp[(sdp['date'] > stamp) & (sdp['date'] <= end)]
        for stock in targets.loc[targets['asof_date'] == day, 'asset_id']:
            part = window[window['stock'] == stock]
            entry = part.loc[part['value'] > 0, 'value']
            if len(entry):
                episodes.append({'signal_date': day, 'stock': stock, 'growth_rank': g.get(stock, np.nan), 'pnl': part['pnl'].sum(),
                                 'entry_value': entry.iloc[0], 'period_return': part['pnl'].sum() / entry.iloc[0]})
    episodes = pd.DataFrame(episodes)
    episodes.to_csv(DOCS / 'chart_data/h4_growth_episodes.csv', index=False, encoding='utf-8-sig')
    top_half = episodes['growth_rank'] >= episodes['growth_rank'].median()
    out['growth_vs_holding'] = {'episodes': len(episodes), 'spearman': float(episodes[['growth_rank', 'period_return']].corr(method='spearman').iat[0, 1]),
                                'mean_return_top_half': float(episodes.loc[top_half, 'period_return'].mean()),
                                'mean_return_bottom_half': float(episodes.loc[~top_half, 'period_return'].mean())}

    # A2 liquidity, and A191_132 / H1 for comparison
    liq_rows, capacity = [], {}
    for name in RUNS:
        terciles, values = attribution(data[name]['stress'], tercile, adv, NAV0)
        liq_rows += [{'strategy': name, **t} for t in terciles]
        hd = pnl[name][pnl[name]['value'] > 0]
        pct = [adv_pct.at[d, s] if d in adv_pct.index and s in adv_pct.columns else np.nan for d, s in zip(hd['date'], hd['stock'])]
        capacity[name] = {**{k: values[k] for k in ('adv_ratio_p50', 'adv_ratio_p99', 'notional_share_above_1pct_adv', 'notional_share_above_10pct_adv',
                                                     'capital_multiple_for_p90_fill_at_5pct_adv', 'top5_share_of_gross_profit', 'top5_share_of_net_profit')},
                          'held_amount_percentile_median': float(np.nanmedian(pct)), 'held_amount_percentile_p25': float(np.nanpercentile(pct, 25))}
    pd.DataFrame(liq_rows).to_csv(DOCS / 'h4_liquidity_attribution.csv', index=False, encoding='utf-8-sig')
    out['liquidity'] = capacity
    out['size_attribution'] = 'SIZE_ATTRIBUTION_NOT_VERIFIED: no historical market cap or shares outstanding in the repository; traded amount is not market cap'
    out['industry'] = 'NOT_AVAILABLE: no industry classification file in the repository; the production DB is out of scope'

    # A3 H4 against H1 on the same signal dates
    h1t = pd.read_csv(RUNS['H1'][0] / 'targets.csv', dtype={'asset_id': str})
    overlap, tags = [], {}
    for day in dates:
        a, b = set(targets.loc[targets.asof_date == day, 'asset_id']), set(h1t.loc[h1t.asof_date == day, 'asset_id'])
        overlap.append({'signal_date': day, 'h4': len(a), 'h1': len(b), 'common': len(a & b), 'h4_only': sorted(a - b), 'h1_only': sorted(b - a)})
        tags[day] = (a & b, a - b, b - a)

    def tag(frame, own):
        labels = []
        for d, s in zip(frame['date'], frame['stock']):
            key = max([x for x in dates if pd.Timestamp(x) < d], default=None)
            common, h4_only, h1_only = tags[key] if key else (set(), set(), set())
            labels.append('COMMON' if s in common else ('OWN_ONLY' if s in (h4_only if own == 'H4' else h1_only) else 'CARRIED_OR_OTHER'))
        return frame.assign(group=labels)
    split = {n: tag(pnl[n], n).groupby('group')['pnl'].sum().to_dict() for n in ('H4', 'H1')}
    vs_h1 = run_attribution('H4_vs_H1', net['H4'], net['H1'])
    out['h4_vs_h1'] = {'mean_overlap': float(np.mean([o['common'] for o in overlap])), 'signal_dates': len(dates), 'pnl_by_group': split,
                       'alpha_on_h1_annual': vs_h1['alpha'] * 252, 'alpha_on_h1_p_hac': vs_h1['alpha_p_hac'], 'beta_on_h1': vs_h1['beta'],
                       'return_correlation': float(net['H4'].corr(net['H1']))}
    pd.DataFrame(overlap).to_csv(DOCS / 'chart_data/h4_h1_overlap.csv', index=False, encoding='utf-8-sig')

    # D1 style regression on the V2 proxy and a liquidity long-short built from the same raw bars (diagnostic, not a benchmark)
    daily = frames['close'] / frames['close'].shift(1) - 1
    prev = tercile.shift(1)
    liq = daily.where(prev == 'T1_LIQUID').mean(axis=1) - daily.where(prev == 'T3_ILLIQUID').mean(axis=1)
    liq = liq.loc[liq.index >= '2023-01-03']
    out['style_regression'] = {n: style_regression(net[n], bench, liq) for n in RUNS}

    # B1 years and months
    years = {}
    for name in ('H4', 'H1'):
        for y, g in net[name].groupby(net[name].index.year):
            years.setdefault(str(y), {})[name] = float((1 + g).prod() - 1)
    for y, g in bench.loc[bench.index >= '2023-01-03'].groupby(bench.loc[bench.index >= '2023-01-03'].index.year):
        years[str(y)]['V2'] = float((1 + g).prod() - 1)
    out['years'] = years
    monthly = pd.DataFrame({n: (1 + s).groupby(s.index.to_period('M')).prod() - 1 for n, s in (('H4', net['H4']), ('H1', net['H1']), ('V2', bench.loc[bench.index >= '2023-01-03']))})
    monthly.index = monthly.index.astype(str)
    monthly.to_csv(DOCS / 'chart_data/h4_monthly_returns.csv', encoding='utf-8-sig')
    out['year_stock_leaders'] = {str(y): {'top3': per[f'pnl_{y}'].nlargest(3).round(0).to_dict(), 'bottom3': per[f'pnl_{y}'].nsmallest(3).round(0).to_dict()}
                                 for y in (2023, 2024, 2025)}

    # B2 concentration by day
    r = net['H4']
    ordered = r.sort_values(ascending=False)
    k = max(1, int(round(len(r) * .05)))
    day_pnl = sdp.groupby(['date', 'stock'])['pnl'].sum().reset_index()
    best = []
    for d, v in ordered.head(10).items():
        top = day_pnl[day_pnl['date'] == d].nlargest(1, 'pnl')
        best.append({'date': str(d.date()), 'return': float(v), 'top_stock': top['stock'].iloc[0] if len(top) else None})
    out['days'] = {'sessions': len(r), 'sum_of_returns': float(r.sum()), 'best_5pct_sum': float(ordered.head(k).sum()),
                   'worst_5pct_sum': float(ordered.tail(k).sum()), 'positive_days_sum': float(r[r > 0].sum()), 'negative_days_sum': float(r[r < 0].sum()),
                   'best_5pct_share_recomputed': float(stats.best_days_share(r)), 'best_days': best}

    # C1 drawdown with the stocks that lost money inside the worst episode
    dd_info = {}
    for name in ('H4', 'H1', 'V2'):
        info, _ = drawdown(navs[name].loc[navs[name].index >= '2023-01-03'])
        dd_info[name] = info
    w = dd_info['H4']
    inside = sdp[(sdp['date'] > w['peak_date']) & (sdp['date'] <= w['trough_date'])].groupby('stock')['pnl'].sum().sort_values()
    dd_info['H4']['losses_inside'] = inside.head(8).round(0).to_dict()
    dd_info['H4']['loss_inside_total'] = float(inside.sum())
    trough = next(x for x in s4['result']['daily'] if x['date'] == w['trough_date'])
    dd_info['H4']['positions_at_trough'] = sorted(trough['positions'])
    out['drawdown'] = dd_info

    # C2 trading costs
    by_year = fills.groupby(fills['date'].dt.year)[['fee', 'tax', 'slippage_cost', 'cost', 'notional']].sum()
    by_date = fills.groupby(fills['date'].dt.date)[['cost', 'notional']].sum().sort_values('cost', ascending=False)
    out['costs'] = {'total_cost': float(fills['cost'].sum()), 'fees': float(fills['fee'].sum()), 'taxes': float(fills['tax'].sum()),
                    'slippage_estimate': float(fills['slippage_cost'].sum()), 'traded_notional': float(fills['notional'].sum()),
                    'by_year': by_year.round(0).to_dict('index'), 'top_rebalance_dates': by_date.head(5).round(0).reset_index().astype(str).to_dict('records'),
                    'total_return_1x': float(nav_returns(b4)[1].iloc[-1] - 1),
                    'total_return_2x': float(navs['H4'].iloc[-1] - 1)}
    by_year.reset_index(names='year').to_csv(DOCS / 'chart_data/h4_costs_by_year.csv', index=False, encoding='utf-8-sig')

    # C3 statistics, recomputed with the existing functions and the registered trial variance
    result = load(RM / 'report_h4_v2/h4_result.json')
    aligned = pd.concat([r.rename('s'), bench.rename('b')], axis=1).dropna()
    excess = (aligned['s'] - aligned['b']).to_numpy()
    sd = excess.std(ddof=1)
    sr = excess.mean() / sd
    z = (excess - excess.mean()) / sd
    skew, kurt = float((z ** 3).mean()), float((z ** 4).mean())
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    variance = result['trial_sharpe_variance']
    se = math.sqrt(denom / (len(excess) - 1))
    by_year_sr = {str(y): float(stats.sharpe(g['s'] - g['b'])) for y, g in aligned.groupby(aligned.index.year)}
    excess_by_year = {str(y): float((g['s'] - g['b']).sum()) for y, g in aligned.groupby(aligned.index.year)}
    out['statistics'] = {
        'sessions': len(excess), 'excess_sharpe_annual': float(sr * math.sqrt(252)), 'skew': skew, 'kurtosis': kurt,
        'sharpe_standard_error_annual': float(se * math.sqrt(252)),
        'bootstrap': stats.block_bootstrap_sharpe(excess), 'recorded_bootstrap_lower': result['primary']['values']['bootstrap_lower'],
        'dsr_743_recomputed': stats.deflated_sharpe(excess, 743, variance), 'recorded_dsr': result['primary']['values']['deflated_sharpe'],
        'expected_max_sharpe_annual': {str(n): float(stats.expected_max_sharpe(n, variance) * math.sqrt(252)) for n in (12, 242, 534, 743)},
        'sessions_needed_psr_95': float((1.645 ** 2) * denom / sr ** 2 + 1),
        'sessions_needed_dsr_95_at_743': float((1.645 / max(sr - stats.expected_max_sharpe(743, variance), 1e-12)) ** 2 * denom + 1)
                                          if sr > stats.expected_max_sharpe(743, variance) else None,
        'excess_sharpe_by_year': by_year_sr, 'excess_return_sum_by_year': excess_by_year,
        'share_of_excess_from_2025': excess_by_year['2025'] / sum(excess_by_year.values()),
        'alpha_annual': result['primary']['alpha_annual'], 'alpha_p_hac': result['primary']['alpha_p_hac']}

    # D2 event exposure
    ledger = s4['result']['ledger']
    out['events'] = {'forfeits': s4.get('forfeits'), 'forfeited': [{k: r.get(k) for k in ('date', 'stock_id', 'missing_terms', 'forfeited_value')}
                                                                 for r in ledger if r['kind'] == 'ENTITLEMENT_FORFEITED'],
                     'order_status': pd.Series([f"{r['status']}:{r.get('reason')}" for r in ledger if r['kind'] == 'ORDER_STATUS']).value_counts().to_dict(),
                     'cancels': sum(r['kind'] == 'CANCEL' for r in ledger), 'cash_entitlements': sum(r['kind'] == 'CASH_ENTITLEMENT' for r in ledger),
                     'data_status': s4['result']['data_status'], 'price_basis': s4['result']['price_basis'],
                     'stock_receivable_valuation': s4['result']['stock_receivable_valuation']}
    out['inputs_sha256'] = {**inputs, 'h4_result.json': workflow.sha256(RM / 'report_h4_v2/h4_result.json'),
                            'benchmark_series.csv': workflow.sha256(RM / 'validation_execution_v1/benchmark_series.csv'),
                            'fundamental_matrix.parquet': workflow.sha256(ROOT / 'data/processed/fundamental_pit_v3_2_verified/fundamental_matrix.parquet')}
    target = RM / 'h4_attribution_v1'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'summary.json').write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    charts(out, navs, per, pd.DataFrame(liq_rows), monthly, r, fills, dd_info)
    return out


def charts(out, navs, per, liq, monthly, r, fills, dd_info):
    import matplotlib.pyplot as plt
    from scripts.research_failure_review import _figure
    start = '2023-01-03'
    curve = pd.DataFrame({'H4 主要設定': navs['H4'], 'H1 主要設定': navs['H1'], 'V2 代理（含息）': navs['V2']}).loc[start:]
    colors = {'H4 主要設定': '#c0392b', 'H1 主要設定': '#e67e22', 'V2 代理（含息）': '#222'}

    def c01(ax):
        for c in curve:
            ax.plot(curve.index, curve[c], color=colors[c], lw=2.5, label=c)
        ax.set_ylabel('淨值（起始 1.0）')
        ax.legend()
    _figure('01_h4_vs_h1_benchmark', 'H4、H1 與 V2 代理累積報酬', 'H4、H1 為 2x 成本後淨值；V2 為等權含息代理、不扣成本｜' + CAPTION,
            curve.reset_index(names='date'), c01, out=DOCS)
    years = pd.DataFrame(out['years']).T

    def c02(ax):
        years[['H4', 'H1', 'V2']].plot.bar(ax=ax, rot=0, color=['#c0392b', '#e67e22', '#222'])
        ax.axhline(0, color='k', lw=1)
        ax.set_ylabel('年度報酬')
    _figure('02_annual_returns', '年度報酬：H4、H1、V2 代理', CAPTION, years.reset_index(names='year'), c02, out=DOCS)
    shown = pd.concat([per.net_pnl.head(10), per.net_pnl.tail(10)]).sort_values()

    def c03(ax):
        ax.barh(shown.index.astype(str), shown.values, color=['#c0392b' if v > 0 else '#2471a3' for v in shown.values])
        ax.set_xlabel('累計淨損益（新台幣，初始資金 1,000,000）')
    _figure('03_stock_profit_contribution', 'H4 個股損益：前 10 大獲利與前 10 大虧損', '逐股逐日損益加總，與帳本 asset_pnl 對帳差距 0｜' + CAPTION,
            per.reset_index(names='stock'), c03, out=DOCS)

    def c04(ax):
        ax.axis('off')
        ax.text(.5, .55, 'NOT_AVAILABLE', ha='center', fontsize=48, color='#999')
        ax.text(.5, .38, 'Repository 沒有可信的產業分類資料；正式 DB 不在本輪範圍內，未產生替代數據', ha='center', fontsize=16)
    _figure('04_sector_exposure', 'H4 產業曝險', 'NOT_AVAILABLE｜' + CAPTION, pd.DataFrame({'status': ['NOT_AVAILABLE'], 'reason': [out['industry']]}), c04, out=DOCS)
    pivot = liq.pivot(index='strategy', columns='tercile', values='net_share')[['T1_LIQUID', 'T2_MID', 'T3_ILLIQUID', 'UNRANKED']]
    pct = pd.Series({k: v['held_amount_percentile_median'] for k, v in out['liquidity'].items()})

    def c05(axes):
        pivot.plot.barh(stacked=True, ax=axes[0])
        axes[0].set_xlabel('淨利占比（依當日 amihud 三分位）')
        axes[1].bar(pct.index, pct.values, color=['#c0392b', '#e67e22', '#7f8c8d'])
        axes[1].axhline(.5, color='k', ls='--', label='成員中位數')
        axes[1].set_ylabel('持股 20 日平均成交金額在成員中的百分位（中位數）')
        axes[1].set_ylim(0, 1)
        axes[1].legend()
    _figure('05_liquidity_attribution', '流動性歸因：H4 與 H1、A191_132', '成交金額不等於市值：SIZE_ATTRIBUTION_NOT_VERIFIED｜' + CAPTION,
            pd.concat([pivot.reset_index(), pct.rename('held_amount_percentile_median').rename_axis('strategy').reset_index()], ignore_index=True), c05, ncols=2, out=DOCS)
    pos = per.net_pnl[per.net_pnl > 0].sort_values(ascending=False)
    cum = pd.DataFrame({'stocks': range(1, len(pos) + 1), 'share_of_gross': pos.cumsum().values / pos.sum(),
                        'share_of_net': pos.cumsum().values / per.net_pnl.sum()})
    day_sorted = r.sort_values(ascending=False).reset_index(drop=True)

    def c06(axes):
        axes[0].plot(cum.stocks, cum.share_of_gross, label='占毛利')
        axes[0].plot(cum.stocks, cum.share_of_net, label='占淨利（r4 定義）')
        axes[0].axvline(5, color='k', ls=':')
        axes[0].axhline(.5, color='r', ls='--')
        axes[0].set_xlabel('獲利股票數（由大到小）')
        axes[0].legend()
        axes[1].plot(day_sorted.cumsum().values / r.sum(), color='#c0392b')
        axes[1].axvline(len(r) * .05, color='k', ls=':', label='最佳 5% 交易日')
        axes[1].axhline(1, color='#222', lw=1)
        axes[1].set_xlabel('交易日（由最佳到最差）')
        axes[1].set_ylabel('累積日報酬 ÷ 全期日報酬總和')
        axes[1].legend()
    _figure('06_profit_concentration', 'H4 獲利集中度：個股與交易日', 'r4 原判 FAIL 不變｜' + CAPTION, cum, c06, ncols=2, out=DOCS)
    dds = pd.DataFrame({k: (curve[c] / curve[c].cummax() - 1) for k, c in (('H4', 'H4 主要設定'), ('H1', 'H1 主要設定'), ('V2', 'V2 代理（含息）'))})

    def c07(ax):
        for k, c in (('H4', '#c0392b'), ('H1', '#e67e22'), ('V2', '#222')):
            ax.plot(dds.index, dds[k], color=c, label=f"{k} 最大回撤 {dd_info[k]['max_drawdown']:.1%}")
        ax.axvspan(pd.Timestamp(dd_info['H4']['peak_date']), pd.Timestamp(dd_info['H4']['trough_date']), color='#c0392b', alpha=.1)
        ax.set_ylabel('回撤')
        ax.legend()
    _figure('07_drawdown', 'H4 回撤與比較', '陰影為 H4 最大回撤區間｜' + CAPTION, dds.reset_index(names='date'), c07, out=DOCS)
    costs = fills.groupby(fills['date'].dt.date)[['fee', 'tax', 'slippage_cost']].sum()

    def c08(axes):
        costs.plot.bar(stacked=True, ax=axes[0], rot=90)
        axes[0].set_ylabel('交易成本（新台幣）')
        axes[0].tick_params(axis='x', labelsize=8)
        totals = pd.Series({'1x 成本': out['costs']['total_return_1x'], '2x 成本': out['costs']['total_return_2x']})
        axes[1].bar(totals.index, totals.values, color=['#7f8c8d', '#c0392b'])
        axes[1].set_ylabel('總報酬')
    _figure('08_turnover_cost', 'H4 交易成本與成本敏感度', '滑價成本由記錄的滑價率回推｜' + CAPTION, costs.reset_index(names='date'), c08, ncols=2, out=DOCS)
    st = out['statistics']
    yearly_sr = pd.Series(st['excess_sharpe_by_year'])
    emax = pd.Series(st['expected_max_sharpe_annual'])

    def c09(axes):
        axes[0].bar(yearly_sr.index, yearly_sr.values, color='#c0392b')
        axes[0].axhline(st['excess_sharpe_annual'], color='k', ls='--', label=f"全期 {st['excess_sharpe_annual']:.2f}")
        axes[0].set_ylabel('年化超額 Sharpe（V2）')
        axes[0].legend()
        axes[1].bar(emax.index, emax.values, color='#7f8c8d', label='試驗數下的期望最大 Sharpe')
        axes[1].axhline(st['excess_sharpe_annual'], color='#c0392b', ls='--', label='H4 觀察值')
        axes[1].set_xlabel('試驗數')
        axes[1].set_ylabel('年化 Sharpe')
        axes[1].legend()
    _figure('09_r1_r5_diagnostics', 'H4 統計診斷：年度超額 Sharpe 與 r3 門檻來源', 'r2 bootstrap 下界、r3 DSR 以既有函式重算並與封存值核對｜' + CAPTION,
            pd.concat([yearly_sr.rename('excess_sharpe').rename_axis('year').reset_index(), emax.rename('expected_max').rename_axis('trials').reset_index()], ignore_index=True),
            c09, ncols=2, out=DOCS)
    lines = [('研究 ID', 'H4:GROWTH-MOMENTUM-V2 主要設定 Top10／60D／等權'), ('封存判定', 'CONTINUE（不覆寫）'),
             ('2x 總報酬／V2 代理', f"{out['costs']['total_return_2x']:.1%}／{navs['V2'].iloc[-1] - 1:.1%}"),
             ('alpha（V2）／加入流動性因子後', f"{st['alpha_annual']:+.1%}（p {st['alpha_p_hac']:.2f}）／{out['style_regression']['H4']['alpha_annual']:+.1%}（p {out['style_regression']['H4']['alpha_p_hac']:.2f}）"),
             ('相對 H1 的 alpha', f"{out['h4_vs_h1']['alpha_on_h1_annual']:+.1%}（p {out['h4_vs_h1']['alpha_on_h1_p_hac']:.2f}）"),
             ('2025 年占超額報酬', f"{st['share_of_excess_from_2025']:.0%}"), ('前 5 檔占毛利／淨利', f"{out['stocks']['top5_share_of_gross']:.0%}／{out['stocks']['top5_share_of_net']:.0%}"),
             ('最大回撤', f"{dd_info['H4']['max_drawdown']:.1%}（{dd_info['H4']['peak_date']} → {dd_info['H4']['trough_date']}）"),
             ('r1～r5', 'PASS／FAIL／FAIL／FAIL／PASS'), ('規模與產業', 'SIZE_ATTRIBUTION_NOT_VERIFIED；產業 NOT_AVAILABLE')]

    def c10(ax):
        ax.axis('off')
        for i, (k, v) in enumerate(lines):
            ax.text(.02, .92 - i * .09, k, fontsize=17, fontweight='bold')
            ax.text(.36, .92 - i * .09, v, fontsize=17)
    _figure('10_h4_research_summary', 'H4 研究摘要', CAPTION, pd.DataFrame(lines, columns=['item', 'value']), c10, out=DOCS)
    plt.close('all')


def index_html():
    """One offline page: every Markdown report as preformatted text plus all charts."""
    import html
    docs = ['h4_final_decision.md', 'h4_attribution_report.md', 'h4_yearly_analysis.md', 'h4_risk_and_drawdown.md',
            'h4_statistical_diagnostics.md', 'h4_benchmark_comparison.md', 'README.md']
    sections = ''.join(f"<h2 id='{d}'>{d}</h2><pre>{html.escape((DOCS / d).read_text(encoding='utf-8'))}</pre>" for d in docs if (DOCS / d).is_file())
    toc = ''.join(f"<li><a href='#{d}'>{d}</a></li>" for d in docs if (DOCS / d).is_file())
    data = ''.join(f"<li><a href='{p.name}'>{p.name}</a></li>" for p in sorted(DOCS.glob('*.csv')))
    images = ''.join(f"<figure><img src='charts/{p.name}' alt='{p.stem}'><figcaption><a href='chart_data/{p.stem}.csv'>{p.stem}.csv</a></figcaption></figure>"
                     for p in sorted((DOCS / 'charts').glob('*.png')))
    page = ("<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><title>H4 attribution and reliability</title>"
            "<style>body{font-family:'Microsoft JhengHei',sans-serif;margin:24px;max-width:1500px}pre{white-space:pre-wrap;background:#f6f6f6;padding:12px}"
            "figure{display:inline-block;width:48%;margin:4px;vertical-align:top}img{width:100%}</style></head><body>"
            "<h1>H4 獲利來源、風險與可靠性</h1><p>RESEARCH_UNCERTIFIED / DIAGNOSTIC_ONLY。期間 2023-01-03～2025-12-31；未新增回測；未使用 2026 資料。</p>"
            f"<h2>報告</h2><ul>{toc}</ul><h2>資料</h2><ul>{data}</ul><h2>圖表</h2>{images}{sections}</body></html>")
    (DOCS / 'index.html').write_text(page, encoding='utf-8')


if __name__ == '__main__':
    if sys.argv[1:] == ['index']:
        index_html()
    else:
        print(json.dumps(main(), ensure_ascii=False, indent=1, default=str))
