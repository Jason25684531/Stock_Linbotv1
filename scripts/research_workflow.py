"""Research workflow on the native engine, in FinLab-style layers: standardized diagnostics, native-vs-FinLab reconciliation, hypothesis registry, research shadow.

Reads archived results only: no backtest rerun, no strict-OOS column (oos_*), no Frozen/Fresh OOS ledger, no MySQL, no FinLab download or upload.
Every output is labeled DIAGNOSTIC_ONLY or RESEARCH_ONLY; nothing here is a formal performance verdict.
"""
import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import date as _date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / 'outputs/research_workflow/2026-10-24'
DT = ROOT / 'outputs/dual_track_validation/2026-10-23'
FACTOR_POOL = ROOT / 'outputs/factor_validation/factor_validation_20260910_v1/accepted_factor_pool.csv'
STABILITY = ROOT / 'outputs/factor_validation/factor_validation_20260910_v1/factor_temporal_stability.csv'
DATA_STATUS = ROOT / 'outputs/p0_p4/2026-10-24/p0_strategy_data_status_r2.csv'
P1 = ROOT / 'outputs/p0_p4/2026-10-24/p1_day3_d5_diagnostics_r2.csv'
REGISTRY = ROOT / 'outputs/research_workflow/hypothesis_registry.csv'
SHADOW = ROOT / 'outputs/research_shadow'
MI_INDEX = ROOT / 'artifacts/official_data_recovery_cache/mi_index'
TRIALS = 743  # trial count the archived DSR used (statistical_evidence.json)
LABELS = ('DIAGNOSTIC_ONLY', 'RESEARCH_ONLY')
SIGNAL_HASHES = ('strategy_version', 'code_sha256', 'config_sha256', 'inputs_sha256')  # frozen version of code, parameters and data per signal
REGISTRY_FIELDS = ('hypothesis_id', 'strategy', 'hypothesis', 'falsification_test', 'data_window', 'trial_budget', 'registered_at', 'status', 'source')


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def undefined(value, reason):
    """AGENTS.md: an undefined metric is value=None with a reason, never 0 or infinity."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return {'value': None, 'reason': reason}
    return {'value': value, 'reason': None}


def fmt(cell, spec='{:.3f}'):
    return f"None（{cell['reason']}）" if cell['value'] is None else spec.format(cell['value'])


# ---------------------------------------------------------------- diagnostics

def factor_effect(members):
    """IC evidence from the 2026-09-10 validation; oos_* columns are never loaded, so strict OOS stays unread."""
    import pandas as pd
    columns = [c for c in pd.read_csv(FACTOR_POOL, nrows=0).columns if not c.startswith('oos_')]
    pool = pd.read_csv(FACTOR_POOL, usecols=columns).set_index('candidate_id')
    stability = pd.read_csv(STABILITY)
    rows = []
    for factor in members:
        if factor not in pool.index:
            rows.append({'factor': factor, 'status': 'NOT_IN_FACTOR_POOL', 'reason': 'not in accepted_factor_pool.csv'})
            continue
        r = pool.loc[factor]
        years = stability[(stability.factor_id == factor) & (stability.horizon == r.selected_horizon)]
        rows.append({'factor': factor, 'status': r.implementation_status, 'horizon': int(r.selected_horizon), 'mean_ic': r.mean_ic, 'icir': r.icir,
                     'positive_ic_ratio': r.positive_ic_ratio, 'q5_minus_q1': r.q5_minus_q1, 'valid_ic_days': int(r.valid_ic_days),
                     'by_year_ic': {str(y.period): round(y.mean_ic, 4) for y in years.itertuples()}})
    return rows


def diagnose(strategy):
    """Standardized DIAGNOSTIC_ONLY report for one family_name from the archived 384-row results (348 Day3/D5 rows); nothing is rerun."""
    import pandas as pd
    b = pd.read_csv(DT / 'backtest_results.csv', encoding='utf-8-sig')
    g = b[b.family_name == strategy].copy()
    if g.empty:
        raise ValueError(f'{strategy} is not in the archived results')
    status = pd.read_csv(DATA_STATUS, encoding='utf-8-sig').set_index('family_name')
    members = strategy.split('+') if strategy.startswith(('A1', 'RS_')) else []
    g['cost_drag'] = g.gross_total_return - g.total_return
    regime = g.regime.map(json.loads)
    g['bull_excess'], g['bear_excess'] = (regime.map(lambda r, k=k: r[k]['annualized_excess']) for k in ('bull', 'bear'))
    g['bear_sessions'] = regime.map(lambda r: r['bear']['sessions'])
    gates = [c for c in g.columns if c[0] == 'g' and c[1].isdigit()]

    def span(col):
        values = g[col].dropna()
        reason = f'{col} is empty for this family in the archived results'
        return {k: undefined(float(getattr(values, k)()) if len(values) else None, reason) for k in ('min', 'median', 'max')}

    report = {
        'strategy': strategy, 'label': 'DIAGNOSTIC_ONLY', 'family': g.family.iloc[0], 'configurations': len(g), 'window': f'{g.start.min()}..{g.end.max()}',
        'factor_effect': factor_effect(members) or [{'factor': strategy, 'status': 'NOT_IN_FACTOR_POOL',
                                                     'reason': 'D5 is a 5-factor composite; its members have NO_DATA in the 2026-09-10 validation'}],
        **{col: span(col) for col in ('sharpe_net', 'sharpe_net_2x_cost', 'excess_sharpe', 'max_drawdown', 'annual_turnover', 'cost_drag', 'top5_assets_profit_share',
                                     'bull_excess', 'bear_excess')},
        'bear_sessions': int(g.bear_sessions.max()),
        'statistics': {'trials': TRIALS, 'best_bootstrap_lower': span('bootstrap_lower')['max'], 'best_deflated_sharpe': span('deflated_sharpe')['max'],
                       'gate_pass_counts': {c: int(g[c].astype(str).isin(['True', 'PASS']).sum()) for c in gates}},
        'data_limits': {'DATA': status.loc[strategy, 'DATA'] if strategy in status.index else None,
                        'blockers': status.loc[strategy, 'blockers'] if strategy in status.index else 'not in p0 status',
                        'total_return_status': sorted(g.total_return_status.unique()),
                        'configs_on_nonofficial_prices': int((g.held_on_nonofficial_prices > 0).sum()),
                        'configs_holding_mixed_events': int((g.held_through_mixed_events > 0).sum())},
        'sources': {p.relative_to(ROOT).as_posix(): sha256(p) for p in (DT / 'backtest_results.csv', FACTOR_POOL, STABILITY, DATA_STATUS)},
    }
    return report, g


def render(r):
    def factor_row(f):
        if 'mean_ic' not in f:
            return f"| {f['factor']} | {f['status']} | | None | None | None | None | {f.get('reason', '')} |"
        return (f"| {f['factor']} | {f['status']} | {f['horizon']} | {f['mean_ic']:.4f} | {f['icir']:.2f} | {f['positive_ic_ratio']:.2f} | "
                f"{f['q5_minus_q1']:.4f} | {f['by_year_ic']} |")

    def row(name, key, spec='{:.3f}'):
        return f"| {name} | {fmt(r[key]['min'], spec)} | {fmt(r[key]['median'], spec)} | {fmt(r[key]['max'], spec)} |"

    s, d = r['statistics'], r['data_limits']
    lines = [
        f"# {r['strategy']} 標準化診斷報告", '',
        f"**標記：{r['label']}**。來源為既有回測結果（{r['configurations']} 組設定，{r['window']}），未重跑，未讀取 strict OOS 欄位（`oos_*`）。"
        '資料尚未認證，本報告**不是**正式績效驗證。', '',
        '## 1. 因子效果（2026-09-10 因子驗證，僅 train＋validation）',
        '| 因子 | 實作狀態 | 持有期（日） | 平均 IC | ICIR | IC>0 比例 | Q5−Q1 | 各年 IC |', '|---|---|---|---|---|---|---|---|',
        *map(factor_row, r['factor_effect']), '',
        '## 2. 績效與交易成本（各設定的最小值／中位數／最大值）', '| 指標 | 最小 | 中位數 | 最大 |', '|---|---|---|---|',
        row('淨 Sharpe（基準成本）', 'sharpe_net'), row('淨 Sharpe（2 倍成本）', 'sharpe_net_2x_cost'),
        row('超額 Sharpe（對 TAIEX 報酬指數）', 'excess_sharpe'), row('最大回撤', 'max_drawdown', '{:.1%}'),
        row('年化週轉（倍）', 'annual_turnover', '{:.2f}'), row('成本拖累（毛報酬 − 淨報酬）', 'cost_drag', '{:.1%}'),
        row('前 5 檔獲利占比', 'top5_assets_profit_share', '{:.0%}'),
        row('多頭年化超額', 'bull_excess', '{:+.1%}'), row(f"空頭年化超額（{r['bear_sessions']} 個交易日）", 'bear_excess', '{:+.1%}'), '',
        '成本口徑：手續費 0.1425%、證交稅 0.3%、滑價 0.1%（`config/settings.py`）；壓力情境各取兩倍。', '',
        '## 3. 統計可信度',
        f"- 試驗次數：{s['trials']}（Deflated Sharpe 依此扣除多重測試）。",
        f"- 最佳 bootstrap 下界：{fmt(s['best_bootstrap_lower'])}；最佳 Deflated Sharpe：{fmt(s['best_deflated_sharpe'])}。",
        f"- 各閘門通過的設定數（照抄封存結果）：{s['gate_pass_counts']}。g5 的封存判定不等於空頭有超額，請對照第 2 節的空頭年化超額。",
        '- 這些設定已在開發期間研究過，**不得**當作獨立 OOS。新的優化假設須先登記於 `outputs/research_workflow/hypothesis_registry.csv`。', '',
        '## 4. 資料限制',
        f"- DATA：{d['DATA']}；阻擋原因：{d['blockers']}",
        f"- 總報酬狀態：{d['total_return_status']}；持有權息混合事件的設定：{d['configs_holding_mixed_events']}；"
        f"使用非官方估值價格的設定：{d['configs_on_nonofficial_prices']}", '',
        '## 5. 來源雜湊（SHA-256）', *(f'- `{k}` {v}' for k, v in r['sources'].items()), '']
    return '\n'.join(lines)


def cmd_diagnose(strategies):
    import pandas as pd
    OUT.mkdir(parents=True, exist_ok=True)
    frames = []
    for strategy in strategies:
        report, g = diagnose(strategy)
        (OUT / f'diagnostic_{strategy}.json').write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        (OUT / f'diagnostic_{strategy}.md').write_text(render(report), encoding='utf-8')
        frames.append(g.assign(label='DIAGNOSTIC_ONLY'))
        print(strategy, len(g), 'configurations ->', OUT / f'diagnostic_{strategy}.md')
    pd.concat(frames).to_csv(OUT / 'diagnostic_configs.csv', index=False, encoding='utf-8-sig')


# ---------------------------------------------------------------- D5 component factor evidence

D3_RUN = ROOT / 'artifacts/factors/d3_full_20230103_20260728'
D5_FACTORS = ('momentum_20d', 'momentum_60d', 'momentum_12_1', 'near_high_252d', 'amihud_20d')
DEV_YEARS = ('2023', '2024', '2025')  # strict OOS starts 2026-01-01: 2026.csv is never opened
DEV_END = '2025-12-31'
HORIZONS = (5, 20, 60)


def dev_labels(data, horizons=HORIZONS, dev_end=DEV_END):
    """Blank every forward label whose window (T+1 entry plus h sessions) could reach past dev_end, so no 2026 price enters."""
    import pandas as pd
    data = data.copy()
    data['asof_date'] = pd.to_datetime(data['asof_date'])
    sessions = pd.Index(sorted(data.asof_date.unique()))
    last = sessions.searchsorted(pd.Timestamp(dev_end), side='right') - 1
    position = data.asof_date.map(pd.Series(range(len(sessions)), index=sessions))
    for h in horizons:
        data.loc[position + 1 + h > last, f'forward_return_{h}d'] = float('nan')
    return data


D5_IC_HYPOTHESIS = 'D5_PORTFOLIO_5_FACTORS:DIAG-FACTOR-IC'


def d5_ic_outcome(summary) -> dict:
    """Registered test: a factor survives only if aligned mean IC > 0 and positive-IC ratio >= 0.5 at both 20d and 60d."""
    s = summary[summary.horizon.isin([20, 60])]
    ok = (s.aligned_mean_ic > 0) & (s.aligned_positive_ic_ratio >= 0.5)
    return {f: 'NOT_REJECTED' if bool(g.all()) and len(g) == 2 else 'REJECTED' for f, g in ok.groupby(s.factor_id)}


def cmd_factors():
    import pandas as pd
    require_registered(D5_IC_HYPOTHESIS)
    from core.research.day2_candidates import D4_POLICY
    from core.research.factor_evaluation import compute_annual_results, compute_daily_rank_ic, summarize_ic
    paths = [D3_RUN / 'research_dataset' / f / f'{y}.csv' for f in D5_FACTORS for y in DEV_YEARS]
    data = dev_labels(pd.concat([pd.read_csv(p, low_memory=False) for p in paths], ignore_index=True))
    daily = compute_daily_rank_ic(data, horizons=HORIZONS, policy=D4_POLICY)
    summary = summarize_ic(daily).assign(label='DIAGNOSTIC_ONLY', window=f'2023-01-03..{DEV_END} (labels truncated at {DEV_END})')
    annual = compute_annual_results(daily)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / 'd5_factor_ic_dev.csv', index=False, encoding='utf-8-sig')
    annual.to_csv(OUT / 'd5_factor_ic_dev_by_year.csv', index=False, encoding='utf-8-sig')
    (OUT / 'd5_factor_ic_dev_sources.json').write_text(json.dumps({p.relative_to(ROOT).as_posix(): sha256(p) for p in paths}, indent=1), encoding='utf-8')
    print(summary[['factor_id', 'horizon', 'aligned_mean_ic', 'aligned_icir', 'aligned_positive_ic_ratio', 'aligned_valid_ic_days']].to_string(index=False))
    outcome = d5_ic_outcome(summary)
    record_result(D5_IC_HYPOTHESIS, json.dumps(outcome), (OUT / 'd5_factor_ic_dev.csv').relative_to(ROOT).as_posix())
    print(outcome)


# ---------------------------------------------------------------- native vs FinLab

# (dimension, native evidence, FinLab 2.2.16 default, setting that would align FinLab, aligned)
RECONCILIATION = [
    ('data_source', 'official TWSE MI_INDEX bars, D3 back-adjusted with the official ex-rights reference (scripts/track_b_data.py)',
     'FinLab cloud tables, etl:adj_close (finlab/markets/tw.py)', 'custom market= carrying the native D3 price panel (no download)', False),
    ('signal_time', 'close of signal_date (core/research/strategy_search.py signal_date_rule)', 'position row t uses data up to t', 'same', True),
    ('execution_time', 'T+1 next trading day at the universe entry_price, no ffill (strategy_search.py)',
     "trade_at_price='close' on the bar after the signal", 'trade_at_price=<native T+1 entry-price DataFrame>', False),
    ('fees_tax', 'fee 0.1425 %, tax 0.3 % (config/settings.py FEE_RATE, TAX_RATE)', 'fee_ratio/tax_ratio from market defaults',
     'fee_ratio=0.001425, tax_ratio=0.003', True),
    ('slippage', '0.1 % adverse (config/settings.py SLIPPAGE_RATE); stress track 2x', 'none', 'no parameter: must be folded into a custom trade_at_price', False),
    ('corporate_actions', 'official event factors; 220 mixed 權息 events not splittable (OD-P6), rights-offering terms unknown',
     'etl:adj_close includes ex-dividend adjustment; method not certified against TWT49U', 'not alignable until OD-P6 is resolved', False),
    ('delisting', 'held at last trade and flagged (delistings.csv)', 'finlab/backtest/delisting.py handling', 'equivalence unknown; needs a reconciliation test', False),
    ('limit_up_down', 'locked_buy/locked_sell audited, not filled (track_b_evaluate.audit_orders)', 'no limit-lock fill rule by default',
     'not alignable without a custom fill mask', False),
    ('rebalance', 'every 20 or 60 sessions on discrete execution dates', 'resample=None rebalances on any change, or resample=dates',
     'resample=<native execution dates>', True),
    ('benchmark', 'TAIEX total-return index (發行量加權股價報酬指數)', 'report benchmark from FinLab data', 'compute excess outside FinLab against the same index', True),
    ('metrics', 'daily net excess return; Sharpe, DSR (743 trials), bootstrap lower bound, g1..g5', 'Report.metrics: CAGR, Sharpe, MDD on creturn',
     'Sharpe annualization and risk-free convention not proven equal; needs a test on one shared return series', False),
    ('cloud_upload', 'never', 'upload=None uploads only in cloud-scheduled runs', 'upload=False always (research rule)', True),
]


def comparable(rows) -> bool:
    """Results may be compared only when every dimension is aligned; one mismatch blocks the result comparison."""
    return all(row[-1] for row in rows)


def cmd_compare():
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'backtest_reconciliation.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        w = csv.writer(handle)
        w.writerow(('dimension', 'native', 'finlab_default', 'finlab_aligned_setting', 'status'))
        for *row, aligned in RECONCILIATION:
            w.writerow((*row, 'ALIGNED' if aligned else 'NOT_ALIGNED'))
    verdict = 'COMPARABLE' if comparable(RECONCILIATION) else 'NOT_COMPARABLE'
    print(verdict, 'blocked by:', [r[0] for r in RECONCILIATION if not r[-1]])


# ---------------------------------------------------------------- hypothesis registry

def _head(path) -> Path:
    return Path(path).with_suffix('.head.json')


def _seal(path):
    """Anchor the whole file: an edited or deleted row no longer matches the head. ponytail: file hash, a per-row chain if rows ever need partial proofs."""
    _head(path).write_text(json.dumps({'sha256': sha256(path), 'rows': len(list(csv.DictReader(Path(path).open(encoding='utf-8-sig'))))}), encoding='utf-8')


def verify_registry(path=REGISTRY) -> str:
    path = Path(path)
    if not path.is_file():
        return 'EMPTY'
    if not _head(path).is_file():
        return 'NO_HEAD'
    return 'PASS' if json.loads(_head(path).read_text(encoding='utf-8'))['sha256'] == sha256(path) else 'FAIL'


def register(row: dict, path=REGISTRY, today=None) -> dict:
    """Append one hypothesis before any run. An existing id is never rewritten; a missing field is rejected; a tampered file refuses appends."""
    missing = [f for f in REGISTRY_FIELDS if f not in ('registered_at', 'status') and not str(row.get(f, '')).strip()]
    if missing:
        raise ValueError(f'registration misses {missing}')
    path = Path(path)
    if verify_registry(path) not in ('PASS', 'EMPTY'):
        raise ValueError(f'{path.name} does not match its head ({verify_registry(path)}); nothing is appended')
    existing = list(csv.DictReader(path.open(encoding='utf-8-sig'))) if path.is_file() else []
    if any(r['hypothesis_id'] == row['hypothesis_id'] for r in existing):
        raise ValueError(f"{row['hypothesis_id']} already registered; registrations are never rewritten")
    record = {**{f: row.get(f, '') for f in REGISTRY_FIELDS}, 'registered_at': today or _date.today().isoformat(), 'status': 'REGISTERED_NOT_RUN'}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', newline='', encoding='utf-8-sig') as handle:
        w = csv.DictWriter(handle, fieldnames=REGISTRY_FIELDS)
        if not existing:
            w.writeheader()
        w.writerow(record)
    _seal(path)
    return record


def results_path(path=REGISTRY) -> Path:
    return Path(path).with_name('hypothesis_results.csv')


def require_registered(hypothesis_id: str, path=REGISTRY) -> dict:
    """Run gate: the id must be registered in a verified registry and must not have a result yet (one run per registration)."""
    if verify_registry(path) != 'PASS':
        raise ValueError(f'registry does not verify ({verify_registry(path)}); no run')
    rows = {r['hypothesis_id']: r for r in csv.DictReader(Path(path).open(encoding='utf-8-sig'))}
    if hypothesis_id not in rows:
        raise ValueError(f'{hypothesis_id} is not registered; register it before any run')
    done = results_path(path)
    if done.is_file() and any(r['hypothesis_id'] == hypothesis_id for r in csv.DictReader(done.open(encoding='utf-8-sig'))):
        raise ValueError(f'{hypothesis_id} already has a result; a rerun needs a new registration')
    return rows[hypothesis_id]


def record_result(hypothesis_id: str, outcome: str, evidence: str, note: str = '', path=REGISTRY) -> None:
    done = results_path(path)
    new = not done.is_file()
    with done.open('a', newline='', encoding='utf-8-sig') as handle:
        w = csv.writer(handle)
        if new:
            w.writerow(('hypothesis_id', 'run_at_utc', 'outcome', 'evidence', 'label', 'note'))
        w.writerow((hypothesis_id, datetime.now(timezone.utc).isoformat(timespec='seconds'), outcome, evidence, 'DIAGNOSTIC_ONLY', note))


def cmd_seed_registry():
    """Carry the P1 hypotheses for D5 and A191_132 (written 2026-10-24, never run) into the registry."""
    import pandas as pd
    p1 = pd.read_csv(P1, encoding='utf-8-sig').set_index('strategy')
    for strategy in ('D5_PORTFOLIO_5_FACTORS', 'A191_132'):
        hyps, tests = p1.loc[strategy, 'improvement_hypothesis'].split(' || '), p1.loc[strategy, 'falsification_test'].split(' || ')
        for hyp, test in zip(hyps, tests):
            register({'hypothesis_id': f"{strategy}:{hyp.split(':')[0]}", 'strategy': strategy, 'hypothesis': hyp, 'falsification_test': test,
                      'data_window': 'development window only (Day3 ..2025-12-31, D5 ..2026-07-28)', 'trial_budget': '1',
                      'source': f'{P1.relative_to(ROOT).as_posix()} sha256 {sha256(P1)}'}, today='2026-10-24')
    print(REGISTRY)


# ---------------------------------------------------------------- research shadow

LISTING = ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd/t187ap03_L.json'
STRATEGY_VERSION = 'A191_132-research-shadow-v2'  # v1 (2026-10-22 record) used build_mask; v2 uses the Day3 membership rule v2
OPEN_UTC_HOUR = 1  # TWSE opens 09:00 Asia/Taipei = 01:00 UTC


def load_sessions():
    """Official calendar as observed: every cached MI_INDEX file classified TRADING_DAY or NON_TRADING_DAY (holidays and typhoon closures alike)."""
    import pandas as pd
    from core.research.sources import RawResponse
    from core.research.sources.twse import ResponseKind, classify
    stamp, days = datetime.now(timezone.utc), {}
    for path in sorted(MI_INDEX.glob('MI_INDEX_*.json')):
        payload = json.loads(path.read_text(encoding='utf-8'))
        days[pd.Timestamp(path.stem[-8:])] = (classify(RawResponse('twse', 'MI_INDEX', {}, stamp, None, payload, None)).kind == ResponseKind.TRADING_DAY, path, payload)
    return days


def a191_132(amount):
    """A191_132 on an official traded-value pivot (sessions x stocks): 20-session mean, min_periods 20 (as day2_candidates._rolling)."""
    return amount.rolling(20).mean()


def a191_132_scores(asof: str, days=None):
    """Rank A191_132 inside the Day3 membership rule v2, using only sessions dated <= asof."""
    import pandas as pd
    from core.research.adjustment.normalization import normalize_company_profile_listing_dates, normalize_twse_closing_table
    from core.research.selection.universe import build_membership_v2
    from core.research.sources.twse import find_closing_table

    days = load_sessions() if days is None else days
    day = pd.Timestamp(asof)
    if not days.get(day, (False,))[0]:
        raise ValueError(f'{asof} is not a trading session in {MI_INDEX}')
    sessions = sorted(d for d, (trading, *_) in days.items() if trading and d <= day)
    if len(sessions) < 60:
        raise ValueError(f'only {len(sessions)} sessions up to {asof}; the v2 liquidity rule needs 60')
    stamp = datetime.now(timezone.utc)
    quotes = pd.concat([normalize_twse_closing_table(find_closing_table(days[d][2]), d, stamp) for d in sessions], ignore_index=True)
    amount = quotes.pivot(index='trade_date', columns='stock_id', values='amount').reindex(sessions)
    score = a191_132(amount).loc[day]
    listing = normalize_company_profile_listing_dates(json.loads(LISTING.read_text(encoding='utf-8')))
    m = build_membership_v2(quotes, listing, trading_calendar=sessions)
    m = m[m.trade_date == day].set_index('stock_id')
    ranked = score[m.member.reindex(score.index, fill_value=False).astype(bool)].dropna().sort_values(ascending=False, kind='stable')
    inputs = {days[d][1].name: sha256(days[d][1]) for d in sessions} | {LISTING.name: sha256(LISTING)}
    return ranked, inputs, m.exclusion_reason.value_counts().to_dict()


CLOSURES = ROOT / 'outputs/research_workflow/twse_closures.json'
OPEN = ('OPEN_OBSERVED', 'EXPECTED_OPEN', 'EXPECTED_OPEN_UNVERIFIED')
UNVERIFIED_SCHEDULE = 'ANNUAL_SCHEDULE_NOT_IMPORTED'  # blocker code; callers match the code, not the wording


def load_closures(path=CLOSURES):
    """Scheduled closures (TWSE annual holiday schedule) and announced temporary closures (typhoon etc.), with the years whose annual schedule is imported."""
    import pandas as pd
    data = json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else {'closures': [], 'annual_schedule_years': []}
    return {pd.Timestamp(c['date']): c for c in data['closures']}, frozenset(data['annual_schedule_years'])


def session_status(day, days, closures=None, years=frozenset()) -> str:
    """Exchange data wins once cached; before that the schedule decides. Weekdays alone never make a session: without the year's schedule it stays UNVERIFIED."""
    closures = closures or {}
    closed = day in closures or day.weekday() >= 5
    if day in days:
        if days[day][0] and closed:
            return 'CONFLICT'  # a scheduled closure with trading data: stop, never pick a side
        return 'OPEN_OBSERVED' if days[day][0] else 'CLOSED_OBSERVED'
    if closed:
        return 'CLOSED_SCHEDULED'
    return 'EXPECTED_OPEN' if day.year in years else 'EXPECTED_OPEN_UNVERIFIED'


