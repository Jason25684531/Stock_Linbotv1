"""h4-historical-data-unlock-v1 (design.md): 2015-2022 inputs, fundamental v3.3-hist, admission, the one registered H4 run, V2 screen.

Commands: inputs | fundamentals | signals | admission | run | screen. Every checkpoint is write-once; the runner rebuilds
prepare_inputs/prepare_signals and compares hashes before admitting the run. No file after 2022-12-30 is opened for prices."""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.research_mode import RESEARCH_ASSUMPTIONS_V6, assumptions_sha, reference_change_policy
from scripts import research_workflow as workflow
from scripts.prepare_research_inputs import align_events, write_once
from scripts.prepare_research_mode import digest

RM = ROOT / 'outputs/research_mode'
RAW_HIST = RM / 'hist_raw_v1'
RAW_LOCAL = ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd'
T1B = ROOT / 'outputs/finlab_recovery/2026-10-24/t1b_official'
DELIST = ROOT / 'artifacts/official_dual_track_cache/suspendListing.json'
PREPARED, SIGNALS, RUNS, OUT = RM / 'prepared_hist_v1', RM / 'h4_hist_signals_v1', RM / 'h4_hist_runs_v1', RM / 'h4_hist_unlock_v1'
SAME_BASIS = ROOT / 'data/processed/fundamental_pit_v3_yoy_same_basis'
FUND = ROOT / 'data/processed/fundamental_pit_v3_3_hist'
GRID = ROOT / 'outputs/strategy_research/h4_growth_momentum_v1/strategy_grid.csv'
FACTOR_RESULT = RM / 'g2g3_factor_dev_v2/factor_result.json'
START, END = '2016-01-04', '2022-12-30'
HID, PRIMARY, KIND, COMPOSITE = 'H4:HIST-2016-2022-V1', 'H4_GROWTH_MOMENTUM__TOP10__REB60__EQUAL', 'H4_HIST_RAW_REPRODUCIBLE_V1', 'H4_GROWTH_MOMENTUM'
GROWTH = {'G2_OPERATING_INCOME_YOY': 'operating_income_yoy', 'G3_EPS_YOY': 'eps_yoy'}
V2_VARIANCE = 0.00270516167404164  # trial Sharpe variance recorded by the H4-V2 screen over 242 configurations (design D5)
LABELS = {'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True, 'terms_status': 'RESEARCH_ASSUMPTIONS'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def roc(text):
    year, month, day = (int(v) for v in str(text).replace('年', '/').replace('月', '/').replace('日', '').split('/'))
    return date(year + 1911, month, day).isoformat()


def planned_quotes():
    return [d.strftime('%Y%m%d') for d in pd.bdate_range('2015-01-01', '2021-12-29')]


def quote_paths():
    """Fetched 2015-01-01..2021-12-29 plus local 2021-12-30..2022-12-30; later local files are never listed or opened."""
    hist = [RAW_HIST / f'MI_INDEX_{d}.json' for d in planned_quotes()]
    local = [RAW_LOCAL / f'MI_INDEX_{d:%Y%m%d}.json' for d in pd.bdate_range('2021-12-30', END)]
    return [p for p in hist + local if p.is_file()]


def event_sources():
    """TWTAUU/TWTB8U: fetched 2015-2021 rows plus local 2022 rows (rows after 2022-12-30 dropped before use), written once."""
    folder = OUT / 'event_sources'
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, kind in (('TWTAUU', 'CAPITAL_REDUCTION'), ('TWTB8U', 'PAR_VALUE_CHANGE')):
        hist = load(RAW_HIST / f'{name}_20150101_20211231.json')
        local = json.loads(next(T1B.glob(f'{name}_*+0000.bin')).read_bytes().decode('utf-8'))
        if hist['fields'] != local['fields'] or hist.get('stat') != 'OK' or local.get('stat') != 'OK':
            raise ValueError(f'REFERENCE_SCHEMA_MISMATCH:{name}')
        rows, seen = [], {}
        for r in hist['data'] + [r for r in local['data'] if '2022-01-01' <= roc(r[0]) <= END]:
            key, terms = (r[0], r[1]), (r[3], r[4])
            if key in seen:  # the official list repeats one event per announcement revision; identical terms only
                if seen[key] != terms:
                    raise ValueError(f'CONFLICTING_REFERENCE_TERMS:{name}:{key}')
                continue
            seen[key] = terms
            rows.append(r)
        path = folder / f'{name}_20150101_20221230.json'
        write_once(path, {'stat': 'OK', 'fields': hist['fields'], 'data': rows})
        paths[kind] = path
    return paths


def prepare_inputs(prepared=PREPARED):
    from scripts.research_mode_inputs import build_raw_bars, build_research_universe, load_development_events
    prepared = Path(prepared)
    prepared.mkdir(parents=True, exist_ok=True)
    market = build_raw_bars(quote_paths(), end=END)
    days = sorted(market['bars'])
    if days[0] > '2015-01-06' or days[-1] != END:
        raise ValueError('INCOMPLETE_HISTORICAL_WINDOW')
    market.update(LABELS, assumptions_sha256=assumptions_sha(RESEARCH_ASSUMPTIONS_V6))
    market_sha = write_once(prepared / 'bars.json', market)
    universe = build_research_universe(market)
    universe = universe.loc[universe['asof_date'] >= START]
    universe_sha = write_once(prepared / 'universe.json', universe.to_dict('records'))
    dividend, references = RAW_HIST / 'TWT49U_20150101_20221231.json', event_sources()
    converted = load_development_events(dividend, references, END, reference_policy=reference_change_policy(RESEARCH_ASSUMPTIONS_V6))
    raw = DELIST.read_bytes()
    for row in json.loads(raw.decode('utf-8')):
        day, asset = roc(row['DelistingDate']), str(row.get('Code', ''))
        if day > END or len(asset) != 4 or not asset.isdigit() or asset.startswith('0'):
            continue
        converted['unresolved_events'].append({'ex_date': day, 'stock_id': asset, 'missing_terms': ['delisting_settlement_terms'],
                                               'source_sha256': hashlib.sha256(raw).hexdigest()})
    converted = align_events(converted, set(days))
    converted['coverage'] = {asset: days for asset in sorted({a for daily in market['bars'].values() for a in daily})}
    converted.update(LABELS, assumptions_sha256=market['assumptions_sha256'])
    events_sha = write_once(prepared / 'actions.json', converted)
    manifest = {**LABELS, 'assumptions_sha256': market['assumptions_sha256'],
                'producer_sha256': digest(ROOT / 'scripts/research_mode_inputs.py'), 'orchestrator_sha256': digest(Path(__file__)),
                'development_end': END, 'window_start': START, 'files': {'bars.json': market_sha, 'universe.json': universe_sha, 'actions.json': events_sha},
                'source_sha256': {p.name: digest(p) for p in (dividend, *references.values(), DELIST)},
                'quote_files': len(quote_paths()),
                'assumptions': ['observed listing age is a conservative proxy (counted from 2015-01-05)', 'action coverage is local-cache uncertified'],
                'exclusion_counts': {'unresolved_events': len(converted['unresolved_events'])}}
    write_once(prepared / 'input_manifest.json', manifest)
    return manifest


def build_fundamentals():
    """v3.2 rules from FY2014 with availability re-derived on the historical calendar; v3.2 and v3.1 files are not touched."""
    from core.research.fundamental_pit_v3 import build_matrix
    from core.research.fundamentals.pit import available_date_for_publication
    from scripts.research_fundamental_pit_recovery import audit, verified_records
    records = pd.read_parquet(SAME_BASIS / 'fundamental_records.parquet')
    records['ticker'] = records['ticker'].astype(str)
    old = pd.read_parquet(SAME_BASIS / 'fundamental_matrix.parquet', columns=['asof_date', 'ticker'])
    hist_days = sorted(load(PREPARED / 'bars.json')['bars'])
    calendar = [date.fromisoformat(d) for d in hist_days] + sorted(d.date() for d in pd.to_datetime(old['asof_date']).unique() if pd.Timestamp(END) < d <= pd.Timestamp('2025-12-31'))
    pairs = records[['publication_date', 'publication_timestamp']].drop_duplicates()
    mapping = {}
    for pub, stamp in pairs.itertuples(index=False):
        day = date.fromisoformat(str(pub)[:10])
        if day >= calendar[-1]:
            continue
        moment = pd.Timestamp(stamp).to_pydatetime() if str(stamp) else None
        mapping[(pub, stamp)] = available_date_for_publication(day, moment, calendar)[0].isoformat()
    derived = [mapping.get(k) for k in zip(records['publication_date'], records['publication_timestamp'])]
    check = records.assign(derived=derived)
    check = check.loc[(check['publication_date'] >= '2023-01-03') & check['derived'].notna()]
    mismatch = int((check['derived'] != check['available_date'].astype(str).str[:10]).sum())
    if mismatch:
        raise ValueError(f'AVAILABILITY_RULE_NOT_REPRODUCED:{mismatch}')  # same rule must give the stored 2023+ dates
    records['available_date'] = derived
    records = records.loc[records['available_date'].notna() & (records['publication_date'] <= END)].copy()
    frame = audit(records, since=2014)
    yoy = verified_records(records, frame, start=2015)
    kept = yoy.loc[yoy['verification_status'].eq('VERIFIED')]
    FUND.mkdir(parents=True, exist_ok=True)
    frame.to_csv(FUND / 'publication_revision_audit.csv', index=False, encoding='utf-8-sig')
    kept.to_parquet(FUND / 'fundamental_records.parquet', index=False)
    tickers = sorted(old['ticker'].astype(str).unique())
    dates = [pd.Timestamp(d) for d in hist_days if d >= START]
    matrix, _ = build_matrix(kept, tickers, dates)
    matrix.to_parquet(FUND / 'fundamental_matrix.parquet', index=False)
    manifest = {'dataset_version': 'fundamental-pit-v3.3-hist', 'created_from': 'fundamental-pit-v3.1-yoy-same-basis (files unchanged)',
                'rules': ['v3.2 rules (YoY availability = later of current and prior-year filing; both filings VERIFIED)',
                          'available_date re-derived with fundamentals.pit.available_date_for_publication on the 2015-2022 TWSE calendar',
                          f'rule reproduces every stored 2023+ available_date ({len(check)} rows, 0 mismatches)'],
                'survivorship': 'ticker list is the v3 target of 890 companies; no 2016-2022 delisted company has any record',
                'source_records_sha256': workflow.sha256(SAME_BASIS / 'fundamental_records.parquet'),
                'records_sha256': workflow.sha256(FUND / 'fundamental_records.parquet'),
                'audit_sha256': workflow.sha256(FUND / 'publication_revision_audit.csv'),
                'matrix_sha256': workflow.sha256(FUND / 'fundamental_matrix.parquet'), 'matrix_rows': len(matrix),
                'audit_status_counts': frame['verification_status'].value_counts().to_dict(),
                'yoy_rows_by_status': yoy.groupby(['metric', 'verification_status']).size().rename('rows').reset_index().to_dict('records'),
                'external_requests': 0}
    (FUND / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    return manifest


def prepare_signals(prepared=PREPARED, signals=SIGNALS):
    from core.research.d5_research_variant import raw_factor_panels
    from scripts.prepare_h1_signals import frames_of
    from scripts.prepare_h4_signals import scores
    prepared, signals = Path(prepared), Path(signals)
    signals.mkdir(parents=True, exist_ok=True)
    manifest = load(prepared / 'input_manifest.json')
    if manifest['assumptions_sha256'] != assumptions_sha(RESEARCH_ASSUMPTIONS_V6):
        raise ValueError('UNREGISTERED_RESEARCH_ASSUMPTIONS')
    for name, expected in manifest['files'].items():
        if digest(prepared / name) != expected:
            raise ValueError(f'INPUT_CHECKPOINT_UNVERIFIED:{name}')
    result = load(FACTOR_RESULT)
    ready = [GROWTH[f] for f, d in result['decision'].items() if d == 'READY_FOR_STRATEGY_TEST']
    if result['admission'] != 'PASS' or not ready:
        raise ValueError('H4_RUN_CONDITION_NOT_MET')
    grid = pd.read_csv(GRID)
    row = grid.loc[grid['strategy_id'] == PRIMARY]
    if len(grid) != 12 or len(row) != 1 or int(row.iloc[0]['rebalance_days']) != 60:
        raise ValueError('H4_GRID_SHAPE')
    market = load(prepared / 'bars.json')
    universe = pd.DataFrame(load(prepared / 'universe.json'))
    days = sorted(universe['asof_date'].unique())
    if not days or days[0] < START or days[-1] > END:
        raise ValueError('INVALID_HISTORICAL_WINDOW')
    schedule = days[::60]
    matrix = pd.read_parquet(FUND / 'fundamental_matrix.parquet')
    matrix['asof_date'], matrix['ticker'] = pd.to_datetime(matrix['asof_date']), matrix['ticker'].astype(str)
    if matrix['asof_date'].max() > pd.Timestamp(END):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    growth = {m: matrix.pivot(index='asof_date', columns='ticker', values=m) for m in ready}
    momentum = raw_factor_panels(frames_of(market))['near_high_252d']
    outputs = {'universe_60.json': write_once(signals / 'universe_60.json', universe.loc[universe['asof_date'].isin(schedule)].to_dict('records'))}
    eligible = universe.loc[universe['member'] & universe['asof_date'].isin(schedule), ['asof_date', 'asset_id']].copy()
    eligible['asof_date'], eligible['asset_id'] = pd.to_datetime(eligible['asof_date']), eligible['asset_id'].astype(str)
    score = scores(eligible, growth, momentum, ready)
    score['asof_date'] = pd.to_datetime(score['asof_date']).dt.strftime('%Y-%m-%d')
    score['signal_at'] = score['asof_date'] + 'T15:00:00+08:00'
    score['score_available_at'] = score['asof_date'] + 'T13:30:00+08:00'
    score['score_source_sha256'] = digest(prepared / 'bars.json')
    score = score.sort_values(['asof_date', 'asset_id'], kind='stable')
    outputs['scores_00_60.json'] = write_once(signals / 'scores_00_60.json', score.to_dict('records'))
    out = {'kind': 'H4_HIST_GRID', 'parameter_fit_scope': 'NONE_FIXED_WEIGHTS_PRIMARY_ONLY', **LABELS,
           'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V6), 'producer_sha256': digest(Path(__file__)),
           'input_manifest_sha256': digest(prepared / 'input_manifest.json'), 'grid_sha256': digest(GRID), 'primary': PRIMARY,
           'growth_metrics': ready, 'weights': {'growth': .5, 'momentum': .5},
           'fundamental_sha256': {n: digest(FUND / n) for n in ('fundamental_matrix.parquet', 'fundamental_records.parquet')},
           'factor_result_sha256': digest(FACTOR_RESULT), 'composites': [COMPOSITE], 'signal_days': {'60': schedule},
           'scored_assets_per_signal_day': {d: int(n) for d, n in score.groupby('asof_date').size().items()}, 'files': outputs}
    write_once(signals / 'signals_manifest.json', out)
    return out


def admission():
    """Design D4: every condition is computed from data only; no strategy return is read."""
    bars = load(PREPARED / 'bars.json')['bars']
    days = sorted(bars)
    planned = planned_quotes()
    missing = [d for d in planned if not (RAW_HIST / f'MI_INDEX_{d}.json').is_file()]
    per_year = pd.Series([d[:4] for d in days]).value_counts().sort_index()
    universe = pd.DataFrame(load(PREPARED / 'universe.json'))
    signals = load(SIGNALS / 'signals_manifest.json')
    schedule = signals['signal_days']['60']
    scored = signals['scored_assets_per_signal_day']
    records = pd.read_parquet(SAME_BASIS / 'fundamental_records.parquet', columns=['ticker'])
    source = set(records['ticker'].astype(str))
    delisted = {str(r['Code']): roc(r['DelistingDate']) for r in load(DELIST) if len(str(r['Code'])) == 4}
    members = universe.loc[universe['member'] & universe['asof_date'].isin(schedule), ['asof_date', 'asset_id']].copy()
    members['no_source'] = ~members['asset_id'].astype(str).isin(source)
    members['later_delisted'] = [a in delisted and delisted[a] > d for a, d in zip(members['asset_id'], members['asof_date'])]
    members['survivor_gap'] = members['no_source'] & members['later_delisted']
    share = members.groupby('asof_date')['no_source'].mean()
    gap = members.groupby('asof_date')['survivor_gap'].mean()
    kept = pd.read_parquet(FUND / 'fundamental_records.parquet', columns=['metric', 'fiscal_year'])
    events = load(PREPARED / 'actions.json')
    checks = {
        'quotes_all_planned_weekdays_verified': not missing,
        'trading_days_240_252_each_year_2016_2022': bool(per_year.reindex([str(y) for y in range(2016, 2023)]).fillna(0).between(240, 252).all()),
        'event_sources_verified': all(load(p).get('stat') == 'OK' for p in [RAW_HIST / 'TWT49U_20150101_20221231.json', *event_sources().values()]),
        'min_10_scored_members_each_signal_day': len(schedule) > 0 and all(scored.get(d, 0) >= 10 for d in schedule),
        'g2_g3_verified_rows_fy2015_2022': all(((kept['metric'] == m) & (kept['fiscal_year'] == y)).any()
                                               for m in GROWTH.values() for y in range(2015, 2023)),
        'survivor_gap_mean_le_2pct_and_each_day_le_5pct': bool(gap.mean() <= .02 and gap.max() <= .05),
        'no_fundamental_source_mean_le_10pct_and_each_day_le_15pct': bool(share.mean() <= .10 and share.max() <= .15)}
    result = {**LABELS, 'design_sha256': workflow.sha256(ROOT / 'openspec/changes/2026-11-04-h4-historical-data-unlock-v1/design.md'),
              'status': 'PASS' if all(checks.values()) else 'BLOCKED', 'checks': checks, 'missing_quote_files': missing,
              'trading_days_by_year': per_year.to_dict(), 'signal_days': len(schedule), 'first_signal_day': schedule[0] if schedule else None,
              'scored_min': min(scored.values()) if scored else 0,
              'no_fundamental_source_share': {'mean': float(share.mean()), 'max': float(share.max())},
              'survivor_gap_share': {'mean': float(gap.mean()), 'max': float(gap.max())},
              'members_later_delisted_on_signal_days': int(members['later_delisted'].sum()),
              'members_later_delisted_without_source': int((members['later_delisted'] & members['no_source']).sum()),
              'unresolved_events': len(events['unresolved_events']), 'actions': len(events['actions']),
              'label_if_pass': 'SURVIVORSHIP_DISCLOSED'}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'admission.json').write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    members.groupby('asof_date').agg(members=('asset_id', 'size'), no_source=('no_source', 'sum'), later_delisted=('later_delisted', 'sum'), survivor_gap=('survivor_gap', 'sum')) \
        .to_csv(OUT / 'signal_day_members.csv', encoding='utf-8-sig')
    return result


def run_primary():
    from core.research.repair_runner import run
    from scripts.research_mode_run import COSTS
    if load(OUT / 'admission.json')['status'] != 'PASS':
        raise ValueError('ADMISSION_NOT_PASSED')
    RUNS.mkdir(parents=True, exist_ok=True)
    evidence = RUNS / 'runner_evidence.json'
    write_once(evidence, {'kind': KIND, **LABELS, 'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V6),
                          'signals_manifest_sha256': workflow.sha256(SIGNALS / 'signals_manifest.json')})
    row = pd.read_csv(GRID).set_index('strategy_id').loc[PRIMARY]
    config = {'strategy': row['factor_set'], 'config_id': PRIMARY, 'composite_id': row['composite_id'], 'stock_weighting': row['portfolio_weighting'],
              'top_n': int(row['top_n']), 'rebalance_days': int(row['rebalance_days']), **COSTS, 'data_label': 'RESEARCH_UNCERTIFIED',
              'data_end': END, 'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V6), 'output': str(RUNS / '000'),
              'inputs': {'bars': str(PREPARED / 'bars.json'), 'actions': str(PREPARED / 'actions.json'), 'scores': str(SIGNALS / 'scores_00_60.json'),
                         'universe': str(SIGNALS / 'universe_60.json'), 'evidence': str(evidence)}}
    result = run(config)
    return {'status': result['manifest']['status'], 'reason': result['manifest'].get('reason'), 'reused': result.get('reused', False)}


def taiex_series():
    """Total-return and price index closes from the same MI_INDEX files used for bars."""
    names = {'發行量加權股價報酬指數': 'taiex_total_return', '發行量加權股價指數': 'taiex_price'}
    rows = {}
    for path in quote_paths():
        values = {}
        for table in load(path).get('tables') or []:
            for item in (table.get('data') or []) if isinstance(table, dict) else []:
                if item and item[0] in names and names[item[0]] not in values:
                    values[names[item[0]]] = float(str(item[1]).replace(',', ''))
        if values:
            rows[pd.Timestamp(path.stem[-8:])] = values
    return pd.DataFrame(rows).T.sort_index()


def screen():
    """RESEARCH-SCREEN:V2 on the one historical run; records the registered verdict exactly once."""
    import math
    from core.research.attribution import run_attribution
    from core.research.research_screen import screen as screen_one
    from scripts import track_b_stats as stats
    from scripts.research_mode_report import build_result, load_runs
    from scripts.research_validation_v2 import benchmark_v2
    workflow.require_registered(HID)
    manifest = load(PREPARED / 'input_manifest.json')
    if any(workflow.sha256(PREPARED / n) != d for n, d in manifest['files'].items()):
        raise ValueError('INPUT_CHECKPOINT_UNVERIFIED')
    bars, events = load(PREPARED / 'bars.json')['bars'], load(PREPARED / 'actions.json')
    proxy, per_turnover = benchmark_v2(bars, events)
    if proxy['diagnostics']['status'] != 'PASS':
        raise ValueError('V2_BLOCKED')
    bench = proxy['returns']
    regime = stats.regimes((1 + bench).cumprod())
    records = load_runs(RUNS, 'H4_HIST')
    if len(records) != 1:
        raise ValueError('EXPECTED_ONE_REGISTERED_RUN')
    record = records[0]
    row = {'config_id': record['config'].get('config_id'), 'status': record['status'], 'reason': record.get('reason')}
    excess = None
    if record['status'] == 'COMPUTABLE':
        res = build_result(record, bench, regime)
        verdict = screen_one(res, trial_sharpe_variance=V2_VARIANCE, version='V2')
        net = pd.Series(res['net_returns_2x'], index=res['excess_series'].index)
        fit = run_attribution(PRIMARY, net, bench)
        taiex = taiex_series()['taiex_total_return'].pct_change().dropna()
        tfit = run_attribution(PRIMARY, net, taiex)
        years = {str(y): {'strategy': float((1 + g).prod() - 1), 'benchmark_v2': float((1 + bench.reindex(g.index)).prod() - 1),
                          'taiex_total_return': float((1 + taiex.reindex(g.index).fillna(0)).prod() - 1)}
                 for y, g in net.groupby(pd.to_datetime(net.index).year)}
        fills = [r['date'] for r in record['_tracks']['stress']['result']['ledger'] if r['kind'] == 'FILL']
        row.update(screen_status=verdict['status'], gates=verdict.get('gates'), values=verdict.get('values'),
                   research_trials=verdict.get('research_trials'), dsr_trials=verdict.get('dsr_trials'),
                   total_return_2x=res['stress']['total_return'], cagr_2x=(1 + res['stress']['total_return']) ** (252 / res['sessions']) - 1,
                   sharpe_2x=res['stress']['sharpe'], max_drawdown_2x=res['stress']['max_drawdown'], turnover_2x=res['stress']['turnover'],
                   total_return_1x=res['base']['total_return'], sharpe_1x=res['base']['sharpe'], turnover_1x=res['base']['turnover'],
                   benchmark_v2_total_return=res['benchmark_total_return'], sessions=res['sessions'],
                   alpha_annual=fit['alpha'] * 252, alpha_p_hac=fit['alpha_p_hac'], beta=fit['beta'],
                   taiex_alpha_annual=tfit['alpha'] * 252, taiex_alpha_p_hac=tfit['alpha_p_hac'], taiex_beta=tfit['beta'],
                   first_fill=min(fills) if fills else None, yearly=years)
        excess = res['excess_returns_2x']
        pd.DataFrame({'net_2x': net, 'benchmark_v2': bench.reindex(net.index), 'taiex_total_return': taiex.reindex(net.index)}) \
            .to_csv(OUT / 'daily_returns.csv', index_label='date')
    if row.get('gates') is None:
        decision = 'NOT_EVALUABLE'
    elif row['alpha_annual'] <= 0:
        decision = 'STOP'
    else:
        decision = 'CONTINUE' if row['gates']['r1'] else 'REVISE'
    sensitivity = {str(n): float(stats.deflated_sharpe(excess, n, V2_VARIANCE)) for n in (1, 12, 242, workflow.research_trials(), 743)} \
        if excess is not None else None
    result = {**LABELS, 'screen_version': 'RESEARCH-SCREEN:V2', 'hypothesis_id': HID, 'primary_config': PRIMARY, 'decision': decision,
              'decision_mapping': 'CONTINUE = supported (alpha > 0 and r1); REVISE = alpha > 0, r1 fails; STOP = not supported (alpha <= 0)',
              'survivorship': 'SURVIVORSHIP_DISCLOSED', 'not_independent_oos': True,
              'benchmark_v2_sha256': workflow.sha256(ROOT / 'core/research/benchmark_v2.py'), 'benchmark_v2_diagnostics': proxy['diagnostics'],
              'cost_per_one_side_turnover_2x': per_turnover, 'trial_sharpe_variance': V2_VARIANCE,
              'research_trials': workflow.research_trials(), 'formal_effective_trials': workflow.effective_trials(),
              'inputs_sha256': manifest['files'], 'primary': row, 'primary_dsr_by_trial_count': sensitivity}
    (OUT / 'h4_hist_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1, default=float, allow_nan=False) + '\n', encoding='utf-8')
    workflow.record_result(HID, decision, 'outputs/research_mode/h4_hist_unlock_v1/h4_hist_result.json',
                           f"alpha_annual={row.get('alpha_annual')} r1={(row.get('gates') or {}).get('r1')}")
    return {k: v for k, v in result.items() if k != 'primary'} | {'primary_summary': {
        k: row.get(k) for k in ('status', 'alpha_annual', 'alpha_p_hac', 'total_return_2x', 'sharpe_2x', 'max_drawdown_2x', 'gates')}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inputs', 'fundamentals', 'signals', 'admission', 'run', 'screen'))
    command = parser.parse_args().command
    action = {'inputs': prepare_inputs, 'fundamentals': build_fundamentals, 'signals': prepare_signals,
              'admission': admission, 'run': run_primary, 'screen': screen}[command]
    print(json.dumps(action(), ensure_ascii=False, indent=1, default=str)[:4000])
