"""G2/G3 fundamental admission and development-window factor study; H4 registration (offline, development window only)."""
import hashlib
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

SAME_BASIS = ROOT / 'data/processed/fundamental_pit_v3_yoy_same_basis'
VERSIONS = {
    'V1': (SAME_BASIS, 'g2g3_factor_dev_v1', 'G2G3:FACTOR-DEV-V1', 'H4:GROWTH-MOMENTUM-V1',
           '2026-11-03-strategy-review-fundamental-execution-v1', '2026-11-03-fundamental-factor-execution-v1'),
    'V2': (ROOT / 'data/processed/fundamental_pit_v3_2_verified', 'g2g3_factor_dev_v2', 'G2G3:FACTOR-DEV-V2', 'H4:GROWTH-MOMENTUM-V2',
           '2026-11-04-fundamental-pit-recovery-h4-v1', '2026-11-04-fundamental-pit-recovery-h4-v1'),
}


def use(version):
    """ponytail: one module, version switch by rebinding paths; V1 keeps its original inputs, outputs and registrations."""
    global DATA, OUT, FACTOR_ID, H4_ID, DESIGN, DOCS
    data, out, FACTOR_ID, H4_ID, change, docs = VERSIONS[version]
    DATA, OUT = data, ROOT / 'outputs/research_mode' / out
    DESIGN, DOCS = ROOT / 'openspec/changes' / change / 'design.md', ROOT / 'docs/research' / docs


use('V1')


FACTORS = {'G2_OPERATING_INCOME_YOY': ('operating_income_yoy', 'operating_profit'), 'G3_EPS_YOY': ('eps_yoy', 'eps')}
H4_GRID = ROOT / 'outputs/strategy_research/h4_growth_momentum_v1/strategy_grid.csv'
H4_PRIMARY = 'H4_GROWTH_MOMENTUM__TOP10__REB60__EQUAL'
DEV_END = pd.Timestamp('2025-12-31')
MAP = {'ACCEPT': 'READY_FOR_STRATEGY_TEST', 'REVIEW': 'REVIEW_REQUIRED', 'REJECT': 'REJECTED', 'INSUFFICIENT_DATA': 'DATA_BLOCKED'}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    return value


def h4_grid():
    from core.research import strategy_search as search
    from scripts.research_mode_run import COSTS
    rows = []
    for top_n, cadence, weighting in itertools.product(search.TOP_N_VALUES, search.REBALANCE_DAYS_VALUES, search.PORTFOLIO_WEIGHTINGS):
        identity = {'growth': 'READY of G2_OPERATING_INCOME_YOY, G3_EPS_YOY', 'momentum': 'near_high_252d', 'weights': [0.5, 0.5],
                    'top_n': top_n, 'rebalance_days': cadence, 'portfolio_weighting': weighting, **COSTS}
        rows.append({'strategy_id': f'H4_GROWTH_MOMENTUM__TOP{top_n}__REB{cadence}__{weighting.upper()}', 'composite_id': 'H4_GROWTH_MOMENTUM',
                     'factor_set': 'G2_OPERATING_INCOME_YOY+G3_EPS_YOY+near_high_252d', 'factor_weighting': 'growth_0.5_momentum_0.5',
                     'top_n': top_n, 'rebalance_days': cadence, 'portfolio_weighting': weighting, 'implementation_risk': 'FUNDAMENTAL_PIT_UNCERTIFIED',
                     'config_hash': hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()})
    text = pd.DataFrame(rows).sort_values('strategy_id', kind='stable').to_csv(index=False, lineterminator='\n')
    if H4_GRID.exists() and H4_GRID.read_text(encoding='utf-8') != text:
        raise ValueError('H4_GRID_CHANGED')
    H4_GRID.parent.mkdir(parents=True, exist_ok=True)
    H4_GRID.write_text(text, encoding='utf-8')
    return workflow.sha256(H4_GRID)


