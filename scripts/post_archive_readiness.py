"""Post-archive readiness (2026-10-24-assess-ai-training-readiness): read-only baseline/verify, MOPS v3.1 diagnostics, Native7 and strategy15 matrices.

Writes only into OUT. Never writes MySQL, the production status file, models or any archived output; never trains.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import digest, read, write
from scripts.official_data_recovery import git
from scripts.validate_model_data import models

OUT = ROOT / 'outputs/post_archive_readiness/2026-10-24'
DT = ROOT / 'outputs/dual_track_validation/2026-10-23'
OPERATION = ROOT / 'outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1'
STATUS_FILE = OPERATION / 'fundamental_production_status.json'
V3 = ROOT / 'data/processed/fundamental_pit_v3'
V31 = ROOT / 'data/processed/fundamental_pit_v3_yoy_same_basis'
MATRIX_V31 = V31 / 'fundamental_matrix.parquet'
FROZEN = ROOT / 'outputs/strategy_revalidation/2026-10-21-audit/frozen_gate_evidence.json'
STRICT_HASH_FILES = [OPERATION / 'FreshOOSLedger.csv', ROOT / 'jobs/scheduler.py', ROOT / 'config/settings.py', FROZEN]


def out(name):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)  # new evidence only
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def tree(folder):
    return {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p) for p in sorted(Path(folder).rglob('*')) if p.is_file()}


def status_file_state():
    stat = STATUS_FILE.stat()
    return {'sha256': digest(STATUS_FILE), 'mtime': stat.st_mtime, 'size': stat.st_size}


def snapshot():
    """Everything that must stay byte-identical, except the status file (recorded separately: an external scheduled update is allowed)."""
    ledger = DT / 'request_ledger.jsonl'
    return {'models': {k: digest(ROOT / p) for k, (_n, p) in models().items()},
            'dual_track_outputs': tree(DT), 'fundamental_pit_v3': tree(V3), 'fundamental_pit_v3_yoy_same_basis': tree(V31),
            'strict_files': {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p) for p in STRICT_HASH_FILES},
            'request_ledger_lines': len(ledger.read_text(encoding='utf-8').splitlines())}


def baseline():
    state = {'git_head': git('rev-parse', 'HEAD').strip(), 'git_status': git('status', '--short').splitlines(),
             'status_file': status_file_state(), 'BROKER_ORDER_SUBMISSION': 'DISABLED', **snapshot()}
    write(out('baseline.json'), state)
    print({'files': sum(len(v) for v in state.values() if isinstance(v, dict)), 'status_file_sha256': state['status_file']['sha256']})


def diff_snapshot(before, now):
    changed = {}
    for key, value in now.items():
        if isinstance(value, dict):
            changed[key] = sorted(k for k in set(value) | set(before[key]) if value.get(k) != before[key].get(k))
        elif value != before[key]:
            changed[key] = [f'{before[key]} -> {value}']
    return {k: v for k, v in changed.items() if v}


def verify():
    before = read(OUT / 'baseline.json')
    changed = diff_snapshot(before, snapshot())
    status_now, status_before = status_file_state(), before['status_file']
    result = {'strict_changes': changed, 'strict_pass': not changed,
              'status_file': {'before': status_before, 'now': status_now, 'changed': status_now['sha256'] != status_before['sha256'],
                              'rule': 'a change needs pipeline_runs, scheduler log, writer and asof_date proof (production_status_assessment.md); otherwise UNTRUSTED'},
              'BROKER_ORDER_SUBMISSION': 'DISABLED', 'mysql_writes': 'none (SELECT only)', 'official_requests_this_round': 0, 'training_run': False}
    write(out('verification.json'), result)
    print(json.dumps(result, ensure_ascii=False, default=str)[:600])


# ---- Phase 2: MOPS v3.1 diagnostic comparison (archived v3 results are read, never rerun) ----
V31_OUT = OUT / 'mops_v31_g2g3'
BYPRODUCTS = ['FrozenStrategySpec.json', 'strategy_shortlist.csv', 'strategy_bootstrap.json', 'strategy_validation_manifest.json',
              'strategy_validation_report.md', 'FreshOOSAvailabilityReport.json']
CUTOFF = '2026-07-28'


def isolate_byproducts():
    """The job always freezes a candidate and writes a manifest that claims dataset fundamental-pit-v3; neither is valid research output here."""
    target = V31_OUT / 'INVALID_RESEARCH_BYPRODUCTS'
    target.mkdir(exist_ok=True)
    for name in BYPRODUCTS:
        if (V31_OUT / 'run' / name).exists():
            (V31_OUT / 'run' / name).replace(target / name)
    (target / 'README.md').write_text('INVALID_RESEARCH_BYPRODUCT: produced automatically by the G2/G3 job during a version-difference diagnostic. '
                                      'Not a candidate, not frozen, not a shortlist; the manifest names dataset fundamental-pit-v3 by a code constant although the input was v3.1. '
                                      'Never read these files for any selection.\n', encoding='utf-8')
    return sorted(p.name for p in target.iterdir())


def changed_cells(old, new, column):
    import numpy as np
    a, b = old[column].to_numpy(dtype=float), new[column].to_numpy(dtype=float)
    return ~((a == b) | (np.isnan(a) & np.isnan(b)))


def mops_diff():
    import pandas as pd
    from jobs.run_fundamental_strategy_validation import DEFAULT_D3, _ticker, load_universe

    old_run, new_run = DT / 'track_b_runs/g2g3', V31_OUT / 'run'
    diff_dir = OUT / 'mops_v31_diff'
    diff_dir.mkdir(parents=True, exist_ok=True)
    byproducts = isolate_byproducts()
    old_sb, new_sb = pd.read_csv(old_run / 'strategy_scoreboard.csv'), pd.read_csv(new_run / 'strategy_scoreboard.csv')
    old_o, new_o = pd.read_parquet(DT / 'orders_g2g3.parquet'), pd.read_parquet(V31_OUT / 'orders_g2g3.parquet')
    old_r, new_r = pd.read_parquet(DT / 'returns_g2g3.parquet'), pd.read_parquet(V31_OUT / 'returns_g2g3.parquet')
    same_configs = old_sb.set_index('strategy_id').config_hash.to_dict() == new_sb.set_index('strategy_id').config_hash.to_dict()
    grid_equal = digest(old_run / 'strategy_grid.csv') == digest(new_run / 'strategy_grid.csv')
    last_date = str(max(new_r.date.max(), new_o.date.max()).date())

    # signal layer: orders per configuration
    key = ['config_id', 'date', 'asset', 'side']
    merged = old_o[key + ['shares']].merge(new_o[key + ['shares']], on=key, how='outer', suffixes=('_v3', '_v31'), indicator=True)
    merged['kind'] = merged._merge.map({'left_only': 'only_v3', 'right_only': 'only_v31', 'both': 'same'}).astype(object)
    both = merged._merge == 'both'
    merged.loc[both & (merged.shares_v3 != merged.shares_v31), 'kind'] = 'shares_changed'
    sets = old_sb.set_index('strategy_id').factor_set_id
    rows = []
    for config_id, part in merged.groupby('config_id', sort=True):
        diff = part[part.kind != 'same']
        rows.append({'config_id': config_id, 'factor_set_id': sets[config_id], 'orders_v3': int((old_o.config_id == config_id).sum()), 'orders_v31': int((new_o.config_id == config_id).sum()),
                     'only_v3': int((part.kind == 'only_v3').sum()), 'only_v31': int((part.kind == 'only_v31').sum()), 'shares_changed': int((part.kind == 'shares_changed').sum()),
                     'rebalance_dates_with_difference': int(diff.date.nunique()), 'rebalance_dates': int(part.date.nunique())})
    signal = pd.DataFrame(rows)
    signal.to_csv(diff_dir / 'signal_diff_by_config.csv', index=False)
    merged[merged.kind != 'same'].drop(columns='_merge').sort_values(key).to_csv(diff_dir / 'signal_diff_orders.csv', index=False)

    # returns: largest absolute difference per configuration (all five cost tracks)
    joined = old_r.merge(new_r, on=['config_id', 'track', 'date'], how='outer', suffixes=('_v3', '_v31'))
    joined['abs_diff'] = (joined.ret_v3 - joined.ret_v31).abs()
    returns_max = joined.groupby('config_id').abs_diff.max()

    # metric layer: differences only, ordered by id, never by value
    metric_cols = ['total_return', 'cagr', 'sharpe', 'mdd', 'turnover', 'trade_count']
    o, n = old_sb.set_index('strategy_id')[metric_cols], new_sb.set_index('strategy_id')[metric_cols]
    metrics = (n - o).add_prefix('delta_').join(sets).join(returns_max.rename('max_abs_return_diff')).sort_index()
    metrics.to_csv(diff_dir / 'metric_diff_by_config_DIAGNOSTIC.csv')

    # G3 rule: configurations that use only G3 must be bit-identical
    g3_ids = sorted(sets[sets == 'G3'].index)
    g3 = {'configurations': len(g3_ids), 'order_rows_different': int(signal[signal.config_id.isin(g3_ids)][['only_v3', 'only_v31', 'shares_changed']].sum().sum()),
          'max_abs_return_diff': float(returns_max[g3_ids].max()) if g3_ids else None, 'status': 'PASS'}
    if g3['order_rows_different'] or g3['max_abs_return_diff'] != 0.0 or not g3_ids:
        g3['status'] = 'FAIL'

    # data layer: matrix cells and removed rows inside the real G2/G3 universe, up to the cutoff and on signal dates
    old_m, new_m = (pd.read_parquet(p).sort_values(['asof_date', 'ticker']).reset_index(drop=True) for p in (V3 / 'fundamental_matrix.parquet', MATRIX_V31))
    universe = load_universe(DEFAULT_D3)
    universe = universe.loc[universe.asof_date <= CUTOFF]
    signal_asof = set(universe.loc[universe.execution_date.isin(set(new_o.date.unique())), 'asof_date'])
    layer = {'matrix_index_identical': bool(old_m[['asof_date', 'ticker']].equals(new_m[['asof_date', 'ticker']]))}
    for column in ['operating_income_yoy', 'eps_yoy']:
        cells = old_m.loc[changed_cells(old_m, new_m, column), ['asof_date', 'ticker']].copy()
        cells['asset_id'] = cells.ticker.map(_ticker)
        in_universe = cells.merge(universe[['asof_date', 'asset_id']].drop_duplicates(), on=['asof_date', 'asset_id'])
        layer[column] = {'cells_changed_total': len(cells), 'tickers_total': int(cells.ticker.nunique()), 'cells_in_g2g3_universe_to_cutoff': len(in_universe),
                         'tickers_in_universe': int(in_universe.asset_id.nunique()), 'cells_on_signal_dates': int(in_universe.asof_date.isin(signal_asof).sum())}
        if column == 'operating_income_yoy':
            in_universe.sort_values(['asset_id', 'asof_date']).to_csv(diff_dir / 'matrix_cells_in_universe_operating_income_yoy.csv', index=False)
    corrections = pd.read_csv(V31 / 'correction_detail.csv')
    corrections['asset_id'] = corrections.ticker.map(_ticker)
    layer['removed_rows'] = {'total': len(corrections), 'by_metric': corrections.metric.value_counts().to_dict(),
                             'rows_for_tickers_in_g2g3_universe': int(corrections.asset_id.isin(set(universe.asset_id)).sum())}
    layer['other_columns_unchanged'] = {c: not bool(changed_cells(old_m, new_m, c).any()) for c in ['eps', 'operating_profit', 'revenue', 'operating_margin']}

    manifest = {'purpose': 'G2/G3 version-difference diagnostic, one run of the archived 36 configurations; not research evidence, no ranking, no candidate',
                'dataset_used': {'name': 'fundamental-pit-v3.1-yoy-same-basis', 'path': MATRIX_V31.relative_to(ROOT).as_posix(), 'sha256': digest(MATRIX_V31)},
                'dataset_archived_comparison': {'name': 'fundamental-pit-v3', 'matrix_sha256': digest(V3 / 'fundamental_matrix.parquet'), 'results': 'archived dual-track outputs, not rerun'},
                'dataset_constant_in_job_manifest': 'fundamental-pit-v3 (code constant; wrong for this run, so that manifest is isolated)',
                'same_36_configuration_hashes': bool(same_configs), 'strategy_grid_identical': bool(grid_equal), 'configurations': len(new_sb),
                'last_date_in_outputs': last_date, 'cutoff': CUTOFF, 'no_row_after_cutoff': last_date <= CUTOFF, 'fresh_oos_read': False,
                'isolated_byproducts': byproducts, 'G3_unchanged': g3, 'data_layer': layer,
                'signal_layer_totals': {k: int(signal[k].sum()) for k in ['orders_v3', 'orders_v31', 'only_v3', 'only_v31', 'shares_changed']},
                'configs_with_any_signal_difference': int(((signal.only_v3 + signal.only_v31 + signal.shares_changed) > 0).sum()),
                'G2_G3_DATA_GATE': 'DATA_BLOCKED (unchanged)'}
    write(diff_dir / 'manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ['same_36_configuration_hashes', 'strategy_grid_identical', 'no_row_after_cutoff', 'G3_unchanged', 'data_layer', 'signal_layer_totals', 'configs_with_any_signal_difference']}, ensure_ascii=False, default=str)[:2200])


# ---- Phase 3: Native7 pre-training audit (no model is trained; frames come from the same provider function as recovered Daily) ----
SPEC_V1 = ROOT / 'outputs/official_data_recovery/2026-10-23/retraining_spec.json'
EVENTS = DT / 'official_events.csv'
BARS = DT / 'official_bars.parquet'
N7 = OUT / 'native7_audit'
_W = {}


def _model_inputs(spec):
    from core.research.native_models import DERIVED_FROM
    return sorted({r for f in spec['feature_order'] for r in DERIVED_FROM.get(f.removesuffix('_z'), (f.removesuffix('_z'),))})


def _worker_init(end, model_inputs):
    import contextlib
    import io
    import pandas as pd
    from core.research.native_frame import NativeResearchData, load_publication_dates
    from scripts.native7_rules_only import MYSQL, build_snapshot
    with contextlib.redirect_stdout(io.StringIO()):
        snap = build_snapshot('2022-01-03', end)
        fin, rev = (pd.read_parquet(MYSQL / f'snapshot_{n}.parquet') for n in ('financial_statements', 'monthly_revenue'))
        _W['data'], _W['mi'] = NativeResearchData(snap, fin[fin.year >= 1911], rev, publications=load_publication_dates()), model_inputs


def _worker_frame(day):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        frame, _ = _W['data'].frame(day, [], model_inputs=_W['mi'])
    return day, frame


def build_frames(days, end, model_inputs, workers=8):
    import concurrent.futures as cf
    with cf.ProcessPoolExecutor(workers, initializer=_worker_init, initargs=(end, model_inputs)) as pool:
        return dict(pool.map(_worker_frame, days, chunksize=8))


class FrameStub:
    """Same interface build_training_frame uses; frames were computed by NativeResearchData.frame(day, [], model_inputs=...)."""

    def __init__(self, snapshot, frames):
        self.snapshot, self.frames = snapshot, frames

    def frame(self, day, required, model_inputs=None):
        return self.frames[day].copy(), {}


def _forward(frame, n):
    """Next-n-session aggregation excluding today (same shape as the label code)."""
    return frame.iloc[::-1].rolling(n, min_periods=1).sum().iloc[::-1].shift(-1)


def event_matrices(sessions, stocks):
    import numpy as np
    import pandas as pd
    events = pd.read_csv(EVENTS, encoding='utf-8')
    events = events[events.stock_id.astype(str).str.fullmatch(r'\d{4}')].drop_duplicates(['ex_date', 'stock_id', 'action_type'])
    index = pd.DatetimeIndex(sessions)
    pos = index.searchsorted(pd.to_datetime(events.ex_date))  # an event takes effect on the first session on or after its ex-date
    events = events[pos < len(index)].assign(pos=pos[pos < len(index)])
    factor = pd.DataFrame(1.0, index=index, columns=stocks)
    kinds = {}
    for kind, name in (('息', 'dividend'), ('權', 'rights'), ('權息', 'rights_and_dividend')):
        part = events[events.action_type == kind]
        counts = pd.DataFrame(0.0, index=index, columns=stocks)
        for row in part.itertuples():
            if row.stock_id in counts.columns:
                counts.iat[row.pos, counts.columns.get_loc(row.stock_id)] += 1
        kinds[name] = counts
    for row in events.itertuples():
        if row.stock_id in factor.columns:
            factor.iat[row.pos, factor.columns.get_loc(row.stock_id)] *= row.event_factor
    return events, kinds, factor.cumprod()


def jump_candidates(close, event_any):
    """Raw close-to-close move beyond the 10% daily limit (plus rounding) with no TWT49U event: a possible split or capital reduction (TWT49U does not carry them)."""
    move = (close / close.shift(1) - 1).abs()
    return ((move > 0.105) & (event_any == 0)).astype(float)


def native7(cache=None):
    import numpy as np
    import pandas as pd
    from core.research.model_training import build_training_frame, split_sessions

    N7.mkdir(parents=True, exist_ok=True)
    spec = read(SPEC_V1)
    spec['model_inputs'] = _model_inputs(spec)  # in memory only: the draft JSON lacks the key train() reads
    bars = pd.read_parquet(BARS)
    sessions = [d.strftime('%Y-%m-%d') for d in sorted(bars.trade_date.unique())]
    usable = sessions[30:]
    assert usable[0] == '2022-02-22', usable[0]  # the specification's own first usable session
    data_end = usable[-1]
    cache_dir = Path(cache) / 'frames.parquet' if cache else N7 / '_frames.parquet'  # large; not evidence
    if cache_dir.exists():
        stacked = pd.read_parquet(cache_dir)
        frames = {d: g.drop(columns='day').reset_index(drop=True) for d, g in stacked.groupby('day')}
    else:
        frames = build_frames(usable, data_end, spec['model_inputs'])
        pd.concat([f.assign(day=d) for d, f in frames.items()], ignore_index=True).to_parquet(cache_dir)
    snap = bars.rename(columns={'high': 'high_price', 'close': 'close_price'})[['trade_date', 'stock_id', 'high_price', 'close_price']]
    stub = FrameStub(snap, frames)
    high = snap.pivot(index='trade_date', columns='stock_id', values='high_price').sort_index()
    close = snap.pivot(index='trade_date', columns='stock_id', values='close_price').sort_index()
    events, kinds, cumulative = event_matrices(high.index, list(high.columns))
    any_event = sum(kinds.values())
    jumps = jump_candidates(close, any_event)
    adj_high, adj_close = high / cumulative, close / cumulative
    results = {}
    for key, meta in spec['strategies'].items():
        n, target = int(meta['label']['look_ahead_sessions']), float(meta['label']['target_return'])
        parts = split_sessions(usable, purge=n, embargo=spec['split']['embargo_sessions'], ratios=(spec['split']['train'], spec['split']['validation']))
        valid_next = _forward(high.notna().astype(float), n)
        future_adj = adj_high.iloc[::-1].rolling(n, min_periods=1).max().iloc[::-1].shift(-1)
        windows = {name: _forward(m, n) for name, m in kinds.items()}
        past_event_30 = any_event.rolling(30, min_periods=1).sum()
        position = {d: i for i, d in enumerate(high.index)}
        info = {'horizon': n, 'target_return': target, 'split': {k: [v[0], v[-1], len(v)] for k, v in parts.items()}, 'parts': {}}
        positions = {k: [position[pd.Timestamp(s)] for s in v] for k, v in parts.items()}
        info['split_checks'] = {'train_horizon_ends_before_validation': positions['train'][-1] + n < positions['validation'][0],
                                'validation_horizon_ends_before_test': positions['validation'][-1] + n < positions['test'][0],
                                'sessions_between_train_and_validation': positions['validation'][0] - positions['train'][-1] - 1,
                                'sessions_between_validation_and_test': positions['test'][0] - positions['validation'][-1] - 1,
                                'sets_disjoint': not (set(parts['train']) & set(parts['validation']) | set(parts['train']) & set(parts['test']) | set(parts['validation']) & set(parts['test']))}
        for part, days in parts.items():
            frame = build_training_frame(stub, days, spec, key, data_end)
            frame['stock_id'] = frame.stock_id.astype(str)
            ri, ci = high.index.get_indexer(frame.trade_date), high.columns.get_indexer(frame.stock_id)
            assert (ri >= 0).all() and (ci >= 0).all()
            at = lambda m: m.to_numpy()[ri, ci]
            ev = {name: at(w) > 0 for name, w in windows.items()}
            affected = ev['dividend'] | ev['rights'] | ev['rights_and_dividend']
            jump = at(_forward(jumps, n)) > 0
            adj_gain = at(future_adj) / at(adj_close) - 1
            label_adj = (adj_gain > target).astype(int)
            flips = affected & (label_adj != frame.label.to_numpy())
            frame['affected_official_event'], frame['jump_candidate'] = affected, jump
            year = frame.trade_date.dt.year
            by_year = pd.DataFrame({'year': year, 'rows': 1, 'affected': affected.astype(int), 'jump_candidate_only': (jump & ~affected).astype(int)}).groupby('year').sum()
            by_year['remaining_after_excluding_official_events'] = by_year.rows - by_year.affected
            info['parts'][part] = {
                'sessions': len(days), 'rows': len(frame), 'positives': int(frame.label.sum()),
                'rows_horizon_crosses_dividend': int(ev['dividend'].sum()), 'rows_horizon_crosses_rights': int(ev['rights'].sum()),
                'rows_horizon_crosses_rights_and_dividend': int(ev['rights_and_dividend'].sum()), 'rows_affected_any_official_event': int(affected.sum()),
                'stocks_affected': int(frame.stock_id[affected].nunique()), 'rows_remaining_after_excluding_official_events': int((~affected).sum()),
                'rows_horizon_crosses_possible_split_or_capital_reduction': int(jump.sum()), 'rows_with_jump_candidate_not_in_affected': int((jump & ~affected).sum()),
                'label_flips_official_factors': int(flips.sum()), 'flips_0_to_1': int((flips & (label_adj == 1)).sum()), 'flips_1_to_0': int((flips & (label_adj == 0)).sum()),
                'label_flips_split_or_capital_reduction': 'UNKNOWN (no official adjusted price for these events)',
                'rows_with_partial_horizon_bars': int((at(valid_next) < n).sum()),
                'rows_with_event_in_previous_30_sessions_feature_window': int((at(past_event_30) > 0).sum()),
                'by_year': {int(y): {c: int(v) for c, v in r.items()} for y, r in by_year.iterrows()}}
            if part == 'train':
                frame.loc[affected | jump, ['stock_id', 'trade_date', 'affected_official_event', 'jump_candidate']].sort_values(['stock_id', 'trade_date']).to_csv(
                    N7 / f'affected_rows_train_{key}.csv.gz', index=False, compression='gzip')
        results[key] = info
    write(N7 / 'model_stats.json', {'data_end': data_end, 'first_usable_session': usable[0], 'sessions': len(usable), 'official_events_used': len(events),
                                    'corporate_action_gate': 'OFFICIAL_CORPORATE_ACTIONS DATA_PASS (adjustment factors only; splits and capital reductions are not in TWT49U)', 'models': results})
    print({k: {p: (v['rows'], v['rows_affected_any_official_event'], v['label_flips_official_factors']) for p, v in r['parts'].items()} for k, r in results.items()})



def label_policy(cache=None):
    """OD-T1 (2026-10-24): per model and segment, usable rows under OFFICIAL_FACTORS_VERIFIED_ONLY, rows adjusted by proven events and rows
    excluded (unproven TWT49U event or an unexplained move beyond the daily limit: possible split / capital reduction). Same frames as native7."""
    import pandas as pd
    from core.research.model_training import VERIFIED_ONLY, build_training_frame, corporate_action_adjustment, split_sessions
    spec = read(SPEC_V1)
    spec['model_inputs'] = _model_inputs(spec)
    bars = pd.read_parquet(BARS)
    usable = [d.strftime('%Y-%m-%d') for d in sorted(bars.trade_date.unique())][30:]
    data_end = usable[-1]
    stacked = pd.read_parquet(Path(cache) / 'frames.parquet')
    frames = {d: g.drop(columns='day').reset_index(drop=True) for d, g in stacked.groupby('day')}
    snap = bars.rename(columns={'high': 'high_price', 'close': 'close_price'})[['trade_date', 'stock_id', 'high_price', 'close_price']]
    stub = FrameStub(snap, frames)
    events = pd.read_csv(EVENTS, dtype={'stock_id': str})
    events = events[events.stock_id.str.fullmatch(r'\d{4}')].drop_duplicates(['ex_date', 'stock_id', 'action_type'])
    close = snap.pivot(index='trade_date', columns='stock_id', values='close_price').sort_index()
    cumulative, unexplained = corporate_action_adjustment(events, close)
    proven = (cumulative / cumulative.shift(1).fillna(1.0) < 1)
    has_event = pd.DataFrame(False, index=close.index, columns=close.columns)
    for row in events.itertuples():
        i = close.index.searchsorted(pd.Timestamp(row.ex_date))
        if row.stock_id in close.columns and i < len(close.index):
            has_event.iat[i, close.columns.get_loc(row.stock_id)] = True
    unproven_event, jump = unexplained & has_event, unexplained & ~has_event
    cells = lambda m: [{'session': str(d.date()), 'stock_id': c} for d, c in m[m].stack().index]
    listing = pd.DataFrame([{**c, 'kind': k} for k, m in (('UNPROVEN_TWT49U_EVENT', unproven_event), ('UNEXPLAINED_MOVE_BEYOND_DAILY_LIMIT', jump)) for c in cells(m)])
    listing.to_csv(out('label_policy_excluded_events.csv'), index=False, encoding='utf-8-sig')
    results = {}
    for key, meta in spec['strategies'].items():
        n = int(meta['label']['look_ahead_sessions'])
        parts = split_sessions(usable, purge=n, embargo=spec['split']['embargo_sessions'], ratios=(spec['split']['train'], spec['split']['validation']))
        window = lambda m: m.astype(float).iloc[::-1].rolling(n, min_periods=1).max().iloc[::-1].shift(-1).fillna(0) > 0
        crosses = {'proven': window(proven), 'unproven_event': window(unproven_event), 'jump': window(jump)}
        results[key] = {'horizon': n, 'parts': {}}
        for part, days in parts.items():
            raw = build_training_frame(stub, days, {**spec, 'corporate_actions': {'label_policy': 'RAW_PRICES'}}, key, data_end)
            kept = build_training_frame(stub, days, {**spec, 'corporate_actions': {'label_policy': VERIFIED_ONLY}}, key, data_end, events)
            ri, ci = close.index.get_indexer(raw.trade_date), close.columns.get_indexer(raw.stock_id.astype(str))
            flag = {k: m.to_numpy()[ri, ci] for k, m in crosses.items()}
            merged = raw[['stock_id', 'trade_date', 'label']].merge(kept[['stock_id', 'trade_date', 'label']], on=['stock_id', 'trade_date'], suffixes=('_raw', ''))
            year = raw.trade_date.dt.year
            results[key]['parts'][part] = {
                'rows_full_horizon': len(raw), 'rows_usable': len(kept), 'rows_excluded': len(raw) - len(kept),
                'rows_excluded_unproven_twt49u_event': int(flag['unproven_event'].sum()), 'rows_excluded_unexplained_move': int(flag['jump'].sum()),
                'rows_adjusted_by_proven_event': int((flag['proven'] & ~flag['unproven_event'] & ~flag['jump']).sum()),
                'usable_share': round(len(kept) / len(raw), 6) if len(raw) else None, 'positives_raw': int(raw.label.sum()), 'positives_usable': int(kept.label.sum()),
                'label_flips_vs_raw': int((merged.label != merged.label_raw).sum()), 'flips_0_to_1': int(((merged.label == 1) & (merged.label_raw == 0)).sum()),
                'flips_1_to_0': int(((merged.label == 0) & (merged.label_raw == 1)).sum()), 'stocks_with_excluded_rows': int(raw.stock_id[flag['unproven_event'] | flag['jump']].nunique()),
                'by_year_usable': {int(y): int(v) for y, v in kept.trade_date.dt.year.value_counts().sort_index().items()},
                'by_year_full_horizon': {int(y): int(v) for y, v in year.value_counts().sort_index().items()}}
    write(out('native7_label_policy_stats.json'), {'policy': VERIFIED_ONLY, 'owner_decision': 'OD-T1 / OD-T1b 2026-10-24', 'data_end': data_end,
                                                   'events_in_universe': int(has_event.to_numpy().sum()), 'events_proven': int(proven.to_numpy().sum()),
                                                   'events_unproven': int(unproven_event.to_numpy().sum()), 'unexplained_moves': int(jump.to_numpy().sum()),
                                                   'split_capital_reduction_completeness': 'UNKNOWN: no official split / capital-reduction source is cached; only moves beyond the daily limit are detectable',
                                                   'models': results})
    print({k: {p: (v['rows_full_horizon'], v['rows_usable'], v['rows_excluded_unproven_twt49u_event'], v['rows_excluded_unexplained_move'], v['label_flips_vs_raw']) for p, v in r['parts'].items()} for k, r in results.items()})

# ---- Phase 4: non-AI next actions. Ranking inputs are data requirements and execution-audit flags only; no performance column is read for ranking. ----
RANK_INPUT_COLUMNS = ['uncertified_dataset_count', 'needs_new_official_source', 'material_execution_audit_share', 'nonofficial_price_share', 'strategy']
FORBIDDEN_LABELS = ('OOS_PASS', 'PROFITABLE_PROVEN', 'PRODUCTION_READY')
S15_CONTEXT = {  # structural diagnostics quoted in hypotheses come from backtest_results.csv medians (annual_turnover, cost drag, top5 profit share); they are not ranking inputs
    'cost': ('HYP-COST', 'Net shortfall comes from turnover x cost, not from the ranking signal: with the signal unchanged, trading only when a name leaves the top 2N '
             '(one buffer, registered before any run) cuts annual turnover by at least one third.',
             'On the development window only (Day3 ends 2025-12-31, G2/G3 and D5 end 2026-07-28), one run, trial count 1; hypothesis is rejected if turnover falls by less than one third '
             'or the top-N overlap with the unbuffered portfolio drops below 70%.'),
    'concentration': ('HYP-CONC', 'Profit concentrates in a handful of names (median top-5 profit share above 50% for most configurations): a per-name weight cap of 10% '
                      'brings the top-5 profit share below 50% without changing the ranking.',
                      'Development window only, one run, trial count 1; rejected if the top-5 profit share stays at or above 50% in more than half of that strategy\'s configurations.'),
    'size': ('HYP-SIZE', 'A191_132 is the 20-session mean of official traded value, i.e. a liquidity/size tilt: its result is explained by a plain top-N-by-traded-value basket.',
             'Build the plain basket from the same official traded value on the same rebalance dates; rejected if the factor portfolio beats the basket with a positive bootstrap lower bound of the daily return difference.'),
    'pit': ('HYP-PIT', 'Quarterly fundamentals change once per quarter, so a 20-session rebalance trades on rank noise; trading only when a YoY rank crosses a band lowers turnover.',
            'Cannot be tested until publication-time and first-published-value evidence exist (G2/G3 are DATA_BLOCKED); no run before that.'),
    'window': ('HYP-WINDOW', 'The 252-session factors (momentum_12_1, near_high_252d) leave the first year without signals, so the portfolio is partly in cash early on; '
               'starting the evaluation after the longest factor window removes this effect.',
               'Compare the evaluation start at the first session where all five factors exist with the current start, development window only; rejected if the sign of the result is unchanged.')}


G2G3_NAMES = {'G2_PLUS_G3': 'G2_OPERATING_INCOME_YOY+G3_EPS_YOY', 'G2': 'G2_OPERATING_INCOME_YOY', 'G3': 'G3_EPS_YOY'}


def s15_strategy(row):
    if row.family == 'D5':
        return 'D5_PORTFOLIO_5_FACTORS'
    head = row.config_id.split('__')[0]
    return G2G3_NAMES[head] if row.family == 'G2G3' else head


def strategy15():
    import pandas as pd
    matrix = pd.read_csv(DT / 'strategy_status_matrix.csv', encoding='utf-8-sig')
    matrix = matrix[matrix.group != 'A_NATIVE_AI'].set_index('strategy')
    runs = pd.read_csv(DT / 'backtest_results.csv')
    runs['strategy'] = runs.apply(s15_strategy, axis=1)
    rows = []
    for strategy, info in matrix.iterrows():
        part = runs[runs.strategy == strategy]
        n = len(part)
        fundamentals = info.group == 'B1_G2_G3'
        material = float((part.execution_audit == 'FLAGGED_MATERIAL').mean())
        nonofficial = float((part.held_on_nonofficial_prices > 0).mean())
        requirements = ['TOTAL_RETURN: cash dividend vs share split of rights-and-dividend events (MOPS dividend details, new official source)']
        if fundamentals:
            requirements.insert(0, 'FUNDAMENTAL PIT: first-published values or a second source for the whole population (only a 2026-09-15 snapshot exists); G2/G3 gate DATA_BLOCKED')
        if nonofficial:
            requirements.append(f'VALUATION PRICES: {int((part.held_on_nonofficial_prices > 0).sum())} of {n} configurations hold names on non-official prices (untradable days); official prices or an exclusion rule')
        if material:
            requirements.append(f'EXECUTION: {int((part.execution_audit == "FLAGGED_MATERIAL").sum())} of {n} configurations flagged material (flag-only audit; a fill simulation would be needed)')
        median = lambda c: float(part[c].median())
        concentrated = median('top5_assets_profit_share') > 0.5  # structural diagnostic for the hypothesis text, not a ranking input
        if info.group == 'B1_G2_G3':
            keys = ['pit', 'cost']
        elif info.group == 'B2_D5':
            keys = ['window', 'cost']
        else:
            keys = (['size', 'cost'] if 'A191_132' in strategy and '+' not in strategy else ['cost']) + (['concentration'] if concentrated else [])
        hypotheses = [S15_CONTEXT[k] for k in keys]
        rows.append({'group': info.group, 'strategy': strategy, 'DATA': info.DATA, 'BACKTEST': info.BACKTEST, 'EVIDENCE': info.EVIDENCE, 'configurations': n,
                     'blockers': info.blockers, 'data_requirements': ' | '.join(requirements), 'uncertified_dataset_count': int(fundamentals),
                     'needs_new_official_source': 'YES (total return needs MOPS dividend details' + ('; PIT needs a versioned source' if fundamentals else '') + ')',
                     'material_execution_audit_share': round(material, 4), 'nonofficial_price_share': round(nonofficial, 4),
                     'improvement_hypothesis': ' || '.join(f'{h[0]}: {h[1]}' for h in hypotheses), 'falsification_test': ' || '.join(f'{h[0]}: {h[2]}' for h in hypotheses),
                     'next_action': ('DATA: obtain versioned fundamental values (Owner decision OD-P4)' if fundamentals else
                                     'DATA: decide the rule for names on non-official valuation prices and the dividend-detail source (OD-P5, OD-P6); no new performance conclusion until then'),
                     'owner_decision_id': 'OD-P4' if fundamentals else 'OD-P5/OD-P6'})
    frame = pd.DataFrame(rows)
    frame = frame.sort_values([c for c in RANK_INPUT_COLUMNS if c != 'needs_new_official_source'], kind='stable')  # needs_new_official_source is YES for every strategy (total return)
    frame.insert(0, 'closest_to_trusted_development_rank', range(1, len(frame) + 1))
    assert len(frame) == 15 and not any(label in frame.to_csv() for label in FORBIDDEN_LABELS)
    frame.to_csv(out('strategy15_next_action_matrix.csv'), index=False, encoding='utf-8-sig')
    unblock = pd.DataFrame([
        {'problem': 'Names held on non-official valuation prices (67 untradable days, d3_nonofficial_prices.csv)', 'strategies_affected': int((frame.nonofficial_price_share > 0).sum()),
         'new_official_requests': 'none if an exclusion rule is chosen', 'unblocks': 'removes nonofficial_valuation_price_held from the configurations that carry it'},
        {'problem': 'Cash dividend vs share split of rights-and-dividend events (221 + events)', 'strategies_affected': 15,
         'new_official_requests': 'yes: MOPS dividend details (count not estimated this round)', 'unblocks': 'TOTAL_RETURN_BLOCKED for every configuration'},
        {'problem': 'Fundamental PIT values (first-published or second source)', 'strategies_affected': 3, 'new_official_requests': 'unknown: needs a versioned source',
         'unblocks': 'G2/G3 DATA_BLOCKED (never by itself: statistical gates and independent sample remain)'}])
    unblock.to_csv(OUT / 'strategy15_data_unblock_priorities.csv', index=False, encoding='utf-8-sig')
    print(frame[['closest_to_trusted_development_rank', 'strategy', 'uncertified_dataset_count', 'material_execution_audit_share', 'nonofficial_price_share']].to_string(index=False))


# ---- Phase 5: request gate evidence (read-only; no official request is made) ----
MIN_GAP = 3.2  # seconds; the historical threshold, never changed here


def verify_gate_gaps(events, minimum=MIN_GAP):
    """Next real download: every RELEASED must carry gate_gap_seconds >= minimum. The first release of a process has no own gap, so its
    gate_release_wall difference to the previous release is used. Anything unrecorded makes the batch fail (never assumed compliant)."""
    released = [e for e in events if e.get('event') == 'RELEASED']
    if not released:
        return {'status': 'NO_DOWNLOADS', 'released': 0}
    unrecorded, below, gaps = [], [], []
    previous_wall = None
    for i, e in enumerate(released):
        gap = e.get('gate_gap_seconds')
        wall = e.get('gate_release_wall')
        if gap is None and wall is not None and previous_wall is not None:
            gap = wall - previous_wall
        previous_wall = wall if wall is not None else previous_wall
        if gap is None:
            if i or wall is None:
                unrecorded.append(i)
            continue
        gaps.append(gap)
        if gap < minimum:
            below.append({'index': i, 'gap_seconds': gap})
    return {'status': 'PASS' if not unrecorded and not below else 'FAIL', 'released': len(released), 'minimum_seconds': minimum,
            'pairs_verified': len(gaps), 'smallest_gap_seconds': min(gaps) if gaps else None, 'below_minimum': below, 'unrecorded_releases': len(unrecorded)}


def timestamp_gaps(events):
    """Historical ledgers carry only `ts`: the spacing a reader can reconstruct. Reported for reference, never used to clear a batch."""
    from datetime import datetime
    stamps = [datetime.fromisoformat(e['ts']) for e in events if e.get('event') == 'RELEASED']
    gaps = [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])]
    return {'released': len(stamps), 'smallest_logged_gap_seconds': min(gaps) if gaps else None}


def gate_inventory():
    import re
    rows = []
    for folder in ('scripts', 'core', 'jobs'):
        for path in sorted((ROOT / folder).rglob('*.py')):
            if path == Path(__file__).resolve():
                continue  # this file only mentions the patterns it scans for
            text = path.read_text(encoding='utf-8', errors='replace')
            if re.search(r'requests\.(get|post|Session)|urlopen', text):
                rows.append({'file': path.relative_to(ROOT).as_posix(), 'uses_RequestGate': 'RequestGate' in text, 'shared_state_path': 'state_path' in text,
                             'records_gate_gap_seconds': 'gate_gap_seconds' in text})
    return rows


def gate_check():
    ledger = DT / 'request_ledger.jsonl'
    events = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
    inventory = gate_inventory()
    result = {'historical_ledger': {'path': ledger.relative_to(ROOT).as_posix(), 'sha256': digest(ledger), 'lines': len(events),
                                    'verifier_result': verify_gate_gaps(events), 'reconstructed_from_timestamps': timestamp_gaps(events),
                                    'retained_exception': 'HISTORICAL_RATE_GATE_EVIDENCE_FAIL (2.934 s pair, threshold 3.2 s) stays unresolved and accepted; REQUEST_COMPLIANCE_PASS is never claimed for it'},
              'next_download_rule': f'every RELEASED must carry gate_gap_seconds >= {MIN_GAP} (verify_gate_gaps); unrecorded or smaller means the batch fails',
              'entry_points_with_http': inventory,
              'files_recording_gate_gap_seconds': sorted(p.relative_to(ROOT).as_posix() for folder in ('scripts', 'core', 'jobs') for p in (ROOT / folder).rglob('*.py')
                                                         if p != Path(__file__).resolve() and 'gate_gap_seconds' in p.read_text(encoding='utf-8', errors='replace')),
              'entry_points_without_RequestGate': [r['file'] for r in inventory if not r['uses_RequestGate']],
              'official_requests_this_round': 0}
    write(out('request_gate_check.json'), result)
    print(json.dumps({k: result[k] for k in ('files_recording_gate_gap_seconds', 'entry_points_without_RequestGate')}, ensure_ascii=False))
    print(result['historical_ledger']['verifier_result'], result['historical_ledger']['reconstructed_from_timestamps'])


def data_manifest():
    """Proposal only (spec section 12): every raw official file the training frames derive from, with an overall SHA-256. Registration needs the Owner to fix the data end."""
    import hashlib
    import re
    from scripts.official_data_recovery import CHIP_CACHE, INDEX_CACHE
    from core.research.native_frame import MI_INDEX_DIRS
    end = '20261022'
    files = {}
    for folder in (CHIP_CACHE, *MI_INDEX_DIRS):
        for path in sorted(Path(folder).glob('*.json')):
            m = re.fullmatch(r'(T86|MI_MARGN|MI_INDEX)_(\d{8})\.json', path.name)
            if m and '20211230' <= m.group(2) <= end:
                files[f'{m.group(1)}/{path.name}'] = digest(path)
    derived = {p.name: digest(p) for p in (BARS, EVENTS)}
    lines = chr(10).join(f'{k}:{v}' for k, v in sorted({**files, **{f'derived/{k}': v for k, v in derived.items()}}.items()))
    by_dataset = {d: sum(k.startswith(d + '/') for k in files) for d in ('T86', 'MI_MARGN', 'MI_INDEX')}
    write(out('native7_data_manifest_proposal.json'), {'status': 'PROPOSED_NOT_REGISTERED', 'proposed_data_end': '2026-10-22', 'files': len(files), 'by_dataset': by_dataset,
                                                      'derived_inputs': derived, 'overall_sha256': hashlib.sha256(lines.encode('utf-8')).hexdigest(),
                                                      'note': 'registered only when the Owner approves the specification and fixes the data end (OD-21); a later data end gives a different hash',
                                                      'file_sha256': {**files}})
    print(by_dataset, len(files))



def manifest_recheck():
    """OD-T2: rehash every file of the manifest proposal; the overall SHA-256 may be registered only when nothing changed or went missing."""
    import hashlib
    proposal = read(OUT / 'native7_data_manifest_proposal.json')
    roots = {'T86': None, 'MI_MARGN': None, 'MI_INDEX': None}
    from scripts.official_data_recovery import CHIP_CACHE
    from core.research.native_frame import MI_INDEX_DIRS
    found = {}
    for folder in (CHIP_CACHE, *MI_INDEX_DIRS):
        for path in Path(folder).glob('*.json'):
            found.setdefault(f"{path.name.rsplit('_', 1)[0]}/{path.name}", path)
    changed = sorted(k for k, v in proposal['file_sha256'].items() if k not in found or digest(found[k]) != v)
    derived = {p.name: digest(p) for p in (BARS, EVENTS)}
    lines = chr(10).join(f'{k}:{v}' for k, v in sorted({**proposal['file_sha256'], **{f'derived/{k}': v for k, v in derived.items()}}.items()))
    same = not changed and derived == proposal['derived_inputs'] and hashlib.sha256(lines.encode('utf-8')).hexdigest() == proposal['overall_sha256']
    write(out('native7_data_manifest_recheck.json'), {'overall_sha256': proposal['overall_sha256'], 'files': len(proposal['file_sha256']), 'changed_or_missing': changed,
                                                     'derived_inputs_unchanged': derived == proposal['derived_inputs'], 'status': 'UNCHANGED' if same else 'CHANGED', 'datasets': list(roots)})
    print('UNCHANGED' if same else f'CHANGED {changed[:5]}')


def spec_v2_r4():
    """retraining-spec-v2 r4: r3 plus the Owner decisions OD-T1..T5 of 2026-10-24. Still a draft: the Owner's signature is the only approval."""
    from core.research.model_training import VERIFIED_ONLY, spec_hash
    r3 = read(OUT / 'retraining_spec_v2_draft_r3.json')
    recheck = read(OUT / 'native7_data_manifest_recheck.json')
    stats = read(OUT / 'native7_label_policy_stats.json')
    assert recheck['status'] == 'UNCHANGED' and r3['status'] == 'DRAFT_PENDING_OWNER_APPROVAL'
    r4 = {k: v for k, v in r3.items() if k != 'sha256'}
    r4['revision'] = 'r4 (OD-T1..T5 decided 2026-10-24; pending the Owner signature)'
    r4['data_manifest_sha256'] = recheck['overall_sha256']  # guard 6 compares the hash passed to train() with this value
    r4['corporate_actions'] = {**r3['corporate_actions'], 'label_policy': VERIFIED_ONLY,
                               'decision': 'OD-T1: official factors only for events official data proves (pre-ex close = official previous close, 0 < factor <= 1); every other event and every move beyond the daily limit without an event is excluded from the label, never estimated',
                               'splits_and_capital_reductions': 'OD-T1b: UNKNOWN is not PASS. No official split / capital-reduction source is cached; detectable moves are excluded; moves within the daily limit are undetectable (DATA_BLOCKED until an official source is approved)',
                               'features': 'not adjusted (option B); feature windows that cross an unexplained move are a listed bias risk',
                               'evidence': 'outputs/post_archive_readiness/2026-10-24/native7_label_policy_stats.json', 'events_proven': stats['events_proven'], 'events_unproven': stats['events_unproven']}
    r4['oos'] = {**r3['oos'], 'start_definition': 'OD-T3: first session after the new model freeze that no research has read; at least 252 sessions; sessions read before are never reused',
                 'read_ledger': 'NOT_IMPLEMENTED (CODE): needed before any Fresh OOS session is counted'}
    r4['acceptance'] = {**r3['acceptance'], 'status': 'OD-T4: the existing model thresholds are kept, never lowered; frozen by the SHA-256 of the signed version',
                        'guard_2_enforced': 'train() raises FutureLeakageError before fitting (first, middle and last session of each segment)',
                        'strategy_candidate_criteria': 'OD-24 g1..g5 unchanged (docs/dual_track_validation_report.md section 5), applied to the strategy backtest after model acceptance, never to tune the model'}
    r4['fundamental_rule_strategies'] = 'OD-T5: growth_momentum_breakout, quality_growth, quality_value_low_volatility stay DATA_BLOCKED as strategies; only the price/volume model readiness is assessed; no claim that the full strategy can go live'
    r4['sha256'] = spec_hash(r4)
    write(out('retraining_spec_v2_draft_r4.json'), r4)
    print(r4['sha256'])

