"""H4 2016-2022 historical validation: registration, data admission, v3.2 correction-exclusion audit and offline diagnostics.

No fetch, no backtest, no ledger row, no 2026 data. The validation itself stays REGISTERED_NOT_RUN until its data is admitted."""
import collections
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

RM = ROOT / 'outputs/research_mode'
RAW = ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd'
DOCS = ROOT / 'docs/research/2026-11-04-h4-historical-validation-v1'
OUT = RM / 'h4_historical_v1'
DESIGN = ROOT / 'openspec/changes/2026-11-04-h4-historical-validation-v1/design.md'
HIST_ID = 'H4:HIST-2016-2022-V1'
SAME_BASIS = ROOT / 'data/processed/fundamental_pit_v3_yoy_same_basis'
CAPTION = 'RESEARCH_UNCERTIFIED／DIAGNOSTIC_ONLY'
INDUSTRY = {'01': '水泥', '02': '食品', '03': '塑膠', '04': '紡織纖維', '05': '電機機械', '06': '電器電纜', '08': '玻璃陶瓷', '09': '造紙',
            '10': '鋼鐵', '11': '橡膠', '12': '汽車', '14': '建材營造', '15': '航運', '16': '觀光餐旅', '17': '金融保險', '18': '貿易百貨',
            '20': '其他', '21': '化學', '22': '生技醫療', '23': '油電燃氣', '24': '半導體', '25': '電腦及週邊設備', '26': '光電',
            '27': '通信網路', '28': '電子零組件', '29': '電子通路', '30': '資訊服務', '31': '其他電子', '35': '綠能環保', '36': '數位雲端',
            '37': '運動休閒', '38': '居家生活'}


def register():
    from core.runtime.operation_policy import ExecutionMode, current_mode
    if current_mode() is not ExecutionMode.RESEARCH:
        raise PermissionError('RESEARCH_MODE_REQUIRED')
    grid = ROOT / 'outputs/strategy_research/h4_growth_momentum_v1/strategy_grid.csv'
    return workflow.register({
        'hypothesis_id': HIST_ID, 'strategy': 'H4_GROWTH_MOMENTUM',
        'hypothesis': ('The frozen H4:GROWTH-MOMENTUM-V2 primary configuration (Top10, 60-session rebalance, equal weight, 0.5 growth + 0.5 '
                       'near_high_252d, V6 assumptions, 2x cost, RESEARCH-SCREEN:V2) also earns positive alpha from the first 2016 signal '
                       'date to 2022-12-30. The strategy was chosen after seeing 2023-2025 results; this is an additional historical check, '
                       'not independent out-of-sample evidence.'),
        'falsification_test': ('Runs only when official daily quotes, membership, corporate events (from 2015-01-01) and v3.2-rule '
                               'G2/G3 filings (from FY2014) are admitted; otherwise REGISTERED_NOT_RUN and BLOCKED. Supported when the '
                               'primary 2x alpha on V2 > 0 and r1 passes; not supported when alpha <= 0. Years beating V2 are all reported.'),
        'data_window': '2015-01-01 warm-up, 2016-01-04 to 2022-12-30 evaluation; no 2026 data', 'trial_budget': '1',
        'source': f'design.md sha256 {workflow.sha256(DESIGN)}; strategy_grid.csv sha256 {workflow.sha256(grid)}'})


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def coverage(records, audit_hist):
    """Per calendar or fiscal year: local official quotes, momentum warm-up, G2/G3 filings and their verification share."""
    files = collections.Counter(p.name[9:13] for p in RAW.glob('MI_INDEX_*.json'))
    bars = load(RM / 'prepared_v6/bars.json')['bars']
    stocks = {y: len({s for d, day in bars.items() if d[:4] == y for s in day}) for y in {d[:4] for d in bars}}
    yoy = records.loc[records['metric'].isin(['operating_income_yoy', 'eps_yoy'])]
    status = audit_hist.set_index(['ticker', 'fiscal_year', 'quarter'])['verification_status']
    rows = []
    for year in range(2014, 2023):
        part = yoy.loc[yoy['fiscal_year'] == year]
        cur = status.reindex(pd.MultiIndex.from_arrays([part['ticker'], part['fiscal_year'], part['quarter']])).to_numpy()
        pri = status.reindex(pd.MultiIndex.from_arrays([part['ticker'], part['fiscal_year'] - 1, part['quarter']])).to_numpy()
        both = (cur == 'VERIFIED') & (pri == 'VERIFIED')
        audited = audit_hist.loc[audit_hist['fiscal_year'] == year, 'verification_status']
        rows.append({'year': year, 'official_quote_files_local': files.get(str(year), 0), 'stocks_with_prices': stocks.get(str(year), 0),
                     'momentum_252d_available': False,
                     'g2_tickers': int(part.loc[part['metric'] == 'operating_income_yoy', 'ticker'].nunique()),
                     'g3_tickers': int(part.loc[part['metric'] == 'eps_yoy', 'ticker'].nunique()),
                     'filings_audited': len(audited), 'filings_verified_share': float((audited == 'VERIFIED').mean()) if len(audited) else None,
                     'yoy_rows': len(part), 'yoy_rows_both_filings_verified_share': float(both.mean()) if len(part) else None,
                     'status': 'BLOCKED_NO_PRICES' if files.get(str(year), 0) < 200 else 'BLOCKED_MOMENTUM_WARMUP'})
    return pd.DataFrame(rows)


