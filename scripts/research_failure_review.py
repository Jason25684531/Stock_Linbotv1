"""Build the appendable strategy failure review from sealed research outputs (read-only; no backtest, no 2026 data).

Add a research version by writing one more loader and listing it in LOADERS; rerun to rebuild the same folder."""
import csv
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

OUT = ROOT / 'docs/research/2026-11-03-strategy-failure-review-v1'
RM = ROOT / 'outputs/research_mode'
GRID = ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv'
NA = 'NOT_AVAILABLE'
GATE_TEXT = {'r1': 'r1 成本後淨或超額 Sharpe <= 0', 'r2': 'r2 bootstrap 下界 <= 0', 'r3': 'r3 DSR <= 0.95',
             'r4': 'r4 個股或交易日集中度 >= 50%', 'r5': 'r5 多空環境皆負或樣本不足'}
COLUMNS = ['research_version', 'level', 'strategy', 'config_id', 'top_n', 'rebalance_days', 'weighting', 'status', 'original_status',
           'metric_basis', 'total_return_2x', 'cagr_2x', 'sharpe_2x', 'max_drawdown_2x', 'benchmark_return', 'alpha_annual', 'alpha_basis',
           'turnover_2x', 'r1_net_sharpe', 'r1_excess_sharpe', 'r1', 'r2_bootstrap_lower', 'r2', 'r3_dsr', 'r3', 'r4_top5_share',
           'r4_best5pct_day_share', 'r4', 'r5_bull_excess', 'r5_bear_excess', 'r5', 'v2_excess_sharpe_diagnostic', 'v2_alpha_diagnostic',
           'primary_failure', 'secondary_reasons', 'final_decision', 'source', 'source_sha256']
_sha = {}


def sha(path):
    path = Path(path)
    if path not in _sha:
        _sha[path] = workflow.sha256(path)
    return _sha[path]


def rel(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def parts(config_id):
    m = re.search(r'TOP(\d+)__REB(\d+)__(EQUAL|SCORE_WEIGHTED)', config_id or '')
    return (int(m[1]), int(m[2]), m[3].lower()) if m else (NA, NA, NA)


def screened(row, gates, values, basis):
    """Fill r1-r5 only for rows that went through a strict screen; primary failure = first failing gate."""
    reg = (values or {}).get('regime') or {}
    failing = [g for g in ('r1', 'r2', 'r3', 'r4', 'r5') if not gates[g]]
    row.update(metric_basis=basis, r1_net_sharpe=values['net_sharpe_2x'], r1_excess_sharpe=values['excess_sharpe_2x'], r1=gates['r1'],
               r2_bootstrap_lower=values['bootstrap_lower'], r2=gates['r2'], r3_dsr=values['deflated_sharpe'], r3=gates['r3'],
               r4_top5_share=values['top5_assets_profit_share'], r4_best5pct_day_share=values['best5pct_days_return_share'], r4=gates['r4'],
               r5_bull_excess=(reg.get('bull') or {}).get('annualized_excess'), r5_bear_excess=(reg.get('bear') or {}).get('annualized_excess'),
               r5=gates['r5'], status='PASS' if not failing else 'SCREEN_FAIL',
               primary_failure=GATE_TEXT[failing[0]] if failing else '', secondary_reasons='; '.join(GATE_TEXT[g] for g in failing[1:]))
    return row


def base(**kw):
    return {**{c: NA for c in COLUMNS}, 'secondary_reasons': '', 'final_decision': '', **kw}


def early():
    rows = []
    for batch, version in (('day3_runs_v3', 'V3'), ('day3_runs_v4', 'V4'), ('d5_runs_v3', 'D5-V3')):
        for manifest in sorted((RM / batch).glob('*/manifest.json')):
            m = load(manifest)
            config = m.get('config') or {}
            reason = m.get('reason') or m.get('status')
            terms = ','.join(m.get('missing_terms') or [])
            top, reb, weight = parts(config.get('config_id'))
            rows.append(base(research_version=version, level='CONFIG', strategy=config.get('strategy', NA), config_id=config.get('config_id', NA),
                             top_n=top, rebalance_days=reb, weighting=weight, original_status=f"{m.get('status')}:{reason}",
                             status='DATA_BLOCKED' if m.get('status') == 'NOT_COMPUTABLE' else 'SCREEN_FAIL' if m.get('status') == 'COMPUTABLE' else m.get('status'),
                             primary_failure=f'資料阻擋：{reason}' + (f'（{terms}）' if terms else '') + (f" 股票 {m.get('stock_id')}" if m.get('stock_id') else ''),
                             final_decision='SUPERSEDED（V4 於 0 組可計算後停止）', source=rel(manifest), source_sha256=sha(manifest)))
    return rows


def erratum_table():
    path = RM / 'report_v7_hypcost/strategy_decisions_erratum.md'
    table = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) == 11 and cells[1].startswith('`'):
            alpha = re.match(r'([+\-−][\d.]+)', cells[3].replace('−', '-'))
            table[cells[0]] = {'best': cells[0] + '__' + cells[1].strip('`'), 'alpha': float(alpha[1]) if alpha else NA, 'decision': cells[9]}
    return table, path