def registrations():
    window = '2023-01-03 to 2025-12-31; labels end by 2025-12-31; no 2026 OOS'
    return {
        'FACTOR': {'hypothesis_id': FACTOR_ID, 'strategy': 'G2_OPERATING_INCOME_YOY+G3_EPS_YOY',
                   'hypothesis': (f'G2 (operating_income_yoy) and G3 (eps_yoy) from {DATA.name} predict 20- and 60-session '
                                  'raw-close returns of prepared_v6 members on the development window. Admission checks A1-A8 and the factor '
                                  f'rule are fixed in {DESIGN.parent.name}/design.md before any result.'),
                   'falsification_test': ('Frozen 2026-09-17 ValidationPolicy and _gate: ACCEPT->READY_FOR_STRATEGY_TEST, REVIEW->REVIEW_REQUIRED, '
                                          'REJECT->REJECTED, INSUFFICIENT_DATA->DATA_BLOCKED. Admission not PASS -> decision DATA_BLOCKED; '
                                          'the mapped verdict is kept only as if_admitted DIAGNOSTIC_ONLY and cannot start H4.'),
                   'data_window': window, 'trial_budget': '0', 'source': 'design.md sha256 ' + workflow.sha256(DESIGN)},
        'H4': {'hypothesis_id': H4_ID, 'strategy': 'H4_GROWTH_MOMENTUM',
               'hypothesis': ('Operating-income or EPS growth plus near_high_252d momentum selects better than the technical factor alone. '
                              'Score = 0.5 x growth rank + 0.5 x near_high_252d rank among prepared_v6 members per signal day; growth uses '
                              f'only factors READY_FOR_STRATEGY_TEST under {FACTOR_ID} with values from {DATA.name} (mean of ranks when both, the single one '
                              'otherwise, p1/p99 winsorized before ranking; a stock missing growth or momentum is not scored). V6 assumptions, '
                              '2x cost track, RESEARCH-SCREEN:V2. Primary ' + H4_PRIMARY + '; the other 11 are sensitivity.'),
               'falsification_test': ('Runs only if admission PASS and at least one growth factor READY; otherwise not run and no result is '
                                      'recorded. CONTINUE when the primary 2x alpha > 0 and r1 passes; REVISE when alpha > 0 and r1 fails; '
                                      'STOP when alpha <= 0; NOT_EVALUABLE when the primary is not computable.'),
               'data_window': window, 'trial_budget': '12', 'source': 'strategy_grid.csv sha256 ' + h4_grid()},
    }


def register(name):
    from core.runtime.operation_policy import ExecutionMode, current_mode
    if current_mode() is not ExecutionMode.RESEARCH:
        raise PermissionError('RESEARCH_MODE_REQUIRED')
    return workflow.register(registrations()[name])


def load():
    from scripts.research_validation_v2 import _prepared
    bars, _, inputs = _prepared()
    universe = pd.DataFrame(json.loads((ROOT / 'outputs/research_mode/prepared_v6/universe.json').read_text(encoding='utf-8')))
    members = universe.loc[universe['member'], ['asof_date', 'asset_id']].rename(columns={'asset_id': 'ticker'})
    members['asof_date'] = pd.to_datetime(members['asof_date'])
    records = pd.read_parquet(DATA / 'fundamental_records.parquet')
    if DATA != SAME_BASIS:  # the verified version holds only G2/G3 rows; base filings for the A4 recomputation come from unchanged v3.1
        base = pd.read_parquet(SAME_BASIS / 'fundamental_records.parquet')
        records = pd.concat([base.loc[base['metric'].isin([b for _, b in FACTORS.values()])], records], ignore_index=True)
    matrix = pd.read_parquet(DATA / 'fundamental_matrix.parquet', columns=['asof_date', 'ticker', *(m for m, _ in FACTORS.values())])
    matrix['asof_date'] = pd.to_datetime(matrix['asof_date'])
    for frame in (records, matrix, members):
        frame['ticker'] = frame['ticker'].astype(str)
    sources = {**inputs, 'fundamental_records.parquet': workflow.sha256(DATA / 'fundamental_records.parquet'),
               'fundamental_matrix.parquet': workflow.sha256(DATA / 'fundamental_matrix.parquet')}
    return bars, members, records, matrix.loc[matrix['asof_date'] <= DEV_END], sources


def asof(records, metric, dates, tickers):
    """Rebuild the as-of panel the way fundamental_pit_v3.build_matrix does: latest available record, period end before publication."""
    right = records.loc[records['metric'].eq(metric) & records['value'].notna() & records['ticker'].isin(tickers)].copy()
    right['available_date'] = pd.to_datetime(right['available_date'])
    right = right.sort_values(['available_date', 'publication_date', 'revision_id'], kind='stable')
    left = pd.DataFrame([(d, t) for t in sorted(tickers) for d in dates], columns=['asof_date', 'ticker']).sort_values('asof_date', kind='stable')
    joined = pd.merge_asof(left, right[['ticker', 'available_date', 'value', 'period_end', 'publication_date']], left_on='asof_date',
                           right_on='available_date', by='ticker', direction='backward', allow_exact_matches=True)
    ok = joined['value'].notna() & (pd.to_datetime(joined['period_end']) < pd.to_datetime(joined['publication_date']))
    joined.loc[~ok, ['value', 'available_date']] = np.nan
    return joined[['asof_date', 'ticker', 'value', 'available_date']]