def correction_bias(records, audit21):
    """Development-window test: forward 60-session returns of member stock-days whose G2 comes from a later-corrected filing."""
    from scripts.prepare_h1_signals import frames_of
    from scripts.research_validation_v2 import _prepared
    bars, _, _ = _prepared()
    close = frames_of({'price_basis': 'RAW', 'bars': bars})['close']
    fwd = (close.shift(-60) / close - 1).stack().rename('fwd60').reset_index()
    fwd.columns = ['asof_date', 'ticker', 'fwd60']
    universe = pd.DataFrame(load(RM / 'prepared_v6/universe.json'))
    members = universe.loc[universe['member'], ['asof_date', 'asset_id']].rename(columns={'asset_id': 'ticker'})
    members['asof_date'] = pd.to_datetime(members['asof_date'])
    g2 = records.loc[records['metric'].eq('operating_income_yoy') & records['value'].notna()].copy()
    g2['available_date'] = pd.to_datetime(g2['available_date'])
    g2 = g2.sort_values(['available_date', 'publication_date', 'revision_id'], kind='stable')
    left = members.sort_values('asof_date', kind='stable')
    joined = pd.merge_asof(left, g2[['ticker', 'available_date', 'value', 'fiscal_year', 'quarter']], left_on='asof_date', right_on='available_date',
                           by='ticker', direction='backward').dropna(subset=['value'])
    status = audit21.set_index(['ticker', 'fiscal_year', 'quarter'])['verification_status']
    cur = status.reindex(pd.MultiIndex.from_arrays([joined['ticker'], joined['fiscal_year'].astype(int), joined['quarter'].astype(int)])).to_numpy()
    pri = status.reindex(pd.MultiIndex.from_arrays([joined['ticker'], joined['fiscal_year'].astype(int) - 1, joined['quarter'].astype(int)])).to_numpy()
    joined['flagged'] = (cur == 'EXCLUDED') | (pri == 'EXCLUDED')
    data = joined.merge(fwd, on=['asof_date', 'ticker']).dropna(subset=['fwd60'])
    data['g2_quintile'] = data.groupby('asof_date')['value'].transform(lambda v: np.ceil(v.rank(pct=True) * 5).clip(1, 5))
    daily = data.groupby(['asof_date', 'flagged'])['fwd60'].mean().unstack().dropna()
    diff = daily[True] - daily[False]
    sparse = diff.iloc[::60]  # non-overlapping 60-session horizons
    by_q = data.groupby(['g2_quintile', 'flagged'])['fwd60'].mean().unstack().rename(columns={True: 'flagged', False: 'other'})
    by_q['difference'] = by_q['flagged'] - by_q['other']
    return {'member_stock_days': len(data), 'flagged_share': float(data['flagged'].mean()),
            'mean_fwd60_flagged': float(data.loc[data.flagged, 'fwd60'].mean()), 'mean_fwd60_other': float(data.loc[~data.flagged, 'fwd60'].mean()),
            'daily_difference_mean': float(diff.mean()), 'nonoverlap_difference_mean': float(sparse.mean()),
            'nonoverlap_t': float(sparse.mean() / (sparse.std(ddof=1) / np.sqrt(len(sparse)))), 'nonoverlap_n': len(sparse),
            'last_label_date': str(data['asof_date'].max().date())}, by_q.reset_index()