def v6():
    report = RM / 'report_v6/screen_results.json'
    diag = {r['config_id']: r for r in load(RM / 'report_v6_v2_diagnostic/screen_results.json')['rows']}
    decisions, erratum = erratum_table()
    rows = []
    for r in load(report)['rows']:
        top, reb, weight = parts(r['config_id'])
        family = decisions.get(r['strategy'], {})
        row = base(research_version='V6', level='CONFIG', strategy=r['strategy'], config_id=r['config_id'], top_n=top, rebalance_days=reb,
                   weighting=weight, original_status=f"{r['status']}:{r.get('screen_status')}:{r.get('reason') or ''}",
                   final_decision=family.get('decision', 'D5_VARIANT 無勘誤列' if r['family'] == 'D5_VARIANT' else NA),
                   source=rel(report), source_sha256=sha(report))
        if r['status'] != 'COMPUTABLE':
            row.update(status='DATA_BLOCKED', primary_failure=f"資料阻擋：{r.get('reason')} 股票 {r.get('blocking_stock')} {r.get('blocking_date')}")
        else:
            row.update(total_return_2x=r['stress_total_return'], cagr_2x=(1 + r['stress_total_return']) ** (252 / r['sessions']) - 1,
                       sharpe_2x=r['stress_sharpe'], max_drawdown_2x=r['stress_max_drawdown'], benchmark_return=r['benchmark_total_return'],
                       turnover_2x=r['stress_turnover'], metric_basis='V1')
            if r.get('gates'):
                screened(row, r['gates'], r['values'], 'V1')
            else:
                row.update(status='NOT_COMPUTABLE', primary_failure='r4 分母非正：2x 成本後淨利 <= 0（UNDEFINED_SCREEN_METRIC）')
            if family.get('best') == r['config_id']:
                row.update(alpha_annual=family['alpha'], alpha_basis='V1 HAC，家族最佳設定（V7 勘誤）')
            d = diag.get(r['config_id']) or {}
            if d.get('values'):
                row.update(v2_excess_sharpe_diagnostic=d['values']['excess_sharpe_2x'], v2_alpha_diagnostic=d.get('alpha_annual'))
        rows.append(row)
    track = RM / 'report_v6/research_track.json'
    for s in load(track)['strategies']:
        if s['family'] in ('DAY3', 'D5_VARIANT'):
            continue
        status = 'NOT_RUN' if s.get('status') == 'DEFERRED_BY_SCOPE' else 'DATA_BLOCKED'
        rows.append(base(research_version='V6', level='STRATEGY', strategy=s['strategy'], config_id='ALL', status=status,
                         original_status=s.get('status'), primary_failure=('範圍外延後，未執行' if status == 'NOT_RUN' else f"資料阻擋：{s.get('reason')}"),
                         final_decision=s.get('status'), source=rel(track), source_sha256=sha(track)))
    return rows


def v7():
    path = RM / 'report_v7_hypcost/hypcost_experiments.json'
    rows = []
    for e in load(path)['experiments']:
        new, old = e['new']['stress'], e['old']['stress']
        value = lambda d, k: (d.get(k) or {}).get('value') if isinstance(d.get(k), dict) else d.get(k)
        row = base(research_version='V7', level='HYPOTHESIS', strategy=e['family'], config_id=e['family'] + ' HYP-COST 名單緩衝 x2',
                   original_status=f"L1 {e['l1']} / L2 {e['l2']} / L3 {e['l3']['status']}", total_return_2x=value(new, 'total_return'),
                   sharpe_2x=value(new, 'sharpe'), max_drawdown_2x=value(new, 'max_drawdown'), benchmark_return=value(e['proxy'], 'total_return'),
                   alpha_annual=value(new, 'alpha_annual_hac'), alpha_basis='V1 HAC', turnover_2x=value(new, 'turnover'),
                   final_decision='STOP（限 HYP-COST 路線）', source=rel(path), source_sha256=sha(path))
        screened(row, e['l3']['gates'], e['l3']['values'], 'V1')
        row.update(status='HYPOTHESIS_REJECTED', primary_failure=f"L1 換手降幅不足：{value(old, 'turnover'):.2f} -> {value(new, 'turnover'):.2f}（門檻 1/3）",
                   secondary_reasons='; '.join(['L2 配對差下界 < 0'] + [GATE_TEXT[g] for g in ('r1', 'r2', 'r3', 'r4', 'r5') if not e['l3']['gates'][g]]))
        rows.append(row)
    return rows