TRACK_A_STATUS = [  # status comes from executable tests only; a description in a JSON file is not evidence
    {'change': 1, 'what': 'dealer_consec_days and institutional_consec_buy_days over the stock history', 'status': 'VERIFIED_BY_TESTS',
     'tests': ['test/test_track_a_fixes.py::test_dealer_consec_days_counts_consecutive_sessions_and_stays_null_without_a_flow', 'test/test_track_a_fixes.py::test_institutional_consec_buy_days_uses_the_history_streak']},
    {'change': 2, 'what': 'z-score population = every complete-case row, rules applied afterwards', 'status': 'VERIFIED_BY_TESTS',
     'tests': ['test/test_track_a_fixes.py::test_rule_inputs_never_change_the_zscore_population', 'test/test_track_a_fixes.py::test_a_row_missing_a_model_input_is_outside_the_population',
               'test/test_track_a_fixes.py::test_legacy_frame_behavior_is_unchanged_without_model_inputs']},
    {'change': 3, 'what': 'TWSE-listed four-digit codes only (recovered Daily and training)', 'status': 'VERIFIED_BY_TESTS',
     'tests': ['test/test_post_archive_readiness.py::test_listed_universe_never_guesses_when_no_official_response_is_cached', 'test/test_post_archive_readiness.py::test_listed_universe_equals_the_four_digit_official_bars_of_the_same_session',
               'test/test_daily_selection_recovery.py::test_recovered_daily_keeps_only_the_official_listed_universe'],
     'gap': 'NEED_OWNER_APPROVAL: the production (legacy) run_daily path does not use listed_universe (2,305 codes scored on 2026-10-22, 1,224 not TWSE-listed common stocks); changing it changes production selection.'},
    {'change': 4, 'what': 'model contract reads training_data_end and spec_sha256', 'status': 'VERIFIED_BY_TESTS',
     'tests': ['test/test_track_a_fixes.py::test_training_data_end_bounds_the_window_and_the_spec_hash_is_checked', 'test/test_track_a_fixes.py::test_a_model_with_a_spec_hash_requires_every_feature_not_only_those_with_importance',
               'test/test_track_a_fixes.py::test_an_old_model_without_the_new_fields_still_loads_as_before']},
    {'change': 5, 'what': 'recovered flag CHIP_SCORE_TRAINING_DEFINITION_UNPROVEN dropped for models carrying a spec hash', 'status': 'VERIFIED_BY_TESTS',
     'tests': ['test/test_track_a_fixes.py::test_recovered_status_only_blocks_on_missing_cutoff_or_unproven_chip_definition']},
    {'change': 6, 'what': 'approval-gated training builder with the spec 9.1 acceptance evaluation', 'status': 'VERIFIED_BY_TESTS',
     'tests': ['test/test_track_a_fixes.py::test_training_refuses_without_a_matching_approval_before_reading_any_data', 'test/test_track_a_fixes.py::test_training_frame_equals_the_recovered_frame_for_the_same_session',
               'test/test_track_a_fixes.py::test_labels_use_only_the_next_sessions_and_rows_without_a_full_horizon_are_dropped', 'test/test_track_a_fixes.py::test_a_stock_without_a_bar_inside_the_horizon_has_no_full_horizon_and_is_dropped',
               'test/test_track_a_fixes.py::test_train_fit_and_dump_record_the_contract_fields_on_synthetic_data', 'test/test_track_a_fixes.py::test_acceptance_passes_an_informative_score_and_fails_noise',
               'test/test_track_a_fixes.py::test_acceptance_reports_undefined_metrics_as_none_with_a_reason_never_zero', 'test/test_track_a_fixes.py::test_train_evaluates_validation_and_test_once_and_records_the_guards', 'test/test_track_a_fixes.py::test_a_model_that_is_not_accepted_is_never_written_under_the_loadable_name_and_test_is_read_once_per_spec'],
     'gap': 'guard 2 (future independence) is outside train() (native7_truncation_check.json); guard 6 is None until the manifest hash is registered in the spec; a non-PASS model is written as *_rejected.pkl; the test segment is read once per (spec_sha256, strategy) via outputs/model_training/test_evaluation_ledger.jsonl; thresholds are still proposals (OD-T4).'}]