def next_session(date: str, days, closures=None, years=frozenset()):
    """(day, status) of the first session after date; None if none within 30 days."""
    import pandas as pd
    for offset in range(1, 31):
        day = pd.Timestamp(date) + pd.Timedelta(days=offset)
        status = session_status(day, days, closures, years)
        if status == 'CONFLICT':
            raise ValueError(f'{day.date()} is a scheduled closure but MI_INDEX shows trading; resolve the calendar first')
        if status in OPEN:
            return day, status
    return None


def deadline_utc(date: str, days, closures=None, years=frozenset()) -> datetime:
    """09:00 Asia/Taipei of the next session: a signal recorded later is never forward evidence."""
    day, _ = next_session(date, days, closures, years)
    return datetime(day.year, day.month, day.day, OPEN_UTC_HOUR, tzinfo=timezone.utc)


def timing(record: dict, days, closures=None, years=frozenset()) -> str:
    """PROSPECTIVE only if recorded before the next session opened and that session is confirmed by exchange data."""
    if record.get('kind') in ('ANNOTATION', 'BLOCKED'):
        return record['kind']
    recorded = datetime.fromisoformat(record['recorded_at_utc'])
    if recorded >= deadline_utc(record['date'], days, closures, years):
        return 'BACKFILLED'
    return 'PROSPECTIVE' if next_session(record['date'], days, closures, years)[1] == 'OPEN_OBSERVED' else 'PENDING_NEXT_SESSION'