def taiex():
    """TWSE total-return (and price) index closes from the local official MI_INDEX files, 2022-12-30 to 2025-12-31."""
    names = {'發行量加權股價報酬指數': 'taiex_total_return', '發行量加權股價指數': 'taiex_price'}
    rows = {}
    for path in sorted(RAW.glob('MI_INDEX_*.json')):
        day = path.name[9:17]
        if not '20221230' <= day <= '20251231':
            continue
        values = {}
        for table in load(path).get('tables') or []:
            for row in table.get('data') or []:
                if row and row[0] in names and names[row[0]] not in values:
                    values[names[row[0]]] = float(str(row[1]).replace(',', ''))
        rows[pd.Timestamp(day)] = values
    return pd.DataFrame(rows).T.sort_index()


def run():
    from core.research.attribution import run_attribution
    from scripts.research_fundamental_pit_recovery import audit
    from scripts.research_h4_attribution import RUNS, nav_returns, tracks
    OUT.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / 'chart_data').mkdir(exist_ok=True)
    records = pd.read_parquet(SAME_BASIS / 'fundamental_records.parquet')
    records['ticker'] = records['ticker'].astype(str)
    audit_hist = audit(records, since=2014)
    audit_hist.to_csv(OUT / 'publication_revision_audit_fy2014.csv', index=False, encoding='utf-8-sig')
    cover = coverage(records, audit_hist)
    cover.to_csv(DOCS / 'data_coverage_by_year.csv', index=False, encoding='utf-8-sig')
    audit21 = pd.read_csv(ROOT / 'data/processed/fundamental_pit_v3_2_verified/publication_revision_audit.csv', dtype={'ticker': str})
    bias, by_q = correction_bias(records, audit21)
    by_q.to_csv(DOCS / 'chart_data/12_correction_bias.csv', index=False, encoding='utf-8-sig')
    index = taiex()
    index.to_csv(OUT / 'taiex_index_levels.csv', encoding='utf-8-sig')
    tr = index['taiex_total_return'].pct_change().dropna()
    bench = pd.read_csv(RM / 'validation_execution_v1/benchmark_series.csv', index_col='date', parse_dates=True)['v2_total'].loc['2023-01-03':]
    net = {n: nav_returns(tracks(n)[0]['stress'])[0] for n in ('H4', 'H1')}
    reg = {n: {k: v for k, v in run_attribution(n, net[n], tr).items() if k in ('alpha', 'alpha_p_hac', 'beta', 'r_squared', 'n_obs')} for n in net}
    for n in reg:
        reg[n]['alpha_annual'] = reg[n].pop('alpha') * 252
    years = {}
    for name, series in (('H4', net['H4']), ('H1', net['H1']), ('V2 等權含息', bench), ('加權報酬指數', tr.loc['2023-01-03':])):
        for y, g in series.groupby(series.index.year):
            years.setdefault(int(y), {})[name] = float((1 + g).prod() - 1)
    industry_map = {r['公司代號']: r['產業別'] for r in load(RAW / 't187ap03_L.json')}
    stock = pd.read_csv(ROOT / 'docs/research/2026-11-04-h4-attribution-reliability-v1/h4_stock_contribution.csv', dtype={'stock': str})
    stock['industry_code'] = stock['stock'].map(industry_map).fillna('UNCLASSIFIED')
    stock['industry'] = stock['industry_code'].map(lambda c: f'{c} {INDUSTRY.get(c, "")}'.strip())
    ind = stock.groupby('industry').agg(stocks=('stock', 'size'), net_pnl=('net_pnl', 'sum'), pnl_2025=('pnl_2025', 'sum'),
                                       gross_profit=('net_pnl', lambda v: v[v > 0].sum())).sort_values('net_pnl', ascending=False)
    ind['share_of_net'] = ind['net_pnl'] / stock['net_pnl'].sum()
    ind['share_of_gross'] = ind['gross_profit'] / stock.loc[stock.net_pnl > 0, 'net_pnl'].sum()
    ind.reset_index().to_csv(DOCS / 'chart_data/07_sector_attribution.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame([{'year': y, 'status': 'BLOCKED', 'H4': 'NOT_AVAILABLE', 'V2': 'NOT_AVAILABLE', 'source': 'H4:HIST-2016-2022-V1 not run'} for y in range(2016, 2023)]
                 + [{'year': y, 'status': 'H4-V2 archived', 'H4': v['H4'], 'V2': v['V2 等權含息'], 'source': 'outputs/research_mode/report_h4_v2'}
                    for y, v in sorted(years.items())]).to_csv(DOCS / 'h4_annual_returns.csv', index=False, encoding='utf-8-sig')
    primary = load(RM / 'report_h4_v2/h4_result.json')['primary']
    r15 = [{'period': '2016-2022', 'status': 'BLOCKED', **{g: 'NOT_AVAILABLE' for g in ('r1', 'r2', 'r3', 'r4', 'r5')}, 'alpha_annual': 'NOT_AVAILABLE'},
           {'period': '2023-2025', 'status': 'H4-V2 archived', **primary['gates'], 'alpha_annual': primary['alpha_annual'],
            **{k: primary['values'][k] for k in ('net_sharpe_2x', 'excess_sharpe_2x', 'bootstrap_lower', 'deflated_sharpe', 'top5_assets_profit_share', 'best5pct_days_return_share')}}]
    pd.DataFrame(r15).to_csv(DOCS / 'h4_r1_r5_results.csv', index=False, encoding='utf-8-sig')
    bars_years = cover[['year', 'official_quote_files_local', 'stocks_with_prices', 'momentum_252d_available', 'status']]
    bars_years.assign(delisting_and_suspension_terms='2022-01-01 onward only (T1B window)', events_source='TWT49U local cache from 2022').to_csv(
        DOCS / 'historical_market_universe.csv', index=False, encoding='utf-8-sig')
    from core.runtime import public_data, research_budget
    ctx = public_data.context('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX', params={'date': '20160104', 'type': 'ALL', 'response': 'json'},
                              purpose='market_research')
    try:
        research_budget.check(ctx.job)
        fetch = 'ALLOWED'
    except Exception as exc:  # the expected outcome: no approved request plan
        fetch = f'{type(exc).__name__}: {exc}'
    summary = {'label': 'DIAGNOSTIC_ONLY', 'hypothesis_id': HIST_ID, 'historical_validation': 'BLOCKED', 'fetch_check_2016_mi_index': fetch,
               'coverage': cover.to_dict('records'), 'correction_bias': bias, 'taiex_regression': reg, 'years': years,
               'industry_top': ind.head(6).reset_index().to_dict('records'), 'industry_unclassified': int((stock.industry_code == 'UNCLASSIFIED').sum()),
               'industry_snapshot': 't187ap03_L.json printed 1150806 (2026-08-06): CURRENT_CLASSIFICATION_NOT_PIT',
               'research_ledger_rows': len(workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER)),
               'inputs_sha256': {p.name: workflow.sha256(p) for p in (SAME_BASIS / 'fundamental_records.parquet', RAW / 't187ap03_L.json',
                                                                       RM / 'report_h4_v2/h4_result.json', DESIGN)}}
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    charts(summary, cover, by_q, ind, net, bench, tr)
    return summary