def spec_v2():
    from core.research.model_training import spec_hash
    v1 = read(SPEC_V1)
    v2 = {k: v for k, v in v1.items() if k not in ('sha256', 'code_changes_required_before_training')}
    v2.update({'version': 'retraining-spec-v2', 'status': 'DRAFT_PENDING_OWNER_APPROVAL', 'created': '2026-10-24', 'predecessor': {'version': v1['version'], 'sha256': v1['sha256']},
               'model_inputs': _model_inputs(v1),
               'corporate_actions': {'status': 'OWNER_DECISION_PENDING', 'universe_price_basis': 'raw prices (unchanged from v1 until the Owner decides)',
                                     'official_adjustment': 'TWT49U event_factor = reference price / previous close (gate OFFICIAL_CORPORATE_ACTIONS DATA_PASS, adjustment factors only)',
                                     'options': ['A: drop every row whose label horizon contains an official event', 'B: label on prices adjusted with the official factors (features stay raw)',
                                                 'C: adjust prices and features with the official factors', 'D: keep raw prices and report the affected share'],
                                     'splits_and_capital_reductions': 'no official adjusted data: flips UNKNOWN; candidates flagged by an unexplained move beyond the 10% daily limit',
                                     'evidence': 'outputs/post_archive_readiness/2026-10-24/native7_audit/model_stats.json'},
               'labels': {'rows_without_full_horizon': 'a stock needs a bar in every one of the next N sessions (fixed this round: min_periods=N)'},
               'oos': {**v1['oos'], 'start_definition': 'PENDING_OWNER_DECISION: first session after the later of training_data_end and the freeze time, counting only sessions no research has read',
                       'read_ledger': 'NOT_IMPLEMENTED: no record exists of which post-freeze sessions research has read'},
               'code_changes_status': TRACK_A_STATUS})
    v2['sha256'] = spec_hash(v2)
    write(out('retraining_spec_v2_draft_r3.json'), v2)  # r1, r2 kept; r3 = items 3 and 6 by tests, after the independent review
    print(v2['sha256'], [(c['change'], c['status']) for c in TRACK_A_STATUS])