def _append(body: dict, root) -> dict:
    root = root or SHADOW  # resolved at call time, so a patched SHADOW (tests) is honoured
    from core.research.shadow_selection import HEAD, LEDGER, _lines, _sha, verify  # ponytail: shares the formal chain format; fork it if that format ever changes
    if verify(root)['status'] != 'PASS':
        raise ValueError('the research ledger chain does not verify; nothing is appended')
    previous = _lines(root)
    body = {**body, 'prev_sha256': previous[-1]['sha256'] if previous else None}
    record = {**body, 'sha256': _sha(body)}
    Path(root).mkdir(parents=True, exist_ok=True)
    with (Path(root) / LEDGER).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')
    (Path(root) / HEAD).write_text(json.dumps({'records': len(previous) + 1, 'sha256': record['sha256']}), encoding='utf-8')
    return record


def _check_date(date: str):
    if _date.fromisoformat(date).isoformat() != date:
        raise ValueError(f'date must be YYYY-MM-DD: {date!r}')


def record_research_day(date: str, picks: list, meta: dict, root=None, backfill=False, days=None, closures=None, years=frozenset(), now=None) -> dict:
    """Append one research signal to a hash-chained ledger kept apart from the qualified shadow (outputs/shadow_selection).

    The session must be confirmed open by exchange data. At or after 09:00 Taipei of the next session the signal is late: refused unless
    backfill=True, which stamps it BACKFILLED. days=None skips the calendar (unit tests of the chain only).
    """
    import pandas as pd
    from core.research.shadow_selection import _lines
    _check_date(date)
    root = root or SHADOW
    if meta.get('label') not in LABELS:
        raise ValueError('research shadow records must be RESEARCH_ONLY or DIAGNOSTIC_ONLY')
    missing = [k for k in SIGNAL_HASHES if not meta.get(k)]
    if missing:
        raise ValueError(f'a research signal must carry its version hashes; missing {missing}')  # older lines are never rewritten
    if any(r.get('kind', 'SIGNAL') == 'SIGNAL' and r['date'] == date and r['strategy'] == meta['strategy'] for r in _lines(root)):
        raise ValueError(f"{meta['strategy']} {date} already recorded; records are never rewritten")
    now = now or datetime.now(timezone.utc)
    late = False
    if days is not None:
        status = session_status(pd.Timestamp(date), days, closures, years)
        if status != 'OPEN_OBSERVED':
            raise ValueError(f'{date} is {status}: only a session confirmed by exchange data can carry a signal')
        late = now >= deadline_utc(date, days, closures, years)
    if late and not backfill:
        raise ValueError(f'the deadline (09:00 Taipei of the session after {date}) has passed: this would be a backfill, pass backfill=True')
    return _append({'kind': 'SIGNAL', 'date': date, **meta, 'picks': picks, 'recorded_at_utc': now.isoformat(timespec='seconds'),
                    'timing_at_record': 'BACKFILLED' if late else 'BEFORE_DEADLINE',
                    'advice': 'NOT_INVESTMENT_ADVICE', 'broker': 'DISABLED'}, root)


