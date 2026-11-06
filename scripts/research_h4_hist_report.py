"""h4-historical-data-unlock-v1 diagnostics and delivery: coverage, PIT and correction bias, events, benchmarks, attribution, charts, index.

Reads the admitted historical inputs and the one registered run; the sealed 2023-2025 H4-V2 run is read only for comparison.
Diagnostics never change the registered verdict. Writes summary.json, CSVs and charts; the Markdown narrative is written by hand."""
import html
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow
from scripts.research_h4_hist_run import (DELIST, END, FUND, OUT, PREPARED, PRIMARY, RAW_HIST, RAW_LOCAL, RUNS, SIGNALS, START,
                                          V2_VARIANCE, load, planned_quotes, roc, taiex_series)

RM = ROOT / 'outputs/research_mode'
DOCS = ROOT / 'docs/research/2026-11-04-h4-historical-data-unlock-v1'
NAV0 = 1_000_000
SEALED_RUN = RM / 'h4_runs_v1/002'
CAPTION = 'RESEARCH_UNCERTIFIED／DIAGNOSTIC_ONLY｜未使用 2026 資料'


def figure(name, title, caption, data, draw, ncols=1):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['Microsoft JhengHei'], 'axes.unicode_minus': False, 'font.size': 13})
    fig, axes = plt.subplots(1, ncols, figsize=(16, 9), dpi=100)
    draw(axes)
    fig.suptitle(title, fontsize=22, fontweight='bold')
    fig.text(0.01, 0.01, caption, fontsize=11, color='#444')
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    for sub in ('charts', 'chart_data'):
        (DOCS / sub).mkdir(parents=True, exist_ok=True)
    fig.savefig(DOCS / 'charts' / f'{name}.png')
    plt.close(fig)
    data.to_csv(DOCS / 'chart_data' / f'{name}.csv', index=False, encoding='utf-8-sig')


def blocked(name, title, reason):
    def draw(ax):
        ax.axis('off')
        ax.text(.5, .58, 'BLOCKED', ha='center', fontsize=48, color='#999')
        ax.text(.5, .4, reason, ha='center', fontsize=14, wrap=True)
    figure(name, title, 'BLOCKED：沒有實際資料，不產生任何績效', pd.DataFrame({'status': ['BLOCKED'], 'reason': [reason]}), draw)


def coverage():
    """Per calendar year: quote files, trading days, stocks, members, signal days, scored members, G2/G3 and filing verification."""
    bars = load(PREPARED / 'bars.json')['bars']
    universe = pd.DataFrame(load(PREPARED / 'universe.json'))
    signals = load(SIGNALS / 'signals_manifest.json')
    schedule, scored = signals['signal_days']['60'], signals['scored_assets_per_signal_day']
    matrix = pd.read_parquet(FUND / 'fundamental_matrix.parquet')
    matrix['asof_date'] = pd.to_datetime(matrix['asof_date']).dt.strftime('%Y-%m-%d')
    matrix['ticker'] = matrix['ticker'].astype(str)
    audit = pd.read_csv(FUND / 'publication_revision_audit.csv', dtype={'ticker': str})
    members = universe.loc[universe['member']]
    sig = members.loc[members['asof_date'].isin(schedule), ['asof_date', 'asset_id']].merge(
        matrix[['asof_date', 'ticker', 'operating_income_yoy', 'eps_yoy']], left_on=['asof_date', 'asset_id'], right_on=['asof_date', 'ticker'], how='left')
    planned = pd.Series(planned_quotes())
    day_index = pd.Series(sorted(bars))
    rows = []
    for year in range(2015, 2023):
        y = str(year)
        days = day_index[day_index.str.startswith(y)]
        stocks = {s for d in days for s in bars[d]}
        m = members.loc[members['asof_date'].str.startswith(y)]
        s = sig.loc[sig['asof_date'].str.startswith(y)]
        audited = audit.loc[audit['fiscal_year'] == year, 'verification_status']
        rows.append({'year': year, 'planned_weekday_requests': int(planned.str.startswith(y).sum()),
                     'quote_files_present': int(sum((RAW_HIST / f'MI_INDEX_{d}.json').is_file() for d in planned[planned.str.startswith(y)])) if year < 2022 else None,
                     'trading_days': len(days), 'stocks_with_bars': len(stocks),
                     'members_per_day_mean': float(m.groupby('asof_date').size().mean()) if len(m) else 0.0,
                     'signal_days': sum(d.startswith(y) for d in schedule),
                     'scored_per_signal_day_min': min((scored.get(d, 0) for d in schedule if d.startswith(y)), default=0),
                     'g2_share_of_signal_members': float(s['operating_income_yoy'].notna().mean()) if len(s) else None,
                     'g3_share_of_signal_members': float(s['eps_yoy'].notna().mean()) if len(s) else None,
                     'no_fundamental_source_share': None,
                     'filings_audited_fy': len(audited), 'filings_verified_share_fy': float((audited == 'VERIFIED').mean()) if len(audited) else None,
                     'filings_excluded_share_fy': float((audited == 'EXCLUDED').mean()) if len(audited) else None})
    frame = pd.DataFrame(rows)
    per_day = pd.read_csv(OUT / 'signal_day_members.csv')
    share = per_day.assign(year=per_day['asof_date'].str[:4].astype(int)).groupby('year').apply(lambda g: g['no_source'].sum() / g['members'].sum())
    frame['no_fundamental_source_share'] = frame['year'].map(share)
    return frame


