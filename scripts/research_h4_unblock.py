"""h4-validation-unblock-v1: why H4 signal-day members lack G2/G3 in 2016-2022, and the smallest recovery that would pass admission.

Reads only local data (prepared_hist_v1, v3.1/v3.3 fundamentals, cached MOPS summaries, delisting list). No network, no strategy returns."""
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.research_h4_hist_run import DELIST, FUND, PREPARED, RAW_HIST, RAW_LOCAL, SAME_BASIS, SIGNALS, load, roc

OUT = ROOT / 'outputs/research_mode/h4_unblock_v1'
DOCS = ROOT / 'docs/research/2026-11-05-h4-validation-unblock-v1'
MOPS = ROOT / 'outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z/financial_cache'
LIMITS = {'survivor_gap_mean': .02, 'survivor_gap_max': .05, 'no_source_mean': .10, 'no_source_max': .15}


def members():
    """Signal-day members with the reason each one has or lacks a growth score."""
    universe = pd.DataFrame(load(PREPARED / 'universe.json'))
    schedule = load(SIGNALS / 'signals_manifest.json')['signal_days']['60']
    m = universe.loc[universe['member'] & universe['asof_date'].isin(schedule), ['asof_date', 'asset_id']].copy()
    m['asset_id'] = m['asset_id'].astype(str)
    source = set(pd.read_parquet(SAME_BASIS / 'fundamental_records.parquet', columns=['ticker'])['ticker'].astype(str))
    in_mops = set()
    for path in MOPS.glob('mops_t163sb04_*_sii.parquet'):
        in_mops |= set(pd.read_parquet(path, columns=['stock_id'])['stock_id'].astype(str))
    industry = {r['公司代號']: r['產業別'] for r in load(RAW_LOCAL / 't187ap03_L.json')}
    delisted = {str(r['Code']): roc(r['DelistingDate']) for r in load(DELIST) if len(str(r['Code'])) == 4}
    matrix = pd.read_parquet(FUND / 'fundamental_matrix.parquet', columns=['asof_date', 'ticker', 'operating_income_yoy', 'eps_yoy'])
    matrix['asof_date'] = pd.to_datetime(matrix['asof_date']).dt.strftime('%Y-%m-%d')
    matrix['ticker'] = matrix['ticker'].astype(str)
    m = m.merge(matrix, left_on=['asof_date', 'asset_id'], right_on=['asof_date', 'ticker'], how='left').drop(columns='ticker')
    m['delisted_on'] = m['asset_id'].map(delisted)
    m['later_delisted'] = m['delisted_on'].notna() & (m['delisted_on'] > m['asof_date'])
    m['industry_2026'] = m['asset_id'].map(industry)
    names = stock_names()
    m['name'] = m['asset_id'].map(names)
    # financial: 2026 industry 17; for companies no longer listed, the official name (bank, holding, life, securities, insurance, bills)
    m['financial'] = m['industry_2026'].eq('17') | (m['industry_2026'].isna() & m['name'].fillna('').str.contains('銀|金控|壽|證|產險|保險|票券|新光金|開發金'))
    m['no_source'] = ~m['asset_id'].isin(source)
    m['in_mops_cache'] = m['asset_id'].isin(in_mops)
    m['scored_growth'] = m['operating_income_yoy'].notna() | m['eps_yoy'].notna()

    def reason(r):
        if r.scored_growth:
            return 'HAS_GROWTH'
        if not r.no_source:
            return 'SOURCE_BUT_QUARTER_MISSING_EXCLUDED_OR_UNVERIFIED'
        if r.later_delisted:
            return 'NO_SOURCE_LATER_DELISTED'
        if r.industry_2026 == '17':
            return 'NO_SOURCE_FINANCIAL_STRUCTURE'
        if r.in_mops_cache:
            return 'NO_SOURCE_VALUES_CACHED_OUTSIDE_890_TARGET'
        return 'NO_SOURCE_NOT_IN_ANY_LOCAL_SOURCE'
    m['reason'] = [reason(r) for r in m.itertuples(index=False)]
    m['survivor_gap'] = m['no_source'] & m['later_delisted']
    return m


def stock_names():
    """Stock names from the first fetched quote file of each year (official MI_INDEX closing table)."""
    names = {}
    for year in range(2015, 2022):
        path = next(iter(sorted(RAW_HIST.glob(f'MI_INDEX_{year}01*.json'))[2:3]), None)
        for table in (load(path).get('tables') or []) if path else []:
            if table and '每日收盤行情' in str(table.get('title', '')):
                names.update({r[0]: r[1] for r in table['data']})
    return names