def admission(members, records, matrix):
    from core.research.fundamental_factor_validation import FROZEN_CANDIDATES, FROZEN_POLICY
    dates = sorted(members['asof_date'].unique())
    tickers = set(matrix['ticker'])
    frozen = {c.candidate_id: c.source_metric for c in FROZEN_CANDIDATES}
    checks, detail = {}, {}
    checks['A1_definition'] = all(frozen.get(f) == m for f, (m, _) in FACTORS.items())
    used = records.loc[records['metric'].isin([m for m, _ in FACTORS.values()])].copy()
    used = used.loc[pd.to_datetime(used['available_date']) <= DEV_END]
    stamp = pd.to_datetime(used['publication_timestamp'], utc=True).dt.tz_convert('Asia/Taipei')
    late = stamp.dt.strftime('%H:%M') >= '13:30'
    before = pd.to_datetime(used['available_date']) < pd.to_datetime(used['publication_date'])
    same_day_late = late & (pd.to_datetime(used['available_date']) == pd.to_datetime(used['publication_date']))
    detail['A2'] = {'rows': len(used), 'available_before_publication': int(before.sum()), 'after_close_same_day': int(same_day_late.sum())}
    checks['A2_available_after_publication'] = not before.any() and not same_day_late.any()
    mismatch, latest_available = 0, pd.NaT
    for metric, _ in FACTORS.values():
        rebuilt = asof(records, metric, dates, tickers).merge(matrix[['asof_date', 'ticker', metric]], on=['asof_date', 'ticker'], how='left')
        a, b = rebuilt['value'], rebuilt[metric]
        mismatch += int(((a.isna() != b.isna()) | ((a - b).abs() > 1e-12)).sum())
        latest_available = max(latest_available, rebuilt['available_date'].max()) if pd.notna(latest_available) else rebuilt['available_date'].max()
    detail['A3'] = {'cells_compared': len(dates) * len(tickers) * len(FACTORS), 'mismatches': mismatch}
    checks['A3_asof_equals_matrix'] = mismatch == 0
    base = records.loc[records['metric'].isin([b for _, b in FACTORS.values()])].set_index(['ticker', 'fiscal_year', 'quarter', 'metric'])
    bad_value, bad_order, bad_basis, missing = 0, 0, 0, 0
    for metric, base_metric in FACTORS.values():
        rows = used.loc[used['metric'].eq(metric)]
        for r in rows.itertuples(index=False):
            try:
                cur = base.loc[(r.ticker, r.fiscal_year, r.quarter, base_metric)]
                pri = base.loc[(r.ticker, r.fiscal_year - 1, r.quarter, base_metric)]
            except KeyError:
                missing += 1
                continue
            bad_value += abs(cur['value'] / pri['value'] - 1 - r.value) > 1e-9
            bad_order += pd.Timestamp(pri['available_date']) > pd.Timestamp(r.available_date)
            bad_basis += cur['REPORTING_BASIS'] != pri['REPORTING_BASIS']
    detail['A4_A5'] = {'yoy_rows': len(used), 'base_missing': missing, 'value_mismatch': int(bad_value), 'prior_available_later': int(bad_order),
                       'basis_mismatch': int(bad_basis)}
    checks['A4_prior_year_pairing'] = missing == 0 and bad_value == 0 and bad_order == 0
    removed = pd.read_csv(SAME_BASIS / 'correction_detail.csv', dtype={'ticker': str})
    leaked = removed.merge(used, on=['ticker', 'fiscal_year', 'quarter', 'metric'], how='inner')
    checks['A5_same_basis'] = bad_basis == 0 and leaked.empty
    detail['A5'] = {'removed_rows_present_in_used': len(leaked)}
    retrieved = pd.to_datetime(records['retrieved_at'], utc=True)
    detail['A6'] = {'retrieved_first': str(retrieved.min()), 'retrieved_last': str(retrieved.max()),
                    'retrieval_days': int(retrieved.dt.date.nunique()), 'publications_per_period': 1 if
                    records.groupby(['ticker', 'fiscal_year', 'quarter'])['publication_timestamp'].nunique().max() == 1 else 'MULTIPLE',
                    'first_published_value_column': any('first' in c for c in records.columns), 'second_source': False,
                    'value_source': 'MOPS t163sb04 summary tables retrieved 2026-09-15; publication time from t57sb01 filing list'}
    if 'verification_status' in used.columns:  # V2: official listing audit, one status per row (design D1)
        detail['A6'].update(verification_status=used['verification_status'].value_counts(dropna=False).to_dict(),
                            value_source='v3.1 values kept only where the official t57sb01 listing records no correction for both filings')
        checks['A6_first_published_values'] = bool(used['verification_status'].eq('VERIFIED').all())
    else:  # V1: a single later retrieval and no revision evidence
        checks['A6_first_published_values'] = False
    checks['A7_no_2026'] = bool(matrix['asof_date'].max() <= DEV_END and latest_available <= DEV_END)
    detail['A7'] = {'max_asof': str(matrix['asof_date'].max().date()), 'max_available_used': str(pd.Timestamp(latest_available).date())}
    coverage = []
    for metric, _ in FACTORS.values():
        joined = members.merge(matrix[['asof_date', 'ticker', metric]], on=['asof_date', 'ticker'], how='left')
        daily = joined.groupby('asof_date').agg(members=('ticker', 'size'), valid=(metric, lambda s: int(s.notna().sum())))
        daily['metric'], daily['share'] = metric, daily['valid'] / daily['members']
        coverage.append(daily.reset_index())
    coverage = pd.concat(coverage, ignore_index=True)
    passes = coverage.groupby('metric').apply(lambda g: bool(g['valid'].min() >= 30
                                                            and (g['share'] >= FROZEN_POLICY.min_daily_coverage_ratio).mean() >= FROZEN_POLICY.min_coverage_pass_ratio))
    detail['A8'] = {m: {'min_valid': int(g['valid'].min()), 'median_valid': float(g['valid'].median()), 'mean_share': float(g['share'].mean()),
                        'min_share': float(g['share'].min())} for m, g in coverage.groupby('metric')}
    checks['A8_coverage'] = bool(passes.all())
    status = 'PASS' if all(checks.values()) else 'BLOCKED'
    return {'status': status, 'checks': checks, 'failing': [k for k, v in checks.items() if not v], 'detail': detail}, coverage