# ---- Phase 3: readiness matrix. Every blocker of every model is kept; the verdict is the most severe blocker that blocks APPROVING THE START OF TRAINING. ----
SEVERITY = ['DATA_BLOCKED', 'CODE_BLOCKED', 'SPEC_BLOCKED', 'READY_FOR_OWNER_APPROVAL']
COMMON_SPEC = [  # (code, text, scope). Decided by the Owner 2026-10-24 and written into the r4 draft: OD-T1 label policy, OD-T2 manifest hash, OD-T3 OOS start, OD-T4 thresholds, OD-T5
    ('SPEC_NOT_SIGNED', 'retraining-spec-v2 r4 carries OD-T1..T5 but is DRAFT_PENDING_OWNER_APPROVAL; no approval file with its SHA-256 exists', 'TRAINING')]
COMMON_DATA = [
    ('SPLIT_CAPITAL_REDUCTION_COMPLETENESS_UNKNOWN', 'OD-T1b: no official split / capital-reduction source is cached. The 84 detectable post-suspension moves never enter a full-horizon label, but events inside the daily limit are undetectable and the 30-session feature windows after a detected move are unadjusted (<= 3,183 cells, 0.28%). UNKNOWN is not PASS; an official source needs an approved request', 'TRAINING')]
COMMON_CODE = [  # fixed: acceptance evaluation, guard 2 before fitting (FutureLeakageError), recovered listed-universe test, OD-T1 label policy
    ('FRESH_OOS_READ_LEDGER_NOT_IMPLEMENTED', 'OD-T3 is decided (after the freeze, never-read sessions, >= 252) but nothing records which post-freeze sessions research reads, so "never read" cannot be proven', 'OOS'),
    ('LEGACY_RUN_DAILY_UNIVERSE_NOT_LISTED_ONLY', 'NEED_OWNER_APPROVAL: production (legacy) run_daily scores every 4-digit code and ETF in daily_market_data (2,305 on 2026-10-22, 1,224 not TWSE-listed common stocks); models are trained on the listed universe only. Fixing it changes production selection', 'INFERENCE')]