def correction_bias():
    """Design D3 on 2016-2022 members: forward 60-session raw-close return of member-days whose G2 comes from a later-corrected filing."""
    from scripts.prepare_h1_signals import frames_of
    bars = load(PREPARED / 'bars.json')['bars']
    close = frames_of({'price_basis': 'RAW', 'bars': bars})['close']
    fwd = (close.shift(-60) / close - 1).stack().rename('fwd60').reset_index()
    fwd.columns = ['asof_date', 'ticker', 'fwd60']
    universe = pd.DataFrame(load(PREPARED / 'universe.json'))
    members = universe.loc[universe['member'], ['asof_date', 'asset_id']].rename(columns={'asset_id': 'ticker'})
    members['asof_date'] = pd.to_datetime(members['asof_date'])
    records = pd.read_parquet(ROOT / 'data/processed/fundamental_pit_v3_yoy_same_basis/fundamental_records.parquet')
    records['ticker'] = records['ticker'].astype(str)
    g2 = records.loc[records['metric'].eq('operating_income_yoy') & records['value'].notna() & (records['publication_date'] <= END)].copy()
    # same availability rule as v3.3-hist: later of the current and prior-year base filing
    base = pd.read_parquet(FUND / 'fundamental_records.parquet', columns=['ticker', 'metric', 'fiscal_year', 'quarter', 'available_date'])
    g2 = g2.drop(columns='available_date').merge(base.loc[base['metric'] == 'operating_income_yoy'].drop(columns='metric'),
                                                 on=['ticker', 'fiscal_year', 'quarter'], how='left')
    # rows excluded from v3.3 carry no availability there: rebuild it from the same rule on the audit timestamps
    audit = pd.read_csv(FUND / 'publication_revision_audit.csv', dtype={'ticker': str})
    stamp = audit.set_index(['ticker', 'fiscal_year', 'quarter'])['dataset_publication_timestamp']
    cur = pd.to_datetime(stamp.reindex(pd.MultiIndex.from_arrays([g2['ticker'], g2['fiscal_year'], g2['quarter']])).to_numpy(), utc=True)
    pri = pd.to_datetime(stamp.reindex(pd.MultiIndex.from_arrays([g2['ticker'], g2['fiscal_year'] - 1, g2['quarter']])).to_numpy(), utc=True)
    fallback = pd.Series(np.maximum(cur.values, pri.values)).dt.tz_localize('UTC').dt.tz_convert('Asia/Taipei').dt.normalize().dt.tz_localize(None) + pd.Timedelta(days=1)
    g2['available_date'] = pd.to_datetime(g2['available_date']).fillna(pd.Series(fallback.values, index=g2.index))
    g2 = g2.dropna(subset=['available_date']).sort_values(['available_date', 'publication_date', 'revision_id'], kind='stable')
    joined = pd.merge_asof(members.sort_values('asof_date', kind='stable'), g2[['ticker', 'available_date', 'value', 'fiscal_year', 'quarter']],
                           left_on='asof_date', right_on='available_date', by='ticker', direction='backward').dropna(subset=['value'])
    status = audit.set_index(['ticker', 'fiscal_year', 'quarter'])['verification_status']
    c = status.reindex(pd.MultiIndex.from_arrays([joined['ticker'], joined['fiscal_year'].astype(int), joined['quarter'].astype(int)])).to_numpy()
    p = status.reindex(pd.MultiIndex.from_arrays([joined['ticker'], joined['fiscal_year'].astype(int) - 1, joined['quarter'].astype(int)])).to_numpy()
    joined['flagged'] = (c == 'EXCLUDED') | (p == 'EXCLUDED')
    data = joined.merge(fwd, on=['asof_date', 'ticker']).dropna(subset=['fwd60'])
    daily = data.groupby(['asof_date', 'flagged'])['fwd60'].mean().unstack().dropna()
    diff = daily[True] - daily[False]
    sparse = diff.iloc[::60]
    by_year = data.groupby([data['asof_date'].dt.year, 'flagged'])['fwd60'].mean().unstack().rename(columns={True: 'flagged', False: 'other'})
    by_year['flagged_share'] = data.groupby(data['asof_date'].dt.year)['flagged'].mean()
    return {'member_stock_days': len(data), 'flagged_share': float(data['flagged'].mean()),
            'mean_fwd60_flagged': float(data.loc[data.flagged, 'fwd60'].mean()), 'mean_fwd60_other': float(data.loc[~data.flagged, 'fwd60'].mean()),
            'daily_difference_mean': float(diff.mean()), 'nonoverlap_difference_mean': float(sparse.mean()),
            'nonoverlap_t': float(sparse.mean() / (sparse.std(ddof=1) / np.sqrt(len(sparse)))), 'nonoverlap_n': len(sparse)}, by_year.reset_index(names='year')