def charts(summary, cover, by_q, ind, net, bench, tr):
    from scripts.research_failure_review import _figure
    blocked = '2016～2022 歷史回測未執行：本地官方行情只從 2021-12-30 起，抓取邊界對 MI_INDEX 無核准請求計畫（BUDGET_UNASSIGNED）'

    def placeholder(name, title):
        def draw(ax):
            ax.axis('off')
            ax.text(.5, .58, 'BLOCKED / NOT_AVAILABLE', ha='center', fontsize=40, color='#999')
            ax.text(.5, .4, blocked, ha='center', fontsize=13, wrap=True)
        _figure(name, title, CAPTION, pd.DataFrame({'status': ['BLOCKED'], 'reason': [blocked]}), draw, out=DOCS)
    for name, title in (('01_historical_equity_curve', '2016～2022 累積報酬'), ('02_annual_return_comparison', '2016～2022 年度報酬比較'),
                        ('03_historical_drawdown', '2016～2022 回撤'), ('04_alpha_by_year', '2016～2022 各年 alpha'),
                        ('05_market_regime', '2016～2022 多空環境表現'), ('09_r1_r5_comparison', '2016～2022 r1～r5')):
        placeholder(name, title)
    curve = pd.DataFrame({'H4 主要設定（2x 成本後）': (1 + net['H4']).cumprod(), 'V2 等權含息代理': (1 + bench).cumprod(),
                          '發行量加權股價報酬指數': (1 + tr.loc['2023-01-03':]).cumprod()}).dropna()

    def size(ax):
        for c, color in zip(curve, ('#c0392b', '#222', '#2471a3')):
            ax.plot(curve.index, curve[c], color=color, lw=2.5, label=c)
        ax.set_ylabel('淨值（起始 1.0）')
        ax.legend()
    _figure('06_size_exposure', '市值加權與等權基準：2023～2025（規模歸因 NOT_VERIFIED）',
            '加權報酬指數為官方含息、市值加權、不扣成本、不可直接投資｜無歷史市值，無法做個股規模歸因｜' + CAPTION,
            curve.reset_index(names='date'), size, out=DOCS)

    def sector(ax):
        top = ind.head(12)
        ax.barh(top.index[::-1], top['net_pnl'][::-1], color='#c0392b')
        ax.set_xlabel('H4 2023～2025 淨損益（新台幣）')
    _figure('07_sector_attribution', 'H4 產業損益（現行分類，非當時分類）', '產業別取自 2026-08-06 公司基本資料：CURRENT_CLASSIFICATION_NOT_PIT｜' + CAPTION,
            ind.reset_index(), sector, out=DOCS)
    liq = pd.read_csv(ROOT / 'docs/research/2026-11-04-h4-attribution-reliability-v1/h4_liquidity_attribution.csv')
    pivot = liq.pivot(index='strategy', columns='tercile', values='net_share')[['T1_LIQUID', 'T2_MID', 'T3_ILLIQUID', 'UNRANKED']]

    def liquidity(ax):
        pivot.plot.barh(stacked=True, ax=ax)
        ax.set_xlabel('淨利占比（依當日 amihud 三分位，2023～2025）')
    _figure('08_liquidity_attribution', 'H4 流動性歸因（2023～2025，沿用上一輪封存帳本）', '歷史期間無法計算｜' + CAPTION, pivot.reset_index(), liquidity, out=DOCS)
    cov = cover.set_index('year')

    def coverage_chart(axes):
        axes[0].bar(cov.index.astype(str), cov['official_quote_files_local'], color='#7f8c8d')
        axes[0].axhline(240, color='r', ls='--', label='約一年的交易日')
        axes[0].set_ylabel('本地官方日行情檔數')
        axes[0].legend()
        axes[1].bar(cov.index.astype(str), cov['yoy_rows_both_filings_verified_share'], color='#2471a3')
        axes[1].set_ylabel('G2／G3 YoY 列中兩期申報皆 VERIFIED 的比例')
        axes[1].set_ylim(0, 1)
    _figure('11_data_coverage', '歷史資料覆蓋（行情與財報）', '財報依官方申報清單稽核｜' + CAPTION, cover, coverage_chart, ncols=2, out=DOCS)
    b = summary['correction_bias']

    def bias(ax):
        by_q.set_index('g2_quintile')[['flagged', 'other']].plot.bar(ax=ax, rot=0)
        ax.set_xlabel('G2 五分位（每日成員內）')
        ax.set_ylabel('平均 60 日前瞻報酬')
    _figure('12_correction_bias', 'v3.2 更正排除的選擇偏差檢定（2023～2025）',
            f"日後被標記更正的申報占成員日 {b['flagged_share']:.1%}；非重疊差 {b['nonoverlap_difference_mean']:+.2%}（t {b['nonoverlap_t']:.2f}）｜" + CAPTION,
            by_q, bias, out=DOCS)
    lines = [('2016～2022 歷史驗證', 'BLOCKED：缺 2015～2021 官方行情與事件'), ('抓取檢查', 'BUDGET_UNASSIGNED（未發出請求）'),
             ('財報 FY2014～2022', '本地齊全；依官方清單稽核'), ('v3.2 更正排除', f"PIT_SELECTION_RISK；報酬差 {b['nonoverlap_difference_mean']:+.2%}"),
             ('對加權報酬指數 alpha', f"H4 {summary['taiex_regression']['H4']['alpha_annual']:+.1%}（p {summary['taiex_regression']['H4']['alpha_p_hac']:.2f}）"),
             ('規模', 'SIZE_ATTRIBUTION_NOT_VERIFIED'), ('產業', '現行分類診斷，非 PIT'), ('研究建議', 'REVISE（維持），歷史驗證 BLOCKED')]

    def summary_chart(ax):
        ax.axis('off')
        for i, (k, v) in enumerate(lines):
            ax.text(.02, .9 - i * .11, k, fontsize=17, fontweight='bold')
            ax.text(.36, .9 - i * .11, v, fontsize=17)
    _figure('10_h4_validation_summary', 'H4 歷史驗證摘要', CAPTION, pd.DataFrame(lines, columns=['item', 'value']), summary_chart, out=DOCS)