def labels(bars):
    from scripts.prepare_h1_signals import frames_of
    frames = frames_of({'price_basis': 'RAW', 'bars': bars})
    close = frames['close']
    out = {h: (close.shift(-h) / close - 1).stack().rename(f'forward_return_{h}d') for h in (20, 60)}
    frame = pd.concat(out.values(), axis=1).reset_index()
    frame.columns = ['asof_date', 'ticker', 'forward_return_20d', 'forward_return_60d']
    return frames, frame


def factor_study(bars, members, matrix, gate_open):
    from core.research import fundamental_factor_validation as ffv
    from core.research.d5_research_variant import raw_factor_panels
    from core.research.benchmark_v2 import equal_weight_total_return
    from scripts import track_b_stats as stats
    frames, label = labels(bars)
    base = members.merge(matrix, on=['asof_date', 'ticker'], how='left').merge(label, on=['asof_date', 'ticker'], how='left')
    sessions = list(frames['close'].index)
    target = int(members.groupby('asof_date').size().median())
    specs = {c.candidate_id: c for c in ffv.FROZEN_CANDIDATES if c.candidate_id in FACTORS}
    tables = {k: [] for k in ('summary', 'stability', 'rank', 'quantile', 'coverage')}
    for factor_id, spec in specs.items():
        work = ffv.normalize_factor(base, spec).merge(base[['asof_date', 'ticker', 'forward_return_20d', 'forward_return_60d']], on=['asof_date', 'ticker'])
        coverage, ic, rank, quantile, turnover, persistence = ffv._evaluate_daily(work, target)
        tables['summary'].append(ffv._summary(ic, rank, quantile, coverage, turnover, persistence, spec))
        tables['stability'].append(ffv.temporal_stability(rank, quantile, coverage, ic))
        tables['rank'].append(rank)
        tables['quantile'].append(quantile)
        tables['coverage'].append(coverage)
    tables = {k: pd.concat(v, ignore_index=True) for k, v in tables.items()}
    last = tables['rank'].dropna(subset=['aligned_rank_ic']).groupby('horizon')['asof_date'].max()
    label_end = {int(h): str(sessions[sessions.index(d) + h].date()) for h, d in last.items()}
    if max(label_end.values()) > '2025-12-31':
        raise ValueError('OOS_LABEL_LEAK')
    source = ffv._source_coverage(ROOT / 'data/processed/fundamental_pit_v3', tuple(specs.values()))
    gate = ffv._gate(tables['summary'], tables['stability'], source, pd.DataFrame(columns=['factor_id', 'crosscheck_status']))
    gate['if_admitted'] = gate['verdict'].map(MAP)
    gate['decision'] = gate['if_admitted'] if gate_open else 'DATA_BLOCKED'
    momentum = raw_factor_panels(frames)['near_high_252d'].stack().rename('near_high_252d').reset_index()
    momentum.columns = ['asof_date', 'ticker', 'near_high_252d']
    ranks = base.merge(momentum, on=['asof_date', 'ticker'], how='left')
    cols = ['operating_income_yoy', 'eps_yoy', 'near_high_252d']
    ranked = ranks.groupby('asof_date')[cols].rank(pct=True)
    ranked['asof_date'] = ranks['asof_date']
    pairs = [('operating_income_yoy', 'eps_yoy'), ('operating_income_yoy', 'near_high_252d'), ('eps_yoy', 'near_high_252d')]
    daily_corr = ranked.groupby('asof_date').apply(lambda g: pd.Series({f'{a}~{b}': g[a].corr(g[b]) for a, b in pairs}))
    correlation = daily_corr.mean().rename('mean_daily_spearman').reset_index().rename(columns={'index': 'pair'})
    events = json.loads((ROOT / 'outputs/research_mode/prepared_v6/actions.json').read_text(encoding='utf-8'))
    proxy = equal_weight_total_return(bars, events['actions'], events['unresolved_events'])['returns']
    regime = stats.regimes((1 + proxy).cumprod())  # RESEARCH-SCREEN:V2 proxy, 200-session rule
    rank_regime = tables['rank'].assign(regime=tables['rank']['asof_date'].map(regime))
    by_regime = rank_regime.groupby(['factor_id', 'horizon', 'regime'])['aligned_rank_ic'].agg(['mean', 'count']).reset_index()
    return tables, gate, correlation, by_regime, label_end, target