PRIMARY_FEATURE_NOTE = 'the 17-feature model has no revenue or financial-statement inputs (OD-14), so this strategy\'s fundamental rule inputs are not model features'


def native7_matrix():
    import pandas as pd
    stats = read(N7 / 'model_stats.json')
    status = pd.read_csv(DT / 'strategy_status_matrix.csv', encoding='utf-8-sig').set_index('strategy')
    spec = read(SPEC_V1)
    rows, detail = [], []
    for key, info in stats['models'].items():
        train, valid, test = (info['parts'][p] for p in ('train', 'validation', 'test'))
        row_status = status.loc[key]
        blocked_inputs = sorted(k for k, v in json.loads(row_status.DATA_detail).items() if v == 'DATA_BLOCKED')
        blockers = []  # (class, code, text, scope)
        blockers += [('DATA', f'RULE_INPUT_{k.upper()}_DATA_BLOCKED', f'OD-T5: the strategy rules need {k}, which is DATA_BLOCKED; the strategy stays DATA_BLOCKED, only the price/volume model readiness is assessed', 'EVALUATION') for k in blocked_inputs]
        blockers += [('DATA', c, t, s) for c, t, s in COMMON_DATA]
        blockers += [('CODE', c, t, s) for c, t, s in COMMON_CODE]
        blockers += [('SPEC', c, t, s) for c, t, s in COMMON_SPEC]
        checks = info['split_checks']
        if not (checks['train_horizon_ends_before_validation'] and checks['validation_horizon_ends_before_test'] and checks['sets_disjoint']):
            blockers.append(('CODE', 'SPLIT_LEAKAGE', 'a label horizon crosses a split boundary', 'TRAINING'))
        scope_training = [b for b in blockers if b[3] == 'TRAINING']
        verdict = next((s for s in SEVERITY[:3] if any(b[0] + '_BLOCKED' == s for b in scope_training)), 'READY_FOR_OWNER_APPROVAL')
        policy = read(OUT / 'native7_label_policy_stats.json')['models'][key]['parts']
        rows.append({'strategy': key, 'model_name': spec['strategies'][key]['model_name'], 'VERDICT': verdict,
                     'DATA_blockers': ' | '.join(f'{c} [{s}]' for k, c, t, s in blockers if k == 'DATA') or 'none',
                     'CODE_blockers': ' | '.join(f'{c} [{s}]' for k, c, t, s in blockers if k == 'CODE') or 'none',
                     'SPEC_blockers': ' | '.join(f'{c} [{s}]' for k, c, t, s in blockers if k == 'SPEC') or 'none',
                     'horizon_sessions': info['horizon'], 'target_return': info['target_return'],
                     'train_rows': train['rows'], 'train_positive_labels': train['positives'], 'validation_rows': valid['rows'], 'test_rows': test['rows'],
                     'train_rows_full_horizon': policy['train']['rows_full_horizon'], 'train_rows_usable_OD_T1': policy['train']['rows_usable'],
                     'train_rows_excluded_unproven_event': policy['train']['rows_excluded_unproven_twt49u_event'], 'train_rows_excluded_unexplained_move': policy['train']['rows_excluded_unexplained_move'],
                     'train_rows_adjusted_by_proven_event': policy['train']['rows_adjusted_by_proven_event'], 'train_label_flips_vs_raw': policy['train']['label_flips_vs_raw'],
                     'validation_rows_usable_OD_T1': policy['validation']['rows_usable'], 'test_rows_usable_OD_T1': policy['test']['rows_usable'],
                     'split_capital_reduction': 'UNKNOWN (not PASS)',
                     'split_train': ' ~ '.join(info['split']['train'][:2]) + f" ({info['split']['train'][2]})", 'split_validation': ' ~ '.join(info['split']['validation'][:2]) + f" ({info['split']['validation'][2]})",
                     'split_test': ' ~ '.join(info['split']['test'][:2]) + f" ({info['split']['test'][2]})",
                     'sessions_purged_plus_embargo_train_to_validation': checks['sessions_between_train_and_validation'],
                     'owner_must_decide': 'sign retraining-spec-v2 r4; approve an official split / capital-reduction source (OD-T1b); OD-T6 inference universe before go-live' + ('; strategy stays DATA_BLOCKED (OD-T5)' if blocked_inputs else ''),
                     'training_run': 'NO', 'old_model_changed': 'NO'})
        detail += [{'strategy': key, 'class': k, 'code': c, 'blocks': s, 'text': t} for k, c, t, s in blockers]
    pd.DataFrame(rows).to_csv(out('native7_training_readiness_matrix_v4.csv'), index=False, encoding='utf-8-sig')  # v1 kept as evidence
    pd.DataFrame(detail).to_csv(out('native7_blockers_detail_v4.csv'), index=False, encoding='utf-8-sig')
    print(pd.DataFrame(rows)[['strategy', 'VERDICT', 'train_rows_full_horizon', 'train_rows_usable_OD_T1', 'train_label_flips_vs_raw']].to_string(index=False))