def survivorship():
    """Signal-day members later delisted, and their forward 60-session raw return against other members (diagnostic only)."""
    from scripts.prepare_h1_signals import frames_of
    per_day = pd.read_csv(OUT / 'signal_day_members.csv')
    bars = load(PREPARED / 'bars.json')['bars']
    close = frames_of({'price_basis': 'RAW', 'bars': bars})['close']
    delisted = {str(r['Code']): roc(r['DelistingDate']) for r in load(DELIST) if len(str(r['Code'])) == 4}
    universe = pd.DataFrame(load(PREPARED / 'universe.json'))
    schedule = load(SIGNALS / 'signals_manifest.json')['signal_days']['60']
    m = universe.loc[universe['member'] & universe['asof_date'].isin(schedule), ['asof_date', 'asset_id']]
    m = m.assign(delisted_on=m['asset_id'].map(delisted))
    gone = m.dropna(subset=['delisted_on'])
    rows = []
    for r in gone.itertuples(index=False):
        series = close[r.asset_id].dropna()
        start = series.loc[:r.asof_date]
        later = series.loc[r.asof_date:].iloc[1:61]
        rows.append({'asof_date': r.asof_date, 'stock': r.asset_id, 'delisted_on': r.delisted_on,
                     'last_close_date': str(series.index.max().date()), 'fwd60_or_to_last_close': float(later.iloc[-1] / start.iloc[-1] - 1) if len(later) and len(start) else None})
    return {'signal_day_members': int(per_day['members'].sum()), 'no_source_member_days': int(per_day['no_source'].sum()),
            'later_delisted_member_days': len(gone), 'later_delisted_stocks': int(gone['asset_id'].nunique())}, pd.DataFrame(rows)


def run_data():
    from scripts.research_h2_a191_liquidity import stock_day_pnl
    from scripts.research_mode_report import asset_pnl, returns_of
    folder = RUNS / '000'
    manifest = load(folder / 'manifest.json')
    if manifest['config']['config_id'] != PRIMARY or manifest['status'] != 'COMPUTABLE':
        return manifest, None
    if any(workflow.sha256(folder / n) != d for n, d in manifest['output_sha256'].items()):
        raise ValueError('RUN_OUTPUT_CHANGED')
    tracks = {t: load(folder / f'{t}.json') for t in ('stress', 'base')}
    returns, nav = returns_of(tracks['stress'], NAV0)
    pnl = stock_day_pnl(tracks['stress'])
    gap = float((pnl.groupby('stock')['pnl'].sum().reindex(pd.Series(asset_pnl(tracks['stress'])).index).fillna(0)
                 - pd.Series(asset_pnl(tracks['stress']))).abs().max())
    if gap > 1e-6 * NAV0:
        raise ValueError(f'PNL_RECONCILIATION_GAP:{gap}')
    return manifest, {'tracks': tracks, 'returns': returns.loc[returns.index >= START], 'nav': nav.loc[nav.index >= START] / NAV0, 'pnl': pnl}


def drawdown(nav):
    peak = nav.cummax()
    dd = nav / peak - 1
    trough = dd.idxmin()
    start = nav.loc[:trough].idxmax()
    recovered = nav.loc[trough:][nav.loc[trough:] >= nav.loc[start]]
    return {'max_drawdown': float(dd.min()), 'peak_date': str(start.date()), 'trough_date': str(trough.date()),
            'recovery_date': str(recovered.index[0].date()) if len(recovered) else None}, dd


def sealed_series():
    """2023-2025 H4-V2 primary (sealed), V2 and TAIEX total return for the comparison and the combined diagnostic."""
    from scripts.research_mode_report import returns_of
    manifest = load(SEALED_RUN / 'manifest.json')
    if any(workflow.sha256(SEALED_RUN / n) != d for n, d in manifest['output_sha256'].items()):
        raise ValueError('SEALED_RUN_CHANGED')
    r, _ = returns_of(load(SEALED_RUN / 'stress.json'), NAV0)
    r = r.loc[r.index >= '2023-01-03']
    v2 = pd.read_csv(RM / 'validation_execution_v1/benchmark_series.csv', index_col='date', parse_dates=True)['v2_total']
    levels = pd.read_csv(RM / 'h4_historical_v1/taiex_index_levels.csv', index_col=0, parse_dates=True)['taiex_total_return']
    return pd.DataFrame({'h4': r, 'v2': v2.reindex(r.index), 'taiex': levels.pct_change().reindex(r.index)})


def statistics(excess, nav0=None):
    from scripts import track_b_stats as stats
    excess = np.asarray(excess, dtype=float)
    return {'sessions': len(excess), 'excess_sharpe_annual': float(excess.mean() / excess.std(ddof=1) * math.sqrt(252)),
            'bootstrap': stats.block_bootstrap_sharpe(excess), 'dsr_743': float(stats.deflated_sharpe(excess, 743, V2_VARIANCE)),
            'best_5pct_days_share': stats.best_days_share(excess)}