def index_html():
    import html
    docs = sorted(DOCS.glob('*.md'), key=lambda p: (p.name != 'h4_final_recommendation.md', p.name))
    sections = ''.join(f"<h2 id='{p.name}'>{p.name}</h2><pre>{html.escape(p.read_text(encoding='utf-8'))}</pre>" for p in docs)
    toc = ''.join(f"<li><a href='#{p.name}'>{p.name}</a></li>" for p in docs)
    data = ''.join(f"<li><a href='{p.name}'>{p.name}</a></li>" for p in sorted(DOCS.glob('*.csv')))
    images = ''.join(f"<figure><img src='charts/{p.name}' alt='{p.stem}'><figcaption><a href='chart_data/{p.stem}.csv'>{p.stem}.csv</a></figcaption></figure>"
                     for p in sorted((DOCS / 'charts').glob('*.png')))
    page = ("<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><title>H4 historical validation</title>"
            "<style>body{font-family:'Microsoft JhengHei',sans-serif;margin:24px;max-width:1500px}pre{white-space:pre-wrap;background:#f6f6f6;padding:12px}"
            "figure{display:inline-block;width:48%;margin:4px;vertical-align:top}img{width:100%}</style></head><body>"
            "<h1>H4 2016～2022 歷史驗證</h1><p>RESEARCH_UNCERTIFIED / DIAGNOSTIC_ONLY。未對外請求、未新增回測、未使用 2026 資料。</p>"
            f"<h2>報告</h2><ul>{toc}</ul><h2>資料</h2><ul>{data}</ul><h2>圖表</h2>{images}{sections}</body></html>")
    (DOCS / 'index.html').write_text(page, encoding='utf-8')


if __name__ == '__main__':
    command = sys.argv[1]
    result = {'register': register, 'run': run, 'index': index_html}[command]()
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str) if result is not None else 'ok')