def record_blocked(date: str, strategy: str, reason: str, root=None) -> dict:
    """A session that could not produce a valid signal (incomplete data, missed approval): recorded as BLOCKED, never filled in later."""
    _check_date(date)
    return _append({'kind': 'BLOCKED', 'date': date, 'strategy': strategy, 'reason': reason, 'label': 'RESEARCH_ONLY',
                    'recorded_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'broker': 'DISABLED'}, root)


def annotate(refers_to: str, status: str, reason: str, root=None) -> dict:
    """Mark an earlier record without touching it: a new chained line pointing at its sha256."""
    from core.research.shadow_selection import _lines
    root = root or SHADOW
    lines = _lines(root)
    target = [r for r in lines if r['sha256'] == refers_to]
    if not target:
        raise ValueError(f'no record {refers_to}')
    if any(r.get('refers_to_sha256') == refers_to and r.get('status') == status for r in lines):
        raise ValueError(f'{refers_to[:12]} already annotated {status}')
    return _append({'kind': 'ANNOTATION', 'date': target[0]['date'], 'strategy': target[0]['strategy'], 'refers_to_sha256': refers_to, 'status': status,
                    'reason': reason, 'label': 'RESEARCH_ONLY', 'recorded_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds')}, root)


def cmd_shadow(date, top=50, backfill=False):
    from core.research.shadow_selection import verify
    days, (closures, years) = load_sessions(), load_closures()
    ranked, inputs, excluded = a191_132_scores(date, days)
    inputs[CLOSURES.name] = sha256(CLOSURES)
    picks = [{'rank': i + 1, 'stock_id': s, 'score': float(v)} for i, (s, v) in enumerate(ranked.head(top).items())]
    hypotheses = [r['hypothesis_id'] for r in csv.DictReader(REGISTRY.open(encoding='utf-8-sig')) if r['strategy'] == 'A191_132'] if REGISTRY.is_file() else []
    config = {'strategy_version': STRATEGY_VERSION, 'top': top,
              'factor': 'mean(official traded value, 20 sessions, min_periods 20), descending; IMPLEMENTATION_APPROXIMATE (traded value, not VWAP x volume)',
              'universe': 'core.research.selection.universe.build_membership_v2 (twse_research_v2; listing dates from t187ap03_L snapshot of the D3 build)'}
    meta = {'strategy': 'A191_132', 'strategy_version': STRATEGY_VERSION, 'code_sha256': sha256(__file__), 'label': 'RESEARCH_ONLY',
            'factor': config['factor'], 'universe': config['universe'],
            'config_sha256': hashlib.sha256(json.dumps(config, sort_keys=True).encode('utf-8')).hexdigest(),
            'universe_members': int(len(ranked)), 'universe_exclusions': excluded, 'data_asof': date, 'inputs_sha256': inputs,
            'evidence': 'not independent OOS; returns are never computed here', 'hypotheses': hypotheses}
    record = record_research_day(date, picks, meta, backfill=backfill, days=days, closures=closures, years=years)
    print(record['strategy'], record['date'], record['timing_at_record'], len(picks), 'picks', verify(SHADOW))


def cmd_timing():
    from core.research.shadow_selection import _lines
    days, (closures, years), lines = load_sessions(), load_closures(), _lines(SHADOW)
    marked = {r['refers_to_sha256']: r['status'] for r in lines if r.get('kind') == 'ANNOTATION'}
    for r in lines:
        # an annotation (e.g. BACKFILLED_INIT) always overrides the clock: a marked record is never forward evidence
        print(r.get('kind', 'SIGNAL'), r['date'], r['strategy'], marked.get(r['sha256']) or timing(r, days, closures, years), r['sha256'][:12])


def import_holidays(source, path=CLOSURES) -> int:
    """Merge a saved TWSE holiday-schedule response (Owner-approved fetch) into the closure file. Rows that are trading days
    (開始交易 / 最後交易, the first/last trading day notes) are skipped; settlement-only days count as closed.
    ponytail: tolerant of the two known shapes (openapi list of dicts with ROC Date, or {'data': [[name, date, ...]]}); verify on the first real file.
    """
    import re
    raw = json.loads(Path(source).read_text(encoding='utf-8'))
    rows = raw['data'] if isinstance(raw, dict) else [[r.get('Name', ''), r.get('Date', ''), r.get('Weekday', ''), r.get('Description', '')] for r in raw]
    data = json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else {'closures': [], 'annual_schedule_years': []}
    known, added, years = {c['date'] for c in data['closures']}, 0, set()
    for row in rows:
        name, text = str(row[0]), ' '.join(map(str, row))
        digits = re.sub(r'\D', '', str(row[1]))
        if len(digits) == 7:  # ROC yyyMMdd
            digits = f'{int(digits[:3]) + 1911}{digits[3:]}'
        if len(digits) != 8 or '開始交易' in text or '最後交易' in text:
            continue
        day = f'{digits[:4]}-{digits[4:6]}-{digits[6:]}'
        years.add(int(digits[:4]))
        if day not in known:
            data['closures'].append({'date': day, 'kind': 'ANNUAL_HOLIDAY', 'reason': name, 'source': f'{Path(source).name} sha256 {sha256(source)}'})
            known.add(day)
            added += 1
    data['annual_schedule_years'] = sorted(set(data['annual_schedule_years']) | years)
    data['closures'].sort(key=lambda c: c['date'])
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    return added


def readiness(date: str, days, closures, years, now=None) -> dict:
    """Can a PROSPECTIVE signal still be produced for `date`? Read-only; never fetches."""
    import pandas as pd
    from core.research.shadow_selection import _lines, verify
    now = now or datetime.now(timezone.utc)
    day = pd.Timestamp(date)
    status = session_status(day, days, closures, years)
    prior = sum(1 for d, (t, *_) in days.items() if t and d < day)
    deadline = deadline_utc(date, days, closures, years) if status in OPEN else None
    blockers = [b for b, bad in (
        (f'{date} is {status}: do not fetch it as a session', status not in OPEN),
        (f'{UNVERIFIED_SCHEDULE}: {date} annual schedule not imported, weekday-only expectation', status == 'EXPECTED_OPEN_UNVERIFIED'),
        (f'MI_INDEX {date} not cached yet (needs the Owner-approved fetch after the close)', status in ('EXPECTED_OPEN', 'EXPECTED_OPEN_UNVERIFIED')),
        (f'v2 universe needs 60 sessions, {prior + 1} would exist', prior + 1 < 60),
        ('deadline passed: only BACKFILLED or BLOCKED is possible', deadline is not None and now >= deadline),
        ('research ledger chain does not verify', verify(SHADOW)['status'] != 'PASS'),
        ('hypothesis registry seal does not verify', verify_registry() != 'PASS'),
    ) if bad]
    formal = _lines(ROOT / 'outputs/shadow_selection')
    return {'date': date, 'session_status': status, 'prior_sessions_cached': prior, 'deadline_utc': deadline.isoformat() if deadline else None,
            'deadline_taipei': (deadline + pd.Timedelta(hours=8)).strftime('%Y-%m-%d %H:%M Asia/Taipei') if deadline else None,
            'checked_at_utc': now.isoformat(timespec='seconds'), 'formal_shadow_last_status': formal[-1]['status'] if formal else None,
            'blockers': blockers, 'ready_to_record_now': not blockers,
            'steps_after_approval': ['fetch MI_INDEX for the date only (Owner-approved, request gate)', 'python scripts/research_workflow.py audit',
                                     f'python scripts/research_workflow.py ready --date {date}', f'python scripts/research_workflow.py shadow --date {date}',
                                     'if data is incomplete or the deadline has passed: python scripts/research_workflow.py blocked --date ... --reason ...']}


def cmd_ready(date):
    result = readiness(date, load_sessions(), *load_closures())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'readiness_{date}.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))


INIT_SHA = 'db66b6000a28a01b4ccfcd8d1d2ae9dcc5191c11b6ed52be575609e25bc1c9d4'


def cmd_annotate_init():
    print(annotate(INIT_SHA, 'BACKFILLED_INIT', 'written 2026-10-24 for the 2026-10-22 session as a historical initialization test; v1 universe (build_mask, '
                   'not the Day3 rule v2); never forward evidence'))


# ---------------------------------------------------------------- data-audit (read-only; sequences existing checks)

def calendar_gaps(days, today=None, closures=None):
    """Weekdays before today with no MI_INDEX file and no scheduled/announced closure: unknown sessions, never assumed closed."""
    import pandas as pd
    first, last = min(days), pd.Timestamp(today or _date.today())
    return [d.date().isoformat() for d in pd.bdate_range(first, last) if d not in days and d not in (closures or {}) and d < last]


def cmd_audit():
    from core.research.shadow_selection import verify
    from scripts.production_status_check import check
    days, (closures, years) = load_sessions(), load_closures()
    trading = sorted(d for d, (t, *_) in days.items() if t)
    conflicts = [d.date().isoformat() for d in days if session_status(d, days, closures, years) == 'CONFLICT']
    result = {
        'checked_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'label': 'DIAGNOSTIC_ONLY',
        'calendar': {'latest_session': trading[-1].date().isoformat(), 'sessions_cached': len(trading),
                     'non_trading_days': [d.date().isoformat() for d, (t, *_) in days.items() if not t], 'weekday_files_missing': calendar_gaps(days, closures=closures),
                     'scheduled_or_announced_closures': {d.date().isoformat(): c['reason'] for d, c in sorted(closures.items())},
                     'annual_schedule_years': sorted(years), 'conflicts': conflicts},
        'research_shadow': verify(SHADOW), 'formal_shadow': verify(ROOT / 'outputs/shadow_selection'),
        'hypothesis_registry': verify_registry(),
        'production_status': {k: v for k, v in check(trading[-1].date().isoformat()).items() if k in ('classification', 'asof_date', 'reasons', 'content_is_blocking')},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'data_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('diagnose', 'compare', 'seed-registry', 'factors', 'shadow', 'verify-shadow', 'timing', 'annotate-init', 'audit',
                                                'ready', 'blocked', 'import-holidays'))
    parser.add_argument('--backfill', action='store_true')
    parser.add_argument('--reason')
    parser.add_argument('--file')
    parser.add_argument('--strategy', action='append')
    parser.add_argument('--date')
    args = parser.parse_args()
    if args.command == 'diagnose':
        cmd_diagnose(args.strategy or ['D5_PORTFOLIO_5_FACTORS', 'A191_132'])
    elif args.command == 'shadow':
        cmd_shadow(args.date, backfill=args.backfill)
    elif args.command == 'ready':
        cmd_ready(args.date)
    elif args.command == 'blocked':
        print(record_blocked(args.date, 'A191_132', args.reason))
    elif args.command == 'import-holidays':
        print(import_holidays(args.file), 'closures added')
    elif args.command == 'verify-shadow':
        from core.research.shadow_selection import verify
        print(verify(SHADOW))
    else:
        {'compare': cmd_compare, 'seed-registry': cmd_seed_registry, 'factors': cmd_factors, 'timing': cmd_timing, 'annotate-init': cmd_annotate_init, 'audit': cmd_audit}[args.command]()