def truncation_check(cache=None):
    """Real data: features of a session must not change when every later session is deleted, nor when strategy rules are applied (guardrail 2 and z-score population)."""
    import contextlib
    import io
    import pandas as pd
    from core.research.native_frame import NativeResearchData, load_publication_dates
    from scripts.native7_rules_only import MYSQL, build_snapshot
    spec = read(SPEC_V1)
    mi = _model_inputs(spec)
    stacked = pd.read_parquet(Path(cache) / 'frames.parquet')
    days = sorted(stacked.day.unique())
    fin, rev = (pd.read_parquet(MYSQL / f'snapshot_{n}.parquet') for n in ('financial_statements', 'monthly_revenue'))
    results = []
    for day in (days[100], days[600], days[-60]):
        with contextlib.redirect_stdout(io.StringIO()):
            data = NativeResearchData(build_snapshot('2022-01-03', day), fin[fin.year >= 1911], rev, publications=load_publication_dates())
            truncated, _ = data.frame(day, [], model_inputs=mi)
            with_rules, _ = data.frame(day, ['rsi', 'volume_ratio'], model_inputs=mi)
        full = stacked[stacked.day == day].drop(columns='day').reset_index(drop=True)
        cols = ['stock_id', *spec['feature_order']]
        same = lambda a, b: bool(a.shape == b.shape and (a[['stock_id']].values == b[['stock_id']].values).all() and ((a[cols[1:]] - b[cols[1:]]).abs().fillna(0) <= 1e-12 + 1e-9 * b[cols[1:]].abs().fillna(0)).all().all())
        shared = with_rules.merge(truncated[cols], on='stock_id', suffixes=('', '_t'))
        rule_same = all(((shared[c] - shared[c + '_t']).abs() <= 1e-12 + 1e-9 * shared[c + '_t'].abs()).all() for c in cols[1:])
        results.append({'session': day, 'rows': len(full), 'truncated_equals_full_history_frame': same(truncated.reset_index(drop=True)[cols], full[cols]),
                        'rows_with_rules_subset_of_population': len(with_rules) <= len(truncated), 'z_values_unchanged_by_rules': bool(rule_same)})
    write(out('native7_truncation_check.json'), {'tolerance': 'rtol=1e-9 atol=1e-12', 'sessions': results, 'status': 'PASS' if all(r['truncated_equals_full_history_frame'] and r['z_values_unchanged_by_rules'] for r in results) else 'FAIL'})
    print(results)