def v2_family(version, folder, strategy, hypothesis_id):
    """A V2-screened registered grid: one row per configuration plus the hypothesis row with its recorded decision."""
    path = RM / folder / 'screen_results.json'
    result = RM / folder / f'{version.lower()}_result.json'
    outcome = load(result)
    decision, p = outcome['decision'], outcome['primary']
    rows = []
    for r in load(path)['rows']:
        top, reb, weight = parts(r['config_id'])
        row = base(research_version=version, level='CONFIG', strategy=strategy, config_id=r['config_id'], top_n=top, rebalance_days=reb,
                   weighting=weight, original_status=r.get('screen_status'), total_return_2x=r['total_return_2x'], cagr_2x=r['cagr_2x'],
                   sharpe_2x=r['sharpe_2x'], max_drawdown_2x=r['max_drawdown_2x'], benchmark_return=r['benchmark_v2_total_return'],
                   alpha_annual=r['alpha_annual'], alpha_basis='V2 HAC', turnover_2x=r['turnover_2x'],
                   final_decision=decision + ('\uff08\u4e3b\u8981\u8a2d\u5b9a\uff09' if r['config_id'] == outcome['primary_config'] else '\uff08\u654f\u611f\u5ea6\uff09'),
                   source=rel(path), source_sha256=sha(path))
        rows.append(screened(row, r['gates'], r['values'], 'V2'))
    rejected = decision == 'STOP'
    rows.append(base(research_version=version, level='HYPOTHESIS', strategy=strategy, config_id=hypothesis_id,
                     status='HYPOTHESIS_REJECTED' if rejected else 'SCREEN_FAIL', original_status=decision, metric_basis='V2',
                     alpha_annual=p['alpha_annual'], alpha_basis='V2 HAC primary',
                     primary_failure=(f"primary 2x alpha {p['alpha_annual']:.3f} <= 0 (p {p['alpha_p_hac']:.2f})" if rejected else
                                      'registered verdict ' + decision + ' (primary alpha > 0, r1 pass); strict screen fails ' +
                                      ','.join(g for g, ok in p['gates'].items() if not ok)),
                     final_decision=decision, source=rel(result), source_sha256=sha(result)))
    return rows


def h1():
    return v2_family('H1', 'report_h1_v2', 'near_high_252d', 'near_high_252d:H1')


def h4():
    path = RM / 'report_h4_v2/h4_result.json'
    return v2_family('H4', 'report_h4_v2', 'H4_GROWTH_MOMENTUM', 'H4:GROWTH-MOMENTUM-V2') if path.is_file() else []


def fundamental_v2():
    path = RM / 'g2g3_factor_dev_v2/factor_result.json'
    if not path.is_file():
        return []
    r = load(path)
    return [base(research_version='G2G3-V2', level='HYPOTHESIS', strategy=f, config_id='G2G3:FACTOR-DEV-V2', status='DIAGNOSTIC_ONLY',
                 original_status=f"admission {r['admission']} / decision {r['decision'][f]}", metric_basis='DIAGNOSTIC_ONLY',
                 primary_failure='' if r['admission'] == 'PASS' else ','.join(r['admission_failing']), final_decision=r['decision'][f],
                 source=rel(path), source_sha256=sha(path)) for f in r['decision']]


def h2():
    path = RM / 'h2_a191_liquidity_v1/h2_result.json'
    r = load(path)
    ic = r['residual_ic']
    return [base(research_version='H2', level='HYPOTHESIS', strategy='A191_132', config_id='A191_132:H2-LIQUIDITY', status='HYPOTHESIS_REJECTED',
                 original_status=f"STOP（V7 原判 CONTINUE 不覆寫）", metric_basis='DIAGNOSTIC_ONLY',
                 primary_failure=f"控制 amihud 後殘差 IC 為負：h20 {ic['20']['mean_ic']:.3f}、h60 {ic['60']['mean_ic']:.3f}",
                 secondary_reasons=f"最高流動性三分位占淨利；最低流動性占比 {r['primary_lowest_liquidity_net_share']:.0%}；優勢來自規模與流動性傾斜",
                 final_decision='STOP', source=rel(path), source_sha256=sha(path))]