def main():
    from core.research.attribution import run_attribution
    from core.research.d5_research_variant import raw_factor_panels
    from scripts import track_b_stats as stats
    from scripts.prepare_h1_signals import frames_of
    from scripts.research_h2_a191_liquidity import attribution
    from scripts.research_h4_attribution import fills_of, style_regression
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / 'chart_data').mkdir(exist_ok=True)
    admission = load(OUT / 'admission.json')
    out = {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'admission': admission}
    cover = coverage()
    cover.to_csv(DOCS / 'historical_data_coverage.csv', index=False, encoding='utf-8-sig')
    out['correction_bias'], bias_years = correction_bias()
    bias_years.to_csv(DOCS / 'chart_data/correction_bias_by_year.csv', index=False, encoding='utf-8-sig')
    out['survivorship'], gone = survivorship()
    gone.to_csv(DOCS / 'chart_data/later_delisted_members.csv', index=False, encoding='utf-8-sig')
    events = load(PREPARED / 'actions.json')
    kinds = pd.Series([e.get('type') for e in events['actions']]).value_counts().to_dict()
    missing = pd.Series([','.join(e.get('missing_terms', [])) for e in events['unresolved_events']]).value_counts().to_dict()
    out['events'] = {'actions_by_type': kinds, 'unresolved_by_missing_terms': missing,
                     'actions_by_year': pd.Series([e['ex_date'][:4] for e in events['actions']]).value_counts().sort_index().to_dict(),
                     'unresolved_by_year': pd.Series([e['ex_date'][:4] for e in events['unresolved_events']]).value_counts().sort_index().to_dict()}
    tx = taiex_series()
    tx.to_csv(OUT / 'taiex_index_levels_2015_2022.csv', index_label='date')
    result = load(OUT / 'h4_hist_result.json') if (OUT / 'h4_hist_result.json').is_file() else None
    out['result'] = result
    sealed = sealed_series()
    manifest, run = run_data() if result and result['primary']['status'] == 'COMPUTABLE' else (None, None)
    years = []
    for y, g in sealed.groupby(sealed.index.year):
        years.append({'year': y, 'window': '2023-2025 H4-V2 (sealed)', 'h4_2x': float((1 + g['h4']).prod() - 1),
                      'benchmark_v2': float((1 + g['v2']).prod() - 1), 'taiex_total_return': float((1 + g['taiex']).prod() - 1)})
    from scripts.research_validation_v2 import benchmark_v2
    proxy, _ = benchmark_v2(load(PREPARED / 'bars.json')['bars'], events)
    v2h = proxy['returns']
    v2h.index = pd.to_datetime(v2h.index)
    bench_daily = pd.DataFrame({'benchmark_v2': v2h, 'taiex_total_return': tx['taiex_total_return'].pct_change()}).loc[START:END]
    bench_daily.to_csv(DOCS / 'chart_data/benchmark_daily_2016_2022.csv', index_label='date')
    hist_bars = load(PREPARED / 'bars.json')['bars']
    payers = {}
    for e in events['actions']:
        if e.get('cash_per_share') and e['ex_date'] in hist_bars:
            payers.setdefault(e['ex_date'][:4], set()).add(e['stock_id'])
    stocks = {}
    for d, rows in hist_bars.items():
        stocks.setdefault(d[:4], set()).update(rows)
    share_by_year = {y: len(payers.get(y, ())) / len(stocks[y]) for y in sorted(stocks) if '2016' <= y <= '2022'}
    hist_check = {'rule': 'same as benchmark_v2: cash-dividend share of stocks >= 60% each year and excluded stock-days <= 5%',
                  'cash_dividend_share_by_year': share_by_year, 'excluded_share': proxy['diagnostics']['excluded_share'],
                  'status': 'PASS' if all(v >= .6 for v in share_by_year.values()) and proxy['diagnostics']['excluded_share'] <= .05 else 'BLOCKED',
                  'note': 'module diagnostics only check 2023-2025 (DEV_YEARS), so its own status is not applicable to this window'}
    out['benchmark_2016_2022'] = {'historical_check': hist_check, 'v2_diagnostics': proxy['diagnostics'], 'sessions': len(bench_daily),
                                  'total': {c: float((1 + bench_daily[c].fillna(0)).prod() - 1) for c in bench_daily}}
    if run is None:
        for y, g in bench_daily.groupby(bench_daily.index.year):
            years.append({'year': y, 'window': '2016-2022 (H4 not run: admission BLOCKED)', 'h4_2x': None,
                          'benchmark_v2': float((1 + g['benchmark_v2'].fillna(0)).prod() - 1),
                          'taiex_total_return': float((1 + g['taiex_total_return'].fillna(0)).prod() - 1)})
    if run is not None:
        daily = pd.read_csv(OUT / 'daily_returns.csv', index_col='date', parse_dates=True)
        net, bench, taiex = daily['net_2x'], daily['benchmark_v2'], daily['taiex_total_return']
        for y, g in daily.groupby(daily.index.year):
            years.insert(len([r for r in years if r['year'] < y]), {'year': y, 'window': '2016-2022 H4:HIST (this round)', 'h4_2x': float((1 + g['net_2x']).prod() - 1),
                         'benchmark_v2': float((1 + g['benchmark_v2']).prod() - 1), 'taiex_total_return': float((1 + g['taiex_total_return'].fillna(0)).prod() - 1)})
        nav = (1 + net).cumprod()
        out['drawdown'] = {'H4': drawdown(nav)[0], 'V2': drawdown((1 + bench).cumprod())[0], 'TAIEX_TR': drawdown((1 + taiex.fillna(0)).cumprod())[0]}
        pnl = run['pnl']
        per = pnl.groupby('stock')['pnl'].sum().sort_values(ascending=False)
        gross = float(per[per > 0].sum())
        industry = {r['公司代號']: r['產業別'] for r in load(RAW_LOCAL / 't187ap03_L.json')}
        from scripts.research_h4_historical import INDUSTRY
        pnl['industry'] = pnl['stock'].map(lambda s: INDUSTRY.get(industry.get(s), industry.get(s)) if s in industry else 'UNCLASSIFIED')
        sector = pnl.groupby([pnl['date'].dt.year, 'industry'])['pnl'].sum().unstack(fill_value=0)
        sector_total = pnl.groupby('industry')['pnl'].sum().sort_values(ascending=False)
        sector.reset_index(names='year').to_csv(DOCS / 'chart_data/sector_pnl_by_year.csv', index=False, encoding='utf-8-sig')
        out['stocks'] = {'held': len(per), 'net_profit': float(per.sum()), 'gross_profit': gross, 'top5_share_of_gross': float(per.head(5).sum() / gross),
                         'top5': per.head(5).round(0).to_dict(), 'bottom5': per.tail(5).round(0).to_dict()}
        out['sector'] = {'classification': 't187ap03_L.json 2026-08-06: CURRENT_CLASSIFICATION_NOT_PIT', 'net_by_industry': sector_total.round(0).to_dict(),
                         'top3_share_of_net': float(sector_total.head(3).sum() / per.sum()) if per.sum() > 0 else None,
                         'unclassified_net': float(sector_total.get('UNCLASSIFIED', 0.0))}
        bars = load(PREPARED / 'bars.json')['bars']
        frames = frames_of({'price_basis': 'RAW', 'bars': bars})
        universe = pd.DataFrame(load(PREPARED / 'universe.json'))
        member = universe.pivot(index='asof_date', columns='asset_id', values='member').fillna(False).astype(bool)
        member.index = pd.to_datetime(member.index)
        eligible = member.reindex(index=frames['close'].index, columns=frames['close'].columns).fillna(False).astype(bool)
        rank = raw_factor_panels(frames)['amihud_20d'].where(eligible).rank(axis=1, pct=True)
        tercile = pd.DataFrame(np.select([rank <= 1 / 3, rank <= 2 / 3, rank > 2 / 3], ['T1_LIQUID', 'T2_MID', 'T3_ILLIQUID'], default='UNRANKED'),
                               index=rank.index, columns=rank.columns)
        adv = frames['amount'].rolling(20, min_periods=20).mean().shift(1)
        terciles, values = attribution(run['tracks']['stress'], tercile, adv, NAV0)
        pd.DataFrame(terciles).to_csv(DOCS / 'chart_data/liquidity_terciles.csv', index=False, encoding='utf-8-sig')
        r_daily = frames['close'] / frames['close'].shift(1) - 1
        prev = tercile.shift(1)
        liq = (r_daily.where(prev == 'T1_LIQUID').mean(axis=1) - r_daily.where(prev == 'T3_ILLIQUID').mean(axis=1)).loc[START:]
        out['liquidity'] = {'terciles': terciles, **{k: values[k] for k in ('adv_ratio_p50', 'adv_ratio_p99', 'notional_share_above_1pct_adv',
                                                                          'capital_multiple_for_p90_fill_at_5pct_adv')},
                            'style_regression': style_regression(net, bench, liq)}
        out['size'] = 'SIZE_ATTRIBUTION_NOT_VERIFIED: no historical market cap or shares in any allowlisted source; traded amount is not market cap'
        regime = stats.regimes((1 + bench).cumprod()).reindex(net.index)
        out['regimes'] = {k: {'sessions': int(len(g)), 'h4_annualized': float(g['net'].mean() * 252), 'v2_annualized': float(g['bench'].mean() * 252)}
                          for k, g in pd.DataFrame({'net': net, 'bench': bench, 'regime': regime}).dropna().groupby('regime')}
        fills = fills_of(run['tracks']['stress'])
        by_year = fills.groupby(fills['date'].dt.year)[['fee', 'tax', 'slippage_cost', 'cost', 'notional']].sum()
        by_year.reset_index(names='year').to_csv(DOCS / 'chart_data/costs_by_year.csv', index=False, encoding='utf-8-sig')
        out['costs'] = {'total_cost': float(fills['cost'].sum()), 'traded_notional': float(fills['notional'].sum()),
                        'by_year': by_year.round(0).to_dict('index')}
        ledger = run['tracks']['stress']['result']['ledger']
        out['trade_events'] = {'forfeited': sum(r['kind'] == 'ENTITLEMENT_FORFEITED' for r in ledger),
                               'forfeited_value': float(sum(r.get('forfeited_value') or 0 for r in ledger if r['kind'] == 'ENTITLEMENT_FORFEITED')),
                               'forced_exits': sum(r['kind'] in ('FORCED_EXIT', 'REFERENCE_CHANGE_EXIT') for r in ledger),
                               'order_status': pd.Series([f"{r['status']}:{r.get('reason')}" for r in ledger if r['kind'] == 'ORDER_STATUS']).value_counts().to_dict(),
                               'cancels': sum(r['kind'] == 'CANCEL' for r in ledger), 'cash_entitlements': sum(r['kind'] == 'CASH_ENTITLEMENT' for r in ledger),
                               'ledger_kinds': pd.Series([r['kind'] for r in ledger]).value_counts().to_dict()}
        hist_excess = (net - bench).dropna()
        dev_excess = (sealed['h4'] - sealed['v2']).dropna()
        both = pd.concat([hist_excess, dev_excess])
        out['statistics'] = {'2016_2022': statistics(hist_excess), '2023_2025': statistics(dev_excess),
                             'combined_2016_2025_diagnostic': statistics(both),
                             'combined_alpha_v2': run_attribution('combined', pd.concat([net, sealed['h4']]), pd.concat([bench, sealed['v2']])),
                             'combined_alpha_taiex': run_attribution('combined', pd.concat([net, sealed['h4']]), pd.concat([taiex, sealed['taiex']]).dropna()),
                             'excess_sum_by_year': {str(y): float(g.sum()) for y, g in both.groupby(both.index.year)}}
        pd.DataFrame({'h4_2x': pd.concat([net, sealed['h4']]), 'benchmark_v2': pd.concat([bench, sealed['v2']]),
                      'taiex_total_return': pd.concat([taiex, sealed['taiex']])}).to_csv(DOCS / 'chart_data/daily_returns_2016_2025.csv', index_label='date')
    annual = pd.DataFrame(years).sort_values('year')
    annual['excess_vs_v2'] = annual['h4_2x'] - annual['benchmark_v2']
    annual['excess_vs_taiex_tr'] = annual['h4_2x'] - annual['taiex_total_return']
    annual.to_csv(DOCS / 'h4_annual_results.csv', index=False, encoding='utf-8-sig')
    gates = []
    sealed_result = load(RM / 'report_h4_v2/h4_result.json')['primary']
    for name, primary in (('2016-2022 H4:HIST-2016-2022-V1', result['primary'] if result else None), ('2023-2025 H4:GROWTH-MOMENTUM-V2 (sealed)', sealed_result)):
        if primary is None or primary.get('gates') is None:
            gates.append({'window': name, 'status': 'BLOCKED' if result is None else primary.get('status')})
            continue
        v = primary['values']
        gates.append({'window': name, 'status': primary.get('screen_status'), **{g: primary['gates'][g] for g in ('r1', 'r2', 'r3', 'r4', 'r5')},
                      'net_sharpe_2x': v['net_sharpe_2x'], 'excess_sharpe_2x': v['excess_sharpe_2x'], 'bootstrap_lower': v['bootstrap_lower'],
                      'deflated_sharpe': v['deflated_sharpe'], 'top5_assets_profit_share': v['top5_assets_profit_share'],
                      'best5pct_days_return_share': v['best5pct_days_return_share'], 'alpha_annual_v2': primary['alpha_annual'],
                      'alpha_p_hac': primary['alpha_p_hac'], 'total_return_2x': primary['total_return_2x'], 'max_drawdown_2x': primary['max_drawdown_2x']})
    pd.DataFrame(gates).to_csv(DOCS / 'h4_r1_r5_results.csv', index=False, encoding='utf-8-sig')
    (OUT / 'summary.json').write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    charts(out, cover, annual, pd.DataFrame(gates), run)
    return out