def run():
    workflow.require_registered(FACTOR_ID)
    OUT.mkdir(parents=True, exist_ok=True)
    bars, members, records, matrix, sources = load()
    result, coverage = admission(members, records, matrix)
    coverage.to_csv(OUT / 'admission_coverage.csv', index=False)
    _write(OUT / 'admission.json', {**result, 'inputs_sha256': sources, 'label': 'DIAGNOSTIC_ONLY'})
    tables, gate, correlation, by_regime, label_end, target = factor_study(bars, members, matrix, result['status'] == 'PASS')
    for name, frame in tables.items():
        frame.to_csv(OUT / f'factor_{name}.csv', index=False)
    gate.to_csv(OUT / 'factor_gate.csv', index=False)
    correlation.to_csv(OUT / 'factor_correlation.csv', index=False)
    by_regime.to_csv(OUT / 'factor_ic_by_regime.csv', index=False)
    ready = gate.loc[gate['decision'].eq('READY_FOR_STRATEGY_TEST'), 'factor_id'].tolist()
    h4 = ('RUN' if result['status'] == 'PASS' and ready else 'BLOCKED' if result['status'] != 'PASS' else 'STOP')
    summary = _write(OUT / 'factor_result.json', {
        'hypothesis_id': FACTOR_ID, 'label': 'DIAGNOSTIC_ONLY', 'admission': result['status'], 'admission_failing': result['failing'],
        'decision': dict(zip(gate['factor_id'], gate['decision'])), 'if_admitted': dict(zip(gate['factor_id'], gate['if_admitted'])),
        'h4_disposition': h4, 'h4_ready_factors': ready, 'label_end_by_horizon': label_end, 'coverage_denominator_median_members': target,
        'inputs_sha256': sources, 'code_sha256': {p: workflow.sha256(ROOT / p) for p in (
            'scripts/research_fundamental_g2g3.py', 'core/research/fundamental_factor_validation.py', 'scripts/prepare_h1_signals.py')},
        'research_ledger_rows': len(workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER))})
    workflow.record_result(FACTOR_ID, json.dumps(summary['decision']), str((OUT / 'factor_result.json').relative_to(ROOT).as_posix()),
                           f"admission={result['status']} failing={','.join(result['failing'])} h4={h4}")
    return summary