def registry_rows():
    """Registered hypotheses not covered by a batch loader: their recorded outcome, or REGISTERED_NOT_RUN."""
    covered = {'RESEARCH-SCREEN', 'RESEARCH-ASSUMPTIONS', 'near_high_252d:H1', 'A191_132:H2-LIQUIDITY', 'RS_004_NEAR_HIGH_120D:HYP-COST',
               'A191_088:HYP-COST', 'G2G3:FACTOR-DEV-V1', 'H4:GROWTH-MOMENTUM-V1', 'G2G3:FACTOR-DEV-V2', 'H4:GROWTH-MOMENTUM-V2'}
    results = {r['hypothesis_id']: r for r in csv.DictReader(workflow.results_path().open(encoding='utf-8-sig'))}
    rows = []
    for r in csv.DictReader(workflow.REGISTRY.open(encoding='utf-8-sig')):
        hid = r['hypothesis_id']
        if hid in covered or hid.split(':')[0] in covered:
            continue
        done = results.get(hid)
        outcome = done['outcome'] if done else 'REGISTERED_NOT_RUN'
        status = 'HYPOTHESIS_REJECTED' if outcome == 'REJECTED' else 'DIAGNOSTIC_ONLY' if done else 'NOT_RUN'
        rows.append(base(research_version='REGISTRY', level='HYPOTHESIS', strategy=r['strategy'], config_id=hid, status=status,
                         original_status=outcome[:120], primary_failure=(done['note'][:200] if done else '已登記，未執行'),
                         final_decision=outcome[:60], source=rel(workflow.results_path() if done else workflow.REGISTRY),
                         source_sha256=sha(workflow.results_path() if done else workflow.REGISTRY)))
    return rows


def fundamental_round():
    path = RM / 'g2g3_factor_dev_v1/factor_result.json'
    if not path.is_file():
        return []
    r = load(path)
    rows = [base(research_version='G2G3', level='HYPOTHESIS', strategy=f, config_id='G2G3:FACTOR-DEV-V1', status=r['decision'][f]
                 if r['decision'][f] == 'DATA_BLOCKED' else 'DIAGNOSTIC_ONLY', original_status=f"decision {r['decision'][f]} / if_admitted {r['if_admitted'][f]}",
                 metric_basis='DIAGNOSTIC_ONLY', primary_failure='資料准入未過：' + ','.join(r['admission_failing']), final_decision=r['decision'][f],
                 source=rel(path), source_sha256=sha(path)) for f in r['decision']]
    rows.append(base(research_version='H4', level='HYPOTHESIS', strategy='H4_GROWTH_MOMENTUM', config_id='H4:GROWTH-MOMENTUM-V1',
                     status='DATA_BLOCKED' if r['h4_disposition'] == 'BLOCKED' else r['h4_disposition'], original_status=r['h4_disposition'],
                     primary_failure='未執行：G2／G3 資料准入未過（' + ','.join(r['admission_failing']) + '）', final_decision='BLOCKED（登記保留）',
                     source=rel(path), source_sha256=sha(path)))
    return rows


LOADERS = (early, v6, v7, h1, h2, registry_rows, fundamental_round, fundamental_v2, h4)
ORDER = ['V3', 'V4', 'D5-V3', 'V6', 'V7', 'H1', 'H2', 'REGISTRY', 'G2G3', 'H4', 'G2G3-V2']
WINDOW = '期間 2023-01-03～2025-12-31'


def _figure(name, title, caption, data, draw, ncols=1, out=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['Microsoft JhengHei'], 'axes.unicode_minus': False, 'font.size': 13})
    fig, axes = plt.subplots(1, ncols, figsize=(16, 9), dpi=100)
    draw(axes)
    fig.suptitle(title, fontsize=22, fontweight='bold')
    fig.text(0.01, 0.01, caption + '｜' + WINDOW, fontsize=11, color='#444')
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    out = out or OUT
    (out / 'charts').mkdir(parents=True, exist_ok=True)
    (out / 'chart_data').mkdir(parents=True, exist_ok=True)
    fig.savefig(out / 'charts' / f'{name}.png')
    plt.close(fig)
    data.to_csv(out / 'chart_data' / f'{name}.csv', index=False, encoding='utf-8-sig')
    return name


def _num(frame, *cols):
    out = frame.copy()
    for c in cols:
        out[c] = pd.to_numeric(out[c], errors='coerce')
    return out


def _nav(folder, initial=1_000_000):
    from scripts.research_mode_report import returns_of
    _, nav = returns_of(load(Path(folder) / 'stress.json'), initial)
    return nav / initial