def charts(out, cover, annual, gates, run):
    reason = '資料准入未通過：' + ', '.join(k for k, v in out['admission']['checks'].items() if not v)

    def bar_cover(axes):
        axes[0].bar(cover['year'], cover['trading_days'], color='#4C72B0')
        axes[0].set_title('每年交易日數')
        axes[1].plot(cover['year'], cover['g2_share_of_signal_members'], marker='o', label='G2 占訊號日成員')
        axes[1].plot(cover['year'], cover['g3_share_of_signal_members'], marker='o', label='G3 占訊號日成員')
        axes[1].plot(cover['year'], cover['filings_verified_share_fy'], marker='s', label='申報 VERIFIED 比例（會計年度）')
        axes[1].plot(cover['year'], cover['no_fundamental_source_share'], marker='x', label='完全沒有財報來源')
        axes[1].set_ylim(0, 1)
        axes[1].legend()
        axes[1].set_title('財報覆蓋與驗證')
    figure('01_data_coverage', '2015～2022 歷史資料覆蓋', '來源：TWSE MI_INDEX（2015～2021 本輪取得）、v3.3-hist 財報｜' + CAPTION, cover, bar_cover, 2)
    if run is None:
        bd = pd.read_csv(DOCS / 'chart_data/benchmark_daily_2016_2022.csv', index_col='date', parse_dates=True)
        bnav = (1 + bd.fillna(0)).cumprod()

        def bench_only(ax):
            ax.plot(bnav.index, bnav['benchmark_v2'], label='V2 等權含息代理（不扣成本）')
            ax.plot(bnav.index, bnav['taiex_total_return'], label='加權報酬指數（含息、市值加權）')
            ax.text(.5, .92, 'H4 未執行：資料准入 BLOCKED（存活者缺口）', transform=ax.transAxes, ha='center', fontsize=16, color='#C44E52')
            ax.legend()
        figure('02_h4_vs_benchmark', '2016～2022 市場基準（H4 未執行）', '只有市場基準，沒有任何策略績效｜RESEARCH_UNCERTIFIED', bnav.reset_index(), bench_only)
        for name, title in (('04_drawdown', 'H4 回撤'), ('06_market_regime', '市場環境'),
                            ('07_sector_exposure', '產業損益'), ('08_size_liquidity', '規模與流動性')):
            blocked(name, title, reason)
    else:
        daily = pd.read_csv(DOCS / 'chart_data/daily_returns_2016_2025.csv', index_col='date', parse_dates=True)
        nav = (1 + daily.fillna(0)).cumprod()

        def eq(axes):
            for k, (lo, hi) in enumerate(((START, END), ('2023-01-03', '2025-12-31'))):
                part = (1 + daily.loc[lo:hi].fillna(0)).cumprod()
                for col, label in (('h4_2x', 'H4 2x 成本'), ('benchmark_v2', 'V2 等權含息代理'), ('taiex_total_return', '加權報酬指數（含息）')):
                    axes[k].plot(part.index, part[col], label=label)
                axes[k].set_title(('2016～2022（本輪）' if k == 0 else '2023～2025（封存 H4-V2）') + '，各自從 1 起算')
                axes[k].legend()
        figure('02_h4_vs_benchmark', 'H4 與市場基準', '基準：V2 為等權含息代理、不扣成本；加權報酬指數為市值加權含息、不可直接投資｜' + CAPTION,
               nav.reset_index(), eq, 2)

        def dd(ax):
            for col, label in (('h4_2x', 'H4 2x 成本'), ('benchmark_v2', 'V2'), ('taiex_total_return', '加權報酬指數')):
                for lo, hi in ((START, END), ('2023-01-03', '2025-12-31')):
                    part = (1 + daily.loc[lo:hi, col].fillna(0)).cumprod()
                    ax.plot(part.index, part / part.cummax() - 1, label=label if lo == START else None,
                            color={'h4_2x': '#C44E52', 'benchmark_v2': '#4C72B0', 'taiex_total_return': '#55A868'}[col])
            ax.axvline(pd.Timestamp('2023-01-01'), color='#999', ls='--')
            ax.legend()
            ax.set_ylabel('自前高回落')
        figure('04_drawdown', 'H4 回撤（兩段分開計算）', '虛線左側為本輪 2016～2022，右側為封存 2023～2025｜' + CAPTION, daily.reset_index(), dd)
        reg = pd.DataFrame([{'regime': k, **v} for k, v in out['regimes'].items()])

        def regime(ax):
            x = np.arange(len(reg))
            ax.bar(x - .2, reg['h4_annualized'], .4, label='H4 年化')
            ax.bar(x + .2, reg['v2_annualized'], .4, label='V2 年化')
            ax.set_xticks(x, [f"{r} ({s} 日)" for r, s in zip(reg['regime'], reg['sessions'])])
            ax.axhline(0, color='#333')
            ax.legend()
        figure('06_market_regime', '2016～2022 多頭與空頭（V2 代理 200 日均線）', '前一日 V2 指數在 200 日均線上為多頭｜' + CAPTION, reg, regime)
        sector = pd.read_csv(DOCS / 'chart_data/sector_pnl_by_year.csv').set_index('year')
        top = sector.sum().sort_values(ascending=False).index[:8]

        def sec(ax):
            (sector[top] / 1e4).plot(kind='bar', stacked=True, ax=ax)
            ax.set_ylabel('淨損益（萬元，起始 100 萬）')
            ax.legend(ncol=2, fontsize=10)
        figure('07_sector_exposure', '2016～2022 產業損益（前 8 大）', '產業為 2026-08-06 現行分類，非當時分類；已下市公司列 UNCLASSIFIED｜' + CAPTION,
               sector.reset_index(), sec)
        liq = pd.read_csv(DOCS / 'chart_data/liquidity_terciles.csv')

        def lq(ax):
            ax.bar(liq.iloc[:, 0].astype(str), liq['net_pnl'] if 'net_pnl' in liq else liq.iloc[:, 1])
            ax.set_title('依 amihud 三分位的淨損益；規模（市值）：SIZE_ATTRIBUTION_NOT_VERIFIED')
        figure('08_size_liquidity', '流動性歸因（規模無法驗證）', '沒有任何白名單來源提供歷史市值或股數；成交金額不等於市值｜' + CAPTION, liq, lq)

    def annual_bars(ax):
        x = np.arange(len(annual))
        for k, (col, label) in enumerate((('h4_2x', 'H4 2x'), ('benchmark_v2', 'V2'), ('taiex_total_return', '加權報酬指數'))):
            ax.bar(x + (k - 1) * .27, annual[col], .27, label=label)
        ax.set_xticks(x, annual['year'])
        ax.axhline(0, color='#333')
        ax.legend()
    figure('03_annual_returns', '各年度報酬（2016～2022 本輪＋2023～2025 封存）', '兩段是不同的回測，不合併為同一條淨值｜' + CAPTION, annual, annual_bars)

    def alpha_year(ax):
        x = np.arange(len(annual))
        ax.bar(x - .2, annual['excess_vs_v2'], .4, label='勝過 V2')
        ax.bar(x + .2, annual['excess_vs_taiex_tr'], .4, label='勝過加權報酬指數')
        ax.set_xticks(x, annual['year'])
        ax.axhline(0, color='#333')
        ax.legend()
    figure('05_alpha_by_year', '各年度超額報酬', '年報酬相減，不是迴歸 alpha｜' + CAPTION, annual, alpha_year)

    def gate_table(ax):
        ax.axis('off')
        cols = [c for c in ('window', 'r1', 'r2', 'r3', 'r4', 'r5', 'bootstrap_lower', 'deflated_sharpe', 'top5_assets_profit_share', 'best5pct_days_return_share') if c in gates]
        cells = [[(f'{v:.3f}' if isinstance(v, float) else str(v)) for v in row] for row in gates[cols].itertuples(index=False)]
        table = ax.table(cellText=cells, colLabels=cols, loc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(11)
        table.scale(1, 2.6)
    figure('09_r1_r5_screen', 'RESEARCH-SCREEN:V2 r1～r5', 'DSR 試驗數 max（研究試驗數，743）；試驗 Sharpe 變異數沿用 H4-V2 記錄值｜' + CAPTION, gates, gate_table)
    res = out.get('result') or {}
    p = res.get('primary') or {}
    lines = [('資料准入', out['admission']['status']), ('登記判定', res.get('decision', 'BLOCKED')),
             ('2x 累積報酬', f"{p['total_return_2x']:.1%}" if p.get('total_return_2x') is not None else 'N/A'),
             ('V2 年化 alpha', f"{p['alpha_annual']:+.1%}（p {p['alpha_p_hac']:.2f}）" if p.get('alpha_annual') is not None else 'N/A'),
             ('加權報酬指數 alpha', f"{p['taiex_alpha_annual']:+.1%}（p {p['taiex_alpha_p_hac']:.2f}）" if p.get('taiex_alpha_annual') is not None else 'N/A'),
             ('最大回撤', f"{p['max_drawdown_2x']:.1%}" if p.get('max_drawdown_2x') is not None else 'N/A'),
             ('r1～r5', ' '.join(f"{g}{'✓' if ok else '✗'}" for g, ok in (p.get('gates') or {}).items()) or 'N/A'),
             ('揭露', '存活者偏差：H4 選不到日後下市公司；非獨立 OOS')]

    if not res:
        adm = out['admission']
        lines = [('歷史行情與事件', '取得完成：1,825 個週一至週五、交易日每年 241～246 日'),
                 ('財報 PIT', '公告時點規則重現 67,219 筆，0 不符；更正排除無灌水（t 0.25）'),
                 ('資料准入', 'BLOCKED（存活者缺口）'),
                 ('日後下市且無財報', f"平均 {adm['survivor_gap_share']['mean']:.1%}，最高 {adm['survivor_gap_share']['max']:.1%}（上限 2%／5%）"),
                 ('完全無財報來源', f"平均 {adm['no_fundamental_source_share']['mean']:.1%}，最高 {adm['no_fundamental_source_share']['max']:.1%}（上限 10%／15%）"),
                 ('H4 歷史回測', '未執行；未寫 Ledger；登記維持 REGISTERED_NOT_RUN'),
                 ('H4 整體建議', 'REVISE（維持）；H4-V2 登記 CONTINUE 不改寫'),
                 ('2026 OOS', '維持封存')]

    def summary(ax):
        ax.axis('off')
        for i, (k, v) in enumerate(lines):
            ax.text(.02, .9 - i * .11, k, fontsize=17, fontweight='bold')
            ax.text(.36, .9 - i * .11, v, fontsize=17)
    figure('10_research_conclusion', 'H4 2016～2022 歷史驗證結論', CAPTION, pd.DataFrame(lines, columns=['item', 'value']), summary)


def index_html(title='H4 2016～2022 歷史資料補齊與回測'):
    docs = sorted(DOCS.glob('*.md'), key=lambda p: (p.name not in ('README.md', 'h4_final_decision.md'), p.name != 'README.md', p.name))
    sections = ''.join(f"<h2 id='{p.name}'>{p.name}</h2><pre>{html.escape(p.read_text(encoding='utf-8'))}</pre>" for p in docs)
    toc = ''.join(f"<li><a href='#{p.name}'>{p.name}</a></li>" for p in docs)
    data = ''.join(f"<li><a href='{p.name}'>{p.name}</a></li>" for p in sorted(DOCS.glob('*.csv')))
    images = ''.join(f"<figure><img src='charts/{p.name}' alt='{p.stem}'><figcaption><a href='chart_data/{p.stem}.csv'>{p.stem}.csv</a></figcaption></figure>"
                     for p in sorted((DOCS / 'charts').glob('*.png')))
    page = ("<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><title>H4 historical data unlock</title>"
            "<style>body{font-family:'Microsoft JhengHei',sans-serif;margin:24px;max-width:1500px}pre{white-space:pre-wrap;background:#f6f6f6;padding:12px}"
            "figure{display:inline-block;width:48%;margin:4px;vertical-align:top}img{width:100%}</style></head><body>"
            f"<h1>{title}</h1><p>RESEARCH_UNCERTIFIED / DIAGNOSTIC_ONLY。未使用 2026 資料。</p>"
            f"<h2>報告</h2><ul>{toc}</ul><h2>資料</h2><ul>{data}</ul><h2>圖表</h2>{images}{sections}</body></html>")
    (DOCS / 'index.html').write_text(page, encoding='utf-8')


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else 'main'
    result = {'main': main, 'index': index_html}[command]()
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str)[:6000] if result is not None else 'ok')