def charts():
    """Six factor charts from the saved study outputs only (no recomputation)."""
    from scripts.research_failure_review import _figure
    note = 'DIAGNOSTIC_ONLY；准入 BLOCKED（A4、A6），數值未經首次公告值認證｜標籤為 prepared_v6 原始收盤價'
    rank = pd.read_csv(OUT / 'factor_rank.csv', parse_dates=['asof_date'])
    roll = rank.pivot_table(index='asof_date', columns=['factor_id', 'horizon'], values='aligned_rank_ic').rolling(60, min_periods=20).mean()
    roll.columns = [f'{f} h{h}' for f, h in roll.columns]

    def ts(ax):
        roll.plot(ax=ax)
        ax.axhline(0, color='k', lw=1)
        ax.set_ylabel('Rank IC，60 日滾動平均')
    made = [_figure('01_ic_timeseries', 'G2／G3 Rank IC 時序', note, roll.reset_index(), ts, out=DOCS)]
    fold = pd.read_csv(OUT / 'factor_stability.csv')
    fold = fold[fold.fold_id.astype(str) != '2026'].pivot_table(index='fold_id', columns=['factor_id', 'horizon'], values='rank_ic')
    fold.columns = [f'{f} h{h}' for f, h in fold.columns]

    def yearly(ax):
        fold.plot.bar(ax=ax, rot=0)
        ax.axhline(0, color='k', lw=1)
        ax.set_ylabel('年度平均 Rank IC')
    made.append(_figure('02_annual_ic', 'G2／G3 年度 IC（2023、2024、2025）', note, fold.reset_index(), yearly, out=DOCS))
    quant = pd.read_csv(OUT / 'factor_quantile.csv')
    qmean = quant.groupby(['factor_id', 'horizon'])[[f'q{q}_return' for q in range(1, 6)]].mean()

    def quantiles(ax):
        qmean.T.plot.bar(ax=ax, rot=0)
        ax.set_ylabel('平均前瞻報酬')
    made.append(_figure('03_quantile_returns', 'G2／G3 五分位平均前瞻報酬', note, qmean.reset_index(), quantiles, out=DOCS))
    spread = quant[quant.horizon == 20].pivot_table(index='asof_date', columns='factor_id', values='q5_minus_q1').fillna(0).cumsum() / 20

    def cumulative(ax):
        spread.plot(ax=ax)
        ax.set_ylabel('Q5−Q1 前瞻 20 日報酬累積和 ÷ 20（重疊樣本，只看形狀）')
    made.append(_figure('04_cumulative_spread', 'G2／G3 累積分位差', note, spread.reset_index(), cumulative, out=DOCS))
    corr = pd.read_csv(OUT / 'factor_correlation.csv')
    names = ['operating_income_yoy', 'eps_yoy', 'near_high_252d']
    matrix = pd.DataFrame(np.eye(3), index=names, columns=names)
    for pair, value in zip(corr['pair'], corr['mean_daily_spearman']):
        a, b = pair.split('~')
        matrix.loc[a, b] = matrix.loc[b, a] = value

    def heat(ax):
        image = ax.imshow(matrix, vmin=-1, vmax=1, cmap='RdBu_r')
        ax.set_xticks(range(3), names)
        ax.set_yticks(range(3), names)
        for i in range(3):
            for j in range(3):
                ax.text(j, i, f'{matrix.iat[i, j]:.2f}', ha='center', va='center', fontsize=16)
        ax.figure.colorbar(image, ax=ax)
    made.append(_figure('05_correlation_heatmap', 'G2／G3 與動能 near_high_252d 的相關', note + '｜每日橫斷面 Spearman 平均',
                        matrix.reset_index(names='factor'), heat, out=DOCS))
    cover = pd.read_csv(OUT / 'admission_coverage.csv', parse_dates=['asof_date'])

    def coverage(axes):
        for metric, g in cover.groupby('metric'):
            axes[0].plot(g.asof_date, g.valid, label=metric)
            axes[1].plot(g.asof_date, g.share, label=metric)
        axes[0].axhline(30, color='r', ls='--', label='最大 Top-N 30')
        axes[0].set_ylabel('有效檔數')
        axes[1].axhline(.2, color='r', ls='--', label='最低覆蓋 20%')
        axes[1].set_ylabel('占 prepared_v6 成員比例')
        axes[1].set_ylim(0, 1)
        for ax in axes:
            ax.legend()
    made.append(_figure('06_coverage', 'G2／G3 每日有效股票數與覆蓋率', note, cover, coverage, ncols=2, out=DOCS))
    result = json.loads((OUT / 'factor_result.json').read_text(encoding='utf-8'))
    summary = pd.read_csv(OUT / 'factor_summary.csv').merge(pd.read_csv(OUT / 'factor_gate.csv')[['factor_id', 'verdict', 'if_admitted', 'decision', 'reason']], on='factor_id')
    summary.assign(admission=result['admission'], label='DIAGNOSTIC_ONLY').to_csv(DOCS / 'g2g3_factor_results.csv', index=False, encoding='utf-8-sig')
    pd.read_csv(H4_GRID).assign(role=lambda g: np.where(g.strategy_id == H4_PRIMARY, 'PRIMARY', 'SENSITIVITY'), status='BLOCKED',
                                reason='not run: admission ' + result['admission'] + ' (' + ','.join(result['admission_failing']) + ')',
                                total_return_2x='NOT_AVAILABLE', alpha_annual='NOT_AVAILABLE', r1_to_r5='NOT_AVAILABLE').to_csv(
        DOCS / 'h4_config_results.csv', index=False, encoding='utf-8-sig')
    return made