def charts(frame):
    made = []
    counts = frame.groupby(['research_version', 'status']).size().unstack(fill_value=0).reindex([v for v in ORDER if v in set(frame.research_version)])

    def d01(ax):
        bottom = np.zeros(len(counts))
        for status in counts.columns:
            ax.bar(counts.index, counts[status], bottom=bottom, label=status)
            bottom += counts[status].to_numpy()
        ax.set_ylabel('設定、策略或假設數')
        ax.legend()
    made.append(_figure('01_strategy_status', '01 歷史研究整體狀態分布', '來源：strategy_results.csv｜每列一個設定、策略或假設；PASS 為 0',
                        counts.reset_index(), d01))

    gates = ['r1', 'r2', 'r3', 'r4', 'r5']
    rates = []
    for version, basis in (('V6', 'V1'), ('V7', 'V1'), ('H1', 'V2'), ('H4', 'V2')):
        part = frame[(frame.research_version == version) & frame.r1.isin([True, False, 'True', 'False'])]
        rates += [{'group': f'{version}（{basis} 代理，n={len(part)}）', 'gate': g, 'pass_rate': part[g].astype(str).eq('True').mean()} for g in gates]
    rates = pd.DataFrame(rates)

    def d02(ax):
        wide = rates.pivot(index='gate', columns='group', values='pass_rate')
        wide.plot.bar(ax=ax, rot=0)
        ax.set_ylim(0, 1)
        ax.set_ylabel('通過比率')
    made.append(_figure('02_screen_pass_rates', '02 r1～r5 各門檻通過率', '來源：report_v6、report_v7_hypcost、report_h1_v2｜V1 與 V2 代理分開呈現，不合併', rates, d02))

    decisions, _ = erratum_table()
    grid = list(csv.DictReader(GRID.open(encoding='utf-8-sig')))
    folder = {r['strategy_id']: RM / 'day3_runs_v6' / f'{i:03d}' for i, r in enumerate(grid)}
    h1_grid = list(csv.DictReader((ROOT / 'outputs/strategy_research/h1_near_high_252d_v1/strategy_grid.csv').open(encoding='utf-8')))
    h1_primary = RM / 'h1_runs_v1' / f"{[r['strategy_id'] for r in h1_grid].index('near_high_252d__EQUAL__TOP10__REB60__EQUAL'):03d}"
    curves = {f'V6 {k}': _nav(folder[v['best']]) for k, v in decisions.items() if v['best'] in folder}
    curves['H1 near_high_252d 主要設定（V2 篩選）'] = _nav(h1_primary)
    bench = pd.read_csv(RM / 'validation_execution_v1/benchmark_series.csv', index_col='date', parse_dates=True)
    curves['V1 代理（價格報酬）'] = (1 + bench['v1_price']).cumprod()
    curves['V2 代理（含息）'] = (1 + bench['v2_total']).cumprod()
    equity = pd.DataFrame(curves)

    def d03(ax):
        for name in equity:
            bold = name.startswith(('V1', 'V2', 'H1')) or 'A191_132' == name.split()[-1]
            ax.plot(equity.index, equity[name], lw=3 if bold else 1, alpha=1 if bold else .45, label=name if bold or len(name) < 40 else None)
        ax.set_ylabel('淨值（起始 1.0，2x 成本後）')
        ax.legend(fontsize=9, ncol=2)
    made.append(_figure('03_equity_curves', '03 各家族最佳設定與市場代理的累積報酬',
                        '來源：day3_runs_v6、h1_runs_v1 的 stress.json、benchmark_series.csv｜家族最佳設定依 V7 勘誤；粗線為代理、A191_132、H1',
                        equity.reset_index(names='date'), d03))

    configs = _num(frame[(frame.level == 'CONFIG') & frame.research_version.isin(['V6', 'H1', 'H4'])], 'max_drawdown_2x', 'r1_excess_sharpe',
                   'v2_alpha_diagnostic', 'alpha_annual', 'r3_dsr', 'r4_top5_share', 'r4_best5pct_day_share', 'turnover_2x')
    configs['group'] = np.where(configs.research_version == 'H1', 'H1 near_high_252d', configs.strategy.str.replace('RS_004_NEAR_HIGH_120D', 'RS120'))
    dd = configs.dropna(subset=['max_drawdown_2x'])[['research_version', 'group', 'config_id', 'max_drawdown_2x']]

    def d04(ax):
        groups = sorted(dd.group.unique())
        ax.boxplot([dd.loc[dd.group == g, 'max_drawdown_2x'] for g in groups], vert=False, tick_labels=groups)
        ax.set_xlabel('2x 成本後最大回撤')
    made.append(_figure('04_drawdown', '04 各家族最大回撤分布', '來源：report_v6（V1）、report_h1_v2（V2）｜只含可計算設定', dd, d04))

    sa = configs[['research_version', 'group', 'config_id', 'r1_excess_sharpe', 'v2_alpha_diagnostic', 'alpha_annual']]

    def d05(axes):
        groups = sorted(sa.group.unique())
        for ax, col, label in ((axes[0], 'r1_excess_sharpe', '超額 Sharpe（V6 用 V1 代理、H1 用 V2 代理）'),
                               (axes[1], 'v2_alpha_diagnostic', 'V2 alpha／年（V6 為 DIAGNOSTIC_ONLY；H1 取正式值）')):
            values = sa.assign(v=np.where(sa.research_version.isin(['H1', 'H4']), sa['alpha_annual'] if col != 'r1_excess_sharpe' else sa[col], sa[col]))
            for i, g in enumerate(groups):
                v = values.loc[values.group == g, 'v'].dropna()
                ax.scatter(v, [i] * len(v), alpha=.6)
            ax.axvline(0, color='k', lw=1)
            ax.set_yticks(range(len(groups)), groups)
            ax.set_xlabel(label)
    made.append(_figure('05_sharpe_alpha', '05 各策略超額 Sharpe 與 alpha', '來源：report_v6、report_v6_v2_diagnostic、report_h1_v2｜左右兩圖口徑不同，不可互相比較',
                        sa, d05, ncols=2))

    p7 = load(RM / 'report_v6/audit/screen_diagnostics.json')
    dsr = configs.dropna(subset=['r3_dsr'])[['research_version', 'group', 'config_id', 'r3_dsr']]
    bars = pd.DataFrame({'item': ['743 次試驗的期望最大 Sharpe', '達 DSR 0.95 所需（中位數）', '實際最佳超額 Sharpe', '實際中位數'],
                         'annual_sharpe': [p7['r3']['expected_max_sr_annual_743'], p7['r3']['required_sr_annual_for_0_95']['median'],
                                           p7['r3']['observed_sr_annual']['max'], p7['r3']['observed_sr_annual']['median']]})

    def d06(axes):
        groups = sorted(dsr.group.unique())
        for i, g in enumerate(groups):
            v = dsr.loc[dsr.group == g, 'r3_dsr']
            axes[0].scatter(v, [i] * len(v), alpha=.6)
        axes[0].axvline(.95, color='r', ls='--', label='門檻 0.95')
        axes[0].set_xlim(-.02, 1)
        axes[0].set_yticks(range(len(groups)), groups)
        axes[0].set_xlabel('DSR（試驗數 743）')
        axes[0].legend()
        axes[1].barh(bars['item'], bars['annual_sharpe'], color=['#999', '#c33', '#36c', '#9bd'])
        axes[1].set_xlabel('年化超額 Sharpe（Phase 7，V1 代理）')
    made.append(_figure('06_dsr', '06 DSR 與門檻：過度擬合風險', '來源：report_v6、report_h1_v2、audit/screen_diagnostics.json｜DSR 以 max(研究試驗數, 743) 計',
                        pd.concat([dsr, bars], ignore_index=True), d06, ncols=2))

    conc = configs.dropna(subset=['r4_top5_share'])[['research_version', 'group', 'config_id', 'r4_top5_share', 'r4_best5pct_day_share']]
    med = pd.DataFrame({'item': ['前 5 檔占淨利（中位數）', '前 5 檔占毛利（中位數，診斷）', '最佳 5% 日占報酬（中位數）'],
                        'share': [p7['r4']['stock_share']['median'], p7['r4']['stock_share_of_gross_profit_diagnostic']['median'], p7['r4']['day_share']['median']]})

    def d07(axes):
        axes[0].scatter(conc.r4_top5_share.clip(lower=.01), conc.r4_best5pct_day_share.clip(lower=.01), alpha=.5)
        axes[0].set_xscale('log')
        axes[0].set_yscale('log')
        axes[0].axvline(.5, color='r', ls='--')
        axes[0].axhline(.5, color='r', ls='--')
        axes[0].set_xlabel('前 5 檔占淨利（r4 定義）')
        axes[0].set_ylabel('最佳 5% 交易日占報酬')
        axes[1].bar(med['item'], med['share'], color=['#c33', '#36c', '#c93'])
        axes[1].axhline(.5, color='r', ls='--')
        axes[1].set_ylabel('占比')
    made.append(_figure('07_profit_concentration', '07 獲利集中度：淨利與毛利分母', '來源：report_v6、report_h1_v2、audit/screen_diagnostics.json｜淨利分母偏小會放大占比',
                        pd.concat([conc, med], ignore_index=True), d07, ncols=2))

    raw = load(RM / 'report_v6/screen_results.json')['rows']
    cost = pd.DataFrame([{'config_id': r['config_id'], 'strategy': r['strategy'], 'turnover_2x': r['stress_turnover'],
                          'cost_drag': r['base_total_return'] - r['stress_total_return']} for r in raw if r['status'] == 'COMPUTABLE'])
    v7rows = [{'config_id': e['family'], 'turnover_v6': e['old']['stress']['turnover']['value'], 'turnover_v7': e['new']['stress']['turnover']['value']}
              for e in load(RM / 'report_v7_hypcost/hypcost_experiments.json')['experiments']]

    def d08(ax):
        ax.scatter(cost.turnover_2x, cost.cost_drag, alpha=.5, label='V6 可計算設定')
        for r in v7rows:
            ax.annotate('', xy=(r['turnover_v7'], 0), xytext=(r['turnover_v6'], 0), arrowprops={'arrowstyle': '->', 'color': 'r'})
            ax.text(r['turnover_v7'], .02, 'V7 ' + r['config_id'], color='r', fontsize=10)
        ax.set_xlabel('年換手（2x 軌）')
        ax.set_ylabel('1x 與 2x 成本總報酬差')
        ax.legend()
    made.append(_figure('08_turnover_cost', '08 換手率與成本侵蝕', '來源：report_v6 screen_results.json、V7 hypcost_experiments.json｜紅色箭頭為 V7 名單緩衝前後換手',
                        pd.concat([cost, pd.DataFrame(v7rows)], ignore_index=True), d08))

    h = configs[configs.research_version == 'H1'][['config_id', 'total_return_2x', 'alpha_annual']].copy()
    h['total_return_2x'] = pd.to_numeric(h['total_return_2x'])
    h['label'] = h.config_id.str.replace('near_high_252d__EQUAL__', '')
    proxy = float(frame.loc[frame.research_version == 'H1', 'benchmark_return'].pipe(pd.to_numeric, errors='coerce').dropna().iloc[0])

    def d09(axes):
        colors = ['#c33' if c.endswith('TOP10__REB60__EQUAL') else '#69c' for c in h.config_id]
        axes[0].barh(h.label, h.total_return_2x, color=colors)
        axes[0].axvline(proxy, color='k', ls='--', label=f'V2 代理 {proxy:.1%}')
        axes[0].set_xlabel('2x 成本後總報酬')
        axes[0].legend()
        axes[1].barh(h.label, h.alpha_annual, color=colors)
        axes[1].axvline(0, color='k')
        axes[1].set_xlabel('alpha／年（V2，HAC）')
    made.append(_figure('09_h1_config_comparison', '09 H1 near_high_252d 十二組設定（紅色為主要設定）', '來源：report_h1_v2｜判定只看主要設定：STOP',
                        h, d09, ncols=2))

    terc = pd.read_csv(RM / 'h2_a191_liquidity_v1/attribution_terciles.csv').pivot(index='config_id', columns='tercile', values='net_share')
    terc.index = terc.index.str.replace('A191_132__EQUAL__', '')
    ic = pd.read_csv(RM / 'h2_a191_liquidity_v1/ic_summary.csv')

    def d10(axes):
        terc[['T1_LIQUID', 'T2_MID', 'T3_ILLIQUID', 'UNRANKED']].plot.barh(stacked=True, ax=axes[0])
        axes[0].set_xlabel('淨利占比（2x 帳本，依當日 amihud 三分位）')
        wide = ic.pivot(index='factor_id', columns='horizon', values='mean_ic')
        wide.plot.bar(ax=axes[1], rot=0)
        axes[1].axhline(0, color='k')
        axes[1].set_ylabel('平均 Rank IC（全市場每日橫斷面）')
    made.append(_figure('10_h2_liquidity', '10 H2 A191_132：流動性歸因與增量 IC', '來源：h2_a191_liquidity_v1｜殘差 = A191_132 對 amihud 正交化；DIAGNOSTIC_ONLY',
                        pd.concat([terc.reset_index(), ic], ignore_index=True), d10, ncols=2))
    return made