def daily(m, recovered=()):
    """Admission shares per signal day if the given tickers had a fundamental source."""
    rec = m['asset_id'].isin(set(recovered))
    g = m.assign(no_source=m['no_source'] & ~rec, survivor_gap=m['survivor_gap'] & ~rec).groupby('asof_date')
    d = pd.DataFrame({'members': g.size(), 'no_source': g['no_source'].sum(), 'survivor_gap': g['survivor_gap'].sum()})
    d['no_source_share'], d['survivor_gap_share'] = d['no_source'] / d['members'], d['survivor_gap'] / d['members']
    return d


def passes(d):
    return {'survivor_gap_mean': d['survivor_gap_share'].mean() <= LIMITS['survivor_gap_mean'],
            'survivor_gap_max': d['survivor_gap_share'].max() <= LIMITS['survivor_gap_max'],
            'no_source_mean': d['no_source_share'].mean() <= LIMITS['no_source_mean'],
            'no_source_max': d['no_source_share'].max() <= LIMITS['no_source_max']}


def minimal_recovery(m, financial=True):
    """Greedy: add the no-source company that removes the most failing member-days until all four limits pass."""
    pool = m.loc[m['no_source'] & (financial | ~m['financial'])]
    candidates = pool.groupby('asset_id').size().sort_values(ascending=False)
    chosen, d = [], daily(m)
    while not all(passes(d).values()) and len(chosen) < len(candidates):
        def score(t):
            x = daily(m, chosen + [t])
            return (x['survivor_gap_share'].clip(lower=0).sub(LIMITS['survivor_gap_max']).clip(lower=0).sum() * 10
                    + max(0, x['survivor_gap_share'].mean() - LIMITS['survivor_gap_mean']) * 100
                    + x['no_source_share'].sub(LIMITS['no_source_max']).clip(lower=0).sum() * 10
                    + max(0, x['no_source_share'].mean() - LIMITS['no_source_mean']) * 100)
        best = min((t for t in candidates.index if t not in chosen), key=score)
        chosen.append(best)
        d = daily(m, chosen)
    return chosen, d