def compare():
    """V1 (unverified values) against V2 (official-listing verified) from saved outputs: comparison table and four charts."""
    from scripts.research_failure_review import _figure
    use('V2')
    dirs = {'V1 未驗證': ROOT / 'outputs/research_mode/g2g3_factor_dev_v1', 'V2 已驗證': OUT}
    comp = pd.concat([pd.read_csv(d / 'factor_summary.csv').merge(pd.read_csv(d / 'factor_gate.csv')[['factor_id', 'decision', 'if_admitted']], on='factor_id')
                      .assign(version=v) for v, d in dirs.items()])
    keep = ['version', 'factor_id', 'horizon', 'aligned_rank_ic', 'icir', 'positive_rank_ic_ratio', 'q5_minus_q1', 'quantile_monotonicity',
            'mean_coverage', 'valid_ic_days', 'decision', 'if_admitted']
    comp[keep].to_csv(DOCS / 'g2g3_comparison.csv', index=False, encoding='utf-8-sig')
    note = 'V1：v3.1 未驗證數值，准入 BLOCKED｜V2：官方清單無更正紀錄的申報，准入 PASS｜標籤為 prepared_v6 原始收盤價'
    comp['key'] = comp['factor_id'].str[:2] + ' h' + comp['horizon'].astype(str)

    def ic(axes):
        for ax, col, label in ((axes[0], 'aligned_rank_ic', '平均 Rank IC'), (axes[1], 'icir', 'ICIR')):
            comp.pivot(index='key', columns='version', values=col).plot.bar(ax=ax, rot=0)
            ax.set_ylabel(label)
    made = [_figure('g2g3_ic_comparison', 'G2／G3 修復前後 IC 與 ICIR', note, comp[keep + ['key']], ic, ncols=2, out=DOCS)]
    folds = pd.concat([pd.read_csv(d / 'factor_stability.csv').assign(version=v) for v, d in dirs.items()])
    folds = folds[folds['fold_id'].astype(str) != '2026']
    folds['key'] = folds['factor_id'].str[:2] + ' h' + folds['horizon'].astype(str) + ' ' + folds['version']

    def annual(ax):
        folds.pivot(index='fold_id', columns='key', values='rank_ic').plot.bar(ax=ax, rot=0)
        ax.set_ylabel('年度平均 Rank IC')
    made.append(_figure('g2g3_annual_ic', 'G2／G3 年度 IC：修復前後', note, folds, annual, out=DOCS))
    quant = pd.read_csv(OUT / 'factor_quantile.csv').groupby(['factor_id', 'horizon'])[[f'q{q}_return' for q in range(1, 6)]].mean()

    def quantiles(ax):
        quant.T.plot.bar(ax=ax, rot=0)
        ax.set_ylabel('平均前瞻報酬（V2 已驗證）')
    made.append(_figure('g2g3_quantile_returns', 'G2／G3 五分位平均前瞻報酬（V2）', note, quant.reset_index(), quantiles, out=DOCS))
    cover = pd.concat([pd.read_csv(d / 'admission_coverage.csv', parse_dates=['asof_date']).assign(version=v) for v, d in dirs.items()])

    def coverage(axes):
        for (metric, version), g in cover.groupby(['metric', 'version']):
            axes[0].plot(g.asof_date, g.valid, label=f'{metric} {version}')
            axes[1].plot(g.asof_date, g.share, label=f'{metric} {version}')
        axes[0].axhline(30, color='r', ls='--', label='最大 Top-N 30')
        axes[1].axhline(.2, color='r', ls='--', label='最低覆蓋 20%')
        axes[0].set_ylabel('有效檔數')
        axes[1].set_ylabel('占成員比例')
        axes[1].set_ylim(0, 1)
        for ax in axes:
            ax.legend(fontsize=9)
    made.append(_figure('g2g3_coverage', 'G2／G3 有效股票數與覆蓋率：修復前後', note, cover, coverage, ncols=2, out=DOCS))
    return made