def index_html(frame, made):
    docs = [('README.md', '說明與追加方式'), ('executive_summary.md', '執行摘要'), ('research_version_comparison.md', '版本比較'),
            ('strategies/v6_strategy_review.md', 'V6'), ('strategies/v7_cost_review.md', 'V7'), ('strategies/h1_near_high_252d.md', 'H1'),
            ('strategies/h2_a191_132.md', 'H2'), ('strategy_results.csv', '策略總表 CSV'), ('failure_reason_matrix.csv', '失敗原因矩陣 CSV'),
            ('source_manifest.csv', '來源清單')]
    keep = ['research_version', 'level', 'strategy', 'config_id', 'status', 'metric_basis', 'total_return_2x', 'sharpe_2x', 'max_drawdown_2x',
            'alpha_annual', 'alpha_basis', 'r1', 'r2', 'r3', 'r4', 'r5', 'primary_failure', 'final_decision', 'source']
    data = json.dumps(frame[keep].astype(str).to_dict('records'), ensure_ascii=False).replace('</', '<' + '\\/')
    links = ''.join(f"<li><a href='{p}'>{t}</a></li>" for p, t in docs)
    images = ''.join(f"<figure><img src='charts/{m}.png' alt='{m}'><figcaption><a href='chart_data/{m}.csv'>{m}.csv</a></figcaption></figure>" for m in made)
    page = f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><title>Strategy failure review</title>