def main():
    for p in (OUT, DOCS):
        p.mkdir(parents=True, exist_ok=True)
    m = members()
    m.to_csv(OUT / 'signal_day_member_reasons.csv', index=False, encoding='utf-8-sig')
    base = daily(m)
    chosen, after = minimal_recovery(m, financial=False)  # H4-V2 never scored financials; recovery stays within its universe
    chosen_any, after_any = minimal_recovery(m)
    # cached-values scenario: every NO_SOURCE company whose values are already in the local MOPS cache gets a source
    cached = sorted(set(m.loc[m['reason'] == 'NO_SOURCE_VALUES_CACHED_OUTSIDE_890_TARGET', 'asset_id']))
    cached_d = daily(m, cached)
    per = m.loc[~m['scored_growth']].groupby(['asset_id', 'reason']).agg(
        signal_member_days=('asof_date', 'size'), first_signal=('asof_date', 'min'), last_signal=('asof_date', 'max'),
        delisted_on=('delisted_on', 'first'), industry_2026=('industry_2026', 'first'), name=('name', 'first'), financial=('financial', 'first'),
        in_mops_cache=('in_mops_cache', 'first')).reset_index()
    for y in range(2016, 2023):
        per[f'days_{y}'] = per['asset_id'].map(m.loc[~m['scored_growth'] & m['asof_date'].str.startswith(str(y))].groupby('asset_id').size()).fillna(0).astype(int)
    rank = {t: i + 1 for i, t in enumerate(chosen)}
    per['minimal_recovery_rank'] = per['asset_id'].map(rank)
    # quarters a recovered company needs: FY of the first signal minus one (YoY base) through the last quarter before its last signal
    per['quarters_needed'] = [f"{int(r.first_signal[:4]) - 1}Q1..{r.last_signal[:4]}Q3" if r.reason.startswith('NO_SOURCE') else ''
                              for r in per.itertuples(index=False)]
    per = per.sort_values(['minimal_recovery_rank', 'signal_member_days'], ascending=[True, False], na_position='last')
    per.to_csv(DOCS / 'missing_fundamental_inventory.csv', index=False, encoding='utf-8-sig')
    delisted = per.loc[per['reason'] == 'NO_SOURCE_LATER_DELISTED'].copy()
    delisted['recovery_status'] = 'NOT_RECOVERED'
    delisted['local_sources_checked'] = 'v3.1 records; v3 t57sb01 publication cache; MOPS t163sb04 sii cache 103-111; research_universe.parquet'
    delisted['why_not_recovered'] = 'absent from every local source; MOPS t163sb04 summaries omit delisted companies; per-company filing lists need a new fetch job'
    delisted.to_csv(DOCS / 'delisted_company_recovery.csv', index=False, encoding='utf-8-sig')
    adm = base.join(after.add_prefix('after_minimal_'), how='left')
    adm.reset_index().to_csv(DOCS / 'historical_admission_daily.csv', index=False, encoding='utf-8-sig')
    counts = m.groupby([m['asof_date'].str[:4], 'reason']).size().unstack(fill_value=0)
    (DOCS / 'chart_data').mkdir(exist_ok=True)
    counts.reset_index(names='year').to_csv(DOCS / 'chart_data/missing_by_year.csv', index=False, encoding='utf-8-sig')
    summary = {'label': 'DIAGNOSTIC_ONLY', 'signal_days': int(len(base)), 'member_days': int(len(m)),
               'reason_counts': m['reason'].value_counts().to_dict(),
               'current': {'survivor_gap_mean': base['survivor_gap_share'].mean(), 'survivor_gap_max': base['survivor_gap_share'].max(),
                           'no_source_mean': base['no_source_share'].mean(), 'no_source_max': base['no_source_share'].max(),
                           'max_days': {'survivor_gap': base['survivor_gap_share'].idxmax(), 'no_source': base['no_source_share'].idxmax()},
                           'passes': passes(base)},
               'cached_values_scenario': {'companies': len(cached), 'survivor_gap_mean': cached_d['survivor_gap_share'].mean(),
                                          'survivor_gap_max': cached_d['survivor_gap_share'].max(), 'no_source_mean': cached_d['no_source_share'].mean(),
                                          'no_source_max': cached_d['no_source_share'].max(), 'passes': passes(cached_d)},
               'minimal_recovery': {'companies': len(chosen), 'tickers': chosen,
                                    'later_delisted_among_them': int(per.loc[per['asset_id'].isin(chosen), 'reason'].eq('NO_SOURCE_LATER_DELISTED').sum()),
                                    'after': {'survivor_gap_mean': after['survivor_gap_share'].mean(), 'survivor_gap_max': after['survivor_gap_share'].max(),
                                              'no_source_mean': after['no_source_share'].mean(), 'no_source_max': after['no_source_share'].max()},
                                    'passes': passes(after)},
               'minimal_recovery_if_financials_allowed': {'companies': len(chosen_any), 'tickers': chosen_any, 'passes': passes(after_any),
                                                          'financial_among_them': int(m.loc[m['asset_id'].isin(chosen_any)].groupby('asset_id')['financial'].first().sum())},
               'later_delisted_no_source_companies': int(delisted['asset_id'].nunique()),
               'later_delisted_no_source_financial': int(delisted['financial'].sum())}
    (OUT / 'inventory_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    return summary


def charts():
    """Eight charts; every H4 historical performance chart is NOT_RUN because admission is DATA_BLOCKED."""
    import numpy as np
    from scripts import research_h4_hist_report as rep
    rep.DOCS = DOCS
    cap = 'RESEARCH_UNCERTIFIED／DIAGNOSTIC_ONLY｜未使用 2026 資料'
    counts = pd.read_csv(DOCS / 'chart_data/missing_by_year.csv').set_index('year')
    shares = counts.div(counts.sum(axis=1), axis=0).drop(columns='HAS_GROWTH')

    def missing(ax):
        (shares * 100).plot(kind='bar', stacked=True, ax=ax)
        ax.set_ylabel('占訊號日成員（%）')
        ax.legend(fontsize=10)
    rep.figure('01_missing_data_by_year', '訊號日成員缺少成長分數的原因', '來源：prepared_hist_v1、v3.1／v3.3 財報、本地 MOPS 快取｜' + cap,
               shares.reset_index(), missing)
    adm = pd.read_csv(DOCS / 'historical_admission_daily.csv', parse_dates=['asof_date']).set_index('asof_date')

    def surv(axes):
        for ax, col, lim, title in ((axes[0], 'survivor_gap_share', (LIMITS['survivor_gap_mean'], LIMITS['survivor_gap_max']), '日後下市且無財報'),
                                    (axes[1], 'no_source_share', (LIMITS['no_source_mean'], LIMITS['no_source_max']), '完全沒有財報來源')):
            ax.plot(adm.index, adm[col] * 100, marker='o', label='目前資料')
            ax.plot(adm.index, adm['after_minimal_' + col] * 100, marker='x', ls='--', label='若補齊最小 15 家（尚未取得）')
            ax.axhline(lim[0] * 100, color='#C44E52', ls=':', label=f'平均上限 {lim[0]:.0%}')
            ax.axhline(lim[1] * 100, color='#C44E52', label=f'單日上限 {lim[1]:.0%}')
            ax.set_title(title)
            ax.set_ylabel('%')
            ax.legend(fontsize=10)
    rep.figure('02_survivorship_bias_coverage', '准入缺口：目前與最小補件情境', '虛線為假設情境，不是已取得的資料｜' + cap, adm.reset_index(), surv, 2)
    for name, title in (('03_h4_vs_benchmark', 'H4 與基準'), ('04_annual_returns', '各年度報酬'), ('05_drawdown', '回撤'), ('06_alpha_by_year', '各年度 alpha')):
        def draw(ax):
            ax.axis('off')
            ax.text(.5, .58, 'NOT_RUN', ha='center', fontsize=54, color='#999')
            ax.text(.5, .4, '2016～2022 H4 回測未執行：資料准入 DATA_BLOCKED（下市公司財報未取得）', ha='center', fontsize=15)
        rep.figure(name, title + '（2016～2022）', 'NOT_RUN：沒有任何策略績效｜' + cap, pd.DataFrame({'status': ['NOT_RUN']}), draw)
    contrib = pd.read_csv(ROOT / 'docs/research/2026-11-04-h4-attribution-reliability-v1/h4_stock_contribution.csv', dtype={'stock': str})
    gains = contrib.loc[contrib['net_pnl'] > 0, 'net_pnl'].sort_values(ascending=False)
    cum = (gains.cumsum() / contrib['net_pnl'].sum()).reset_index(drop=True)

    def conc(axes):
        axes[0].plot(np.arange(1, len(cum) + 1), cum * 100, marker='.')
        axes[0].axvline(5, color='#999', ls=':')
        axes[0].axhline(50, color='#C44E52', label='r4 上限 50%')
        axes[0].set_xlabel('獲利個股（由大到小）')
        axes[0].set_ylabel('占全部淨利（%）')
        axes[0].set_title('2023～2025 個股獲利集中（封存 H4-V2）')
        axes[0].legend()
        axes[1].axis('off')
        axes[1].text(.5, .55, '2016～2022：NOT_RUN', ha='center', fontsize=30, color='#999')
    rep.figure('07_profit_concentration', '獲利集中度', '前 5 檔占淨利 52.5%、最佳 5% 交易日占超額報酬 138.6%（2023～2025）｜' + cap,
               pd.DataFrame({'rank': np.arange(1, len(cum) + 1), 'cumulative_share_of_net': cum}), conc, 2)
    sealed = load(ROOT / 'outputs/research_mode/report_h4_v2/h4_result.json')['primary']
    v, g = sealed['values'], sealed['gates']
    status = {k: 'PASS' if ok else 'FAIL' for k, ok in g.items()}
    rows = [('r1 淨與超額 Sharpe > 0', '{:.2f} / {:.2f}'.format(v['net_sharpe_2x'], v['excess_sharpe_2x']), status['r1']),
            ('r2 Bootstrap 下界 > 0', '{:.2f}'.format(v['bootstrap_lower']), status['r2']),
            ('r3 DSR > 0.95（743 次）', '{:.4f}'.format(v['deflated_sharpe']), status['r3']),
            ('r4 前 5 檔與最佳 5% 日 < 50%', '{:.1%} / {:.1%}'.format(v['top5_assets_profit_share'], v['best5pct_days_return_share']), status['r4']),
            ('r5 多空各 ≥ 60 日且非皆負', 'bull {} / bear {} sessions'.format(v['regime']['bull']['sessions'], v['regime']['bear']['sessions']), status['r5'])]
    table = pd.DataFrame(rows, columns=['gate', 'value_2023_2025', 'status_2023_2025'])
    table['status_2016_2022'] = 'NOT_RUN'

    def gates(ax):
        ax.axis('off')
        t = ax.table(cellText=table.values, colLabels=['門檻', '2023～2025 數值', '2023～2025', '2016～2022'], loc='center')
        t.auto_set_font_size(False)
        t.set_fontsize(14)
        t.scale(1, 3)
    rep.figure('08_r1_r5_status', 'RESEARCH-SCREEN:V2 r1～r5', '2023～2025 為封存值；2016～2022 未執行｜' + cap, table, gates)
    rep.index_html('H4 驗證解鎖專項（2026-11-05）')


if __name__ == '__main__':
    command = sys.argv[1] if len(sys.argv) > 1 else 'main'
    print(json.dumps({'main': main, 'charts': charts}[command](), ensure_ascii=False, indent=1, default=str))