def h4_charts():
    """Four H4 charts from report_h4_v2 and the archived h4 run ledgers; nothing is drawn if H4 did not run."""
    import csv
    from scripts.research_failure_review import _figure, _nav
    use('V2')
    report = json.loads((ROOT / 'outputs/research_mode/report_h4_v2/screen_results.json').read_text(encoding='utf-8'))
    result = json.loads((ROOT / 'outputs/research_mode/report_h4_v2/h4_result.json').read_text(encoding='utf-8'))
    grid = [r['strategy_id'] for r in csv.DictReader(H4_GRID.open(encoding='utf-8'))]
    rows = pd.DataFrame([{**{k: r.get(k) for k in ('config_id', 'status', 'total_return_2x', 'alpha_annual', 'alpha_p_hac', 'max_drawdown_2x', 'sharpe_2x')},
                          **{g: (r.get('gates') or {}).get(g) for g in ('r1', 'r2', 'r3', 'r4', 'r5')}} for r in report['rows']])
    rows['label'] = rows['config_id'].str.replace('H4_GROWTH_MOMENTUM__', '')
    rows['primary'] = rows['config_id'].eq(H4_PRIMARY)
    note = f"H4:GROWTH-MOMENTUM-V2｜RESEARCH-SCREEN:V2｜2x 成本｜判定 {result['decision']}（只看主要設定）"
    bench = pd.read_csv(ROOT / 'outputs/research_mode/validation_execution_v1/benchmark_series.csv', index_col='date', parse_dates=True)
    curves = {c: _nav(ROOT / 'outputs/research_mode/h4_runs_v1' / f'{grid.index(c):03d}') for c in grid}
    h1_grid = [r['strategy_id'] for r in csv.DictReader((ROOT / 'outputs/strategy_research/h1_near_high_252d_v1/strategy_grid.csv').open(encoding='utf-8'))]
    curves['H1 主要設定'] = _nav(ROOT / 'outputs/research_mode/h1_runs_v1' / f"{h1_grid.index('near_high_252d__EQUAL__TOP10__REB60__EQUAL'):03d}")
    curves['V2 代理（含息）'] = (1 + bench['v2_total']).cumprod()
    equity = pd.DataFrame(curves)

    colors = {H4_PRIMARY: '#c0392b', 'H1 主要設定': '#e67e22', 'V2 代理（含息）': '#222'}

    def eq(ax):
        for c in equity:
            main = c in (H4_PRIMARY, 'H1 主要設定', 'V2 代理（含息）')
            ax.plot(equity.index, equity[c], lw=3 if main else 1, alpha=1 if main else .35, color=colors.get(c, '#9ab'),
                    label=c.replace('H4_GROWTH_MOMENTUM__', 'H4 ') + ('（主要）' if c == H4_PRIMARY else '') if main else None)
        ax.set_ylabel('淨值（起始 1.0，2x 成本後）')
        ax.legend()
    made = [_figure('h4_equity_vs_benchmark', 'H4 成長＋動能與 V2 代理的累積報酬', note + '｜細線為 11 組敏感度設定',
                    equity.reset_index(names='date'), eq, out=DOCS)]
    primary_nav = equity[H4_PRIMARY].dropna()
    dd = pd.DataFrame({'H4 主要設定': primary_nav / primary_nav.cummax() - 1,
                       'V2 代理': equity['V2 代理（含息）'] / equity['V2 代理（含息）'].cummax() - 1})

    def draw(axes):
        dd.plot(ax=axes[0])
        axes[0].set_ylabel('回撤')
        axes[1].barh(rows['label'], rows['max_drawdown_2x'], color=['#c33' if p else '#69c' for p in rows['primary']])
        axes[1].set_xlabel('2x 成本後最大回撤')
    made.append(_figure('h4_drawdown', 'H4 回撤', note, pd.concat([dd.reset_index(names='date'), rows], ignore_index=True), draw, ncols=2, out=DOCS))
    gates = rows.set_index('label')[['r1', 'r2', 'r3', 'r4', 'r5']].astype(float)
    dsr = pd.Series(result['primary_dsr_by_trial_count'] or {}, name='dsr')

    def screen(axes):
        axes[0].imshow(gates, cmap='RdYlGn', vmin=0, vmax=1, aspect='auto')
        axes[0].set_xticks(range(5), gates.columns)
        axes[0].set_yticks(range(len(gates)), gates.index)
        for i in range(len(gates)):
            for j in range(5):
                axes[0].text(j, i, 'PASS' if gates.iat[i, j] else 'FAIL', ha='center', va='center', fontsize=9)
        axes[1].bar(dsr.index, dsr.values, color='#69c')
        axes[1].axhline(.95, color='r', ls='--', label='門檻 0.95')
        axes[1].set_xlabel('試驗數')
        axes[1].set_ylabel('主要設定 DSR')
        axes[1].set_ylim(0, 1)
        axes[1].legend()
    made.append(_figure('h4_r1_r5_screen', 'H4 r1～r5 篩選與 DSR 試驗數敏感度', note + '｜正式 DSR 試驗數為 max(研究 Ledger, 743)',
                        pd.concat([gates.reset_index(), dsr.rename_axis('trials').reset_index()], ignore_index=True), screen, ncols=2, out=DOCS))
    h1 = json.loads((ROOT / 'outputs/research_mode/report_h1_v2/h1_result.json').read_text(encoding='utf-8'))['primary']

    def configs(axes):
        colors = ['#c33' if p else '#69c' for p in rows['primary']]
        axes[0].barh(rows['label'], rows['total_return_2x'], color=colors)
        axes[0].axvline(h1['benchmark_v2_total_return'], color='k', ls='--', label='V2 代理')
        axes[0].axvline(h1['total_return_2x'], color='orange', ls=':', label='H1 主要設定')
        axes[0].set_xlabel('2x 成本後總報酬')
        axes[0].legend()
        axes[1].barh(rows['label'], rows['alpha_annual'], color=colors)
        axes[1].axvline(0, color='k')
        axes[1].axvline(h1['alpha_annual'], color='orange', ls=':', label='H1 主要設定')
        axes[1].set_xlabel('alpha／年（V2，HAC）')
        axes[1].legend()
    made.append(_figure('h4_config_comparison', 'H4 十二組設定（紅色為主要設定）', note, rows, configs, ncols=2, out=DOCS))
    return made


if __name__ == '__main__':
    command, version = sys.argv[1], sys.argv[2]  # e.g. register V2 FACTOR | run V2 | charts V1 | compare V2 | h4_charts V2
    use(version)
    result = {'register': lambda: register(sys.argv[3]), 'run': run, 'charts': charts, 'compare': compare, 'h4_charts': h4_charts}[command]()
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