<style>body{{font-family:'Microsoft JhengHei',sans-serif;margin:24px}}table{{border-collapse:collapse;font-size:12px}}td,th{{border:1px solid #ccc;padding:3px 6px}}
th{{background:#eee;position:sticky;top:0}}figure{{display:inline-block;width:48%;margin:4px}}img{{width:100%}}.wrap{{max-height:520px;overflow:auto}}</style></head><body>
<h1>歷史策略研究總覽</h1><p>RESEARCH_UNCERTIFIED / DIAGNOSTIC_ONLY。期間 2023-01-03～2025-12-31，未使用 2026 資料。</p><ul>{links}</ul>
<p>版本 <select id='v'></select> 狀態 <select id='s'></select> 失敗原因 <input id='q' placeholder='keyword'> <span id='n'></span></p>
<div class='wrap'><table id='t'></table></div><h2>圖表</h2>{images}
<script>const rows={data};const cols=Object.keys(rows[0]);
function opts(id,key){{const el=document.getElementById(id);['ALL',...new Set(rows.map(r=>r[key]))].forEach(x=>el.add(new Option(x,x)));el.onchange=draw}}
function draw(){{const v=document.getElementById('v').value,s=document.getElementById('s').value,q=document.getElementById('q').value.toLowerCase();
const hit=rows.filter(r=>(v==='ALL'||r.research_version===v)&&(s==='ALL'||r.status===s)&&(r.primary_failure+r.config_id+r.strategy).toLowerCase().includes(q));
document.getElementById('n').textContent=hit.length+' rows';
document.getElementById('t').innerHTML='<tr>'+cols.map(c=>'<th>'+c+'</th>').join('')+'</tr>'+hit.map(r=>'<tr>'+cols.map(c=>'<td>'+r[c]+'</td>').join('')+'</tr>').join('')}}
opts('v','research_version');opts('s','status');document.getElementById('q').oninput=draw;draw();</script></body></html>"""
    (OUT / 'index.html').write_text(page, encoding='utf-8')


def failure_matrix(frame):
    configs = frame[frame.level == 'CONFIG']
    rows = []
    for version in ORDER:
        part = frame[frame.research_version == version]
        if part.empty:
            continue
        screened_rows = part[part.r1.astype(str).isin(['True', 'False'])]
        rows.append({'research_version': version, 'rows': len(part), 'data_blocked': int((part.status == 'DATA_BLOCKED').sum()),
                     'not_computable': int((part.status == 'NOT_COMPUTABLE').sum()), 'not_run': int((part.status == 'NOT_RUN').sum()),
                     'screened': len(screened_rows), **{f'{g}_fail': int(screened_rows[g].astype(str).eq('False').sum()) for g in ('r1', 'r2', 'r3', 'r4', 'r5')},
                     'hypothesis_rejected': int((part.status == 'HYPOTHESIS_REJECTED').sum()), 'pass': int((part.status == 'PASS').sum()),
                     'primary_failure_top': part.primary_failure.value_counts().index[0] if part.primary_failure.ne(NA).any() else ''})
    pd.DataFrame(rows).to_csv(OUT / 'failure_reason_matrix.csv', index=False, encoding='utf-8-sig')
    return configs


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([row for loader in LOADERS for row in loader()], columns=COLUMNS)
    frame.to_csv(OUT / 'strategy_results.csv', index=False, encoding='utf-8-sig')
    failure_matrix(frame)
    index_html(frame, charts(frame))
    sources = sorted({(s, h) for s, h in zip(frame['source'], frame['source_sha256'])})
    with (OUT / 'source_manifest.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        w = csv.writer(stream)
        w.writerow(('source', 'sha256', 'rows'))
        for s, h in sources:
            w.writerow((s, h, int((frame['source'] == s).sum())))
    return frame


if __name__ == '__main__':
    frame = build()
    print(frame.groupby(['research_version', 'status']).size().to_string())