# ---- Phase 1: production status trust (read-only: no write to the status file, the scheduler or MySQL; SELECT only) ----
RUN_SQL = "SELECT pipeline_name, step_name, run_date, status, trade_date, started_at, finished_at, error_summary FROM pipeline_runs WHERE run_date >= :since ORDER BY id"
LATEST_SQL = "SELECT pipeline_name, step_name, run_date, status, started_at, finished_at FROM pipeline_runs ORDER BY id DESC LIMIT 8"


def pipeline_rows(since):
    import pandas as pd
    from sqlalchemy import text
    from core.db_helper import get_db_engine  # opens a connection and runs SELECT 1; the ensure_* schema functions are never called
    with get_db_engine().connect() as connection:
        return (pd.read_sql(text(RUN_SQL), connection, params={'since': since}).astype(str).to_dict('records'),
                pd.read_sql(text(LATEST_SQL), connection).astype(str).to_dict('records'))


def log_candidates(after):
    import os
    skip = {'myenv', 'outputs', 'artifacts', '.git', 'node_modules', 'data', '__pycache__'}
    found = []
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for name in files:
            path = Path(folder) / name
            if path.suffix == '.log' and path.stat().st_mtime >= after:
                found.append(path.relative_to(ROOT).as_posix())
    return sorted(found)


def written_inside(rows, mtime) -> bool:
    """pipeline_runs times are naive UTC (core.db_helper uses datetime.utcnow()); compare the file mtime in UTC, never local time."""
    from datetime import datetime, timezone
    stamp = datetime.fromtimestamp(mtime, timezone.utc).replace(tzinfo=None).isoformat(' ')
    return any(r['started_at'] <= stamp <= r['finished_at'] for r in rows if r['finished_at'] not in ('None', 'NaT'))


def phase1():
    from datetime import datetime, timezone
    from scripts.production_status_check import check
    before, now = read(OUT / 'baseline.json')['status_file'], status_file_state()
    checked = check()
    content = json.loads(STATUS_FILE.read_text(encoding='utf-8'))
    changed = now['sha256'] != before['sha256']
    try:
        today_rows, latest_rows = pipeline_rows('2026-10-24')
        sql = 'OK'
    except Exception as error:  # an unreachable database is evidence of nothing: stay UNTRUSTED
        today_rows, latest_rows, sql = [], [], f'UNAVAILABLE: {type(error).__name__}'
    evening = [r for r in today_rows if r['pipeline_name'] == 'evening' and r['step_name'] == 'run_daily']
    success = [r for r in evening if r['status'].lower() == 'success']
    logs = log_candidates(datetime(2026, 10, 24, 18, 25).timestamp())
    mtime = datetime.fromtimestamp(now['mtime'])
    proven = changed and written_inside(success, now['mtime']) and bool(logs)  # design 1, line 21: pipeline_runs AND scheduler log AND writer AND asof
    final = 'PRODUCTION_STATUS_UNTRUSTED'
    if checked['classification'] != 'PRODUCTION_STATUS_UNTRUSTED' and proven and content['asof_date'] == checked['latest_official_session']:
        final = 'PRODUCTION_STATUS_TRUSTED'
    elif checked['classification'] != 'PRODUCTION_STATUS_UNTRUSTED' and not changed:
        final = 'PRODUCTION_STATUS_UNVERIFIED'
    result = {'checked_at': datetime.now().isoformat(timespec='seconds'), 'status_file': {'before': before, 'now': now, 'changed_since_round_start': changed, 'mtime_iso': mtime.isoformat()},
              'content': {'asof_date': content['asof_date'], 'production_eligibility': content['production_eligibility'], 'production_enable_flag': content['production_enable_flag'], 'broker_submission_status': content['broker_submission_status']},
              'content_check_only (never TRUSTED by design; not the final classification)': checked, 'pipeline_runs_select': sql, 'pipeline_runs_since_2026_10_24': today_rows, 'pipeline_runs_latest_8': latest_rows,
              'scheduler_log_files_modified_after_18_25': logs, 'mtime_utc': datetime.fromtimestamp(now['mtime'], timezone.utc).isoformat(), 'writer_proven_by_pipeline_runs_window': proven, 'FINAL_CLASSIFICATION': final,
              'rule': 'a changed file is not evidence; TRUSTED needs a success run_daily row whose window contains the mtime, asof_date equal to the latest official session, and content that is not a known test output',
              'wrote_anything': False}
    write(out('production_status_check_r3.json'), result)  # _utc.json kept: it judged TRUSTED without the scheduler log design 1 requires  # production_status_check.json (18:34) kept: it compared local mtime with UTC rows
    print(json.dumps({k: result[k] for k in ('FINAL_CLASSIFICATION', 'pipeline_runs_select', 'writer_proven_by_pipeline_runs_window', 'scheduler_log_files_modified_after_18_25')}, ensure_ascii=False), 'changed', changed, 'asof', content['asof_date'], 'rows_today', len(today_rows))


COMMANDS = {'baseline': baseline, 'verify': verify, 'mops-diff': mops_diff, 'native7': native7, 'strategy15': strategy15, 'gate-check': gate_check, 'data-manifest': data_manifest, 'spec-v2': spec_v2, 'native7-matrix': native7_matrix, 'truncation-check': truncation_check, 'phase1': phase1, 'label-policy': label_policy, 'manifest-recheck': manifest_recheck, 'spec-v2-r4': spec_v2_r4}

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=tuple(COMMANDS))
    parser.add_argument('--cache', help='native7: folder for the per-session frame cache (default inside the output folder)')
    args = parser.parse_args()
    COMMANDS[args.command](**({'cache': args.cache} if args.command in ('native7', 'truncation-check', 'label-policy') else {}))
