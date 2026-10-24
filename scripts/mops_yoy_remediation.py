"""MOPS PIT v3 YoY remediation (change 2026-10-24-remediate-mops-yoy-basis): a NEW dataset version; the v3 files are only read.

Defect: add_derived_metrics divided a standalone current value by a cumulative prior-year value (or the reverse) when the prior-year chain of quarters
was incomplete in the PIT records. Rule of the new version: a YoY exists only when current and prior-year values share the same REPORTING_BASIS; otherwise the
row is removed and listed with its reason. Nothing is imputed, and nothing from the raw cache is used to fill a PIT gap (the raw cache has no publication time
for a quarter whose publication record is missing, so it cannot be point-in-time).
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OLD = ROOT / 'data/processed/fundamental_pit_v3'
RUN = ROOT / 'outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z'
NEW = ROOT / 'data/processed/fundamental_pit_v3_yoy_same_basis'
YOY = {'revenue_yoy': 'revenue', 'operating_income_yoy': 'operating_profit', 'revenue_cumulative_yoy': 'revenue', 'operating_income_cumulative_yoy': 'operating_profit', 'eps_yoy': 'eps'}
RAW = ('revenue', 'operating_profit', 'eps')
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()


def raw_cumulative():
    frames = []
    for path in sorted((RUN / 'financial_cache').glob('mops_t163sb04_*.parquet')):
        frame = pd.read_parquet(path)
        frame = frame.rename(columns={'year': 'fiscal_year', 'operating_profit': 'operating_profit'})
        frame['ticker'] = frame.stock_id.astype('Int64').astype(str)
        frames.append(frame[['ticker', 'fiscal_year', 'quarter', *RAW]])
    return pd.concat(frames, ignore_index=True).drop_duplicates(['ticker', 'fiscal_year', 'quarter'], keep='last')


def raw_check(records, raw):
    """Every base record against the raw cache: cumulative source_value, and standalone = cumulative(q) - cumulative(q-1) (Q4 = annual - 9 months)."""
    base = records[records.metric.isin(RAW)].drop_duplicates(['ticker', 'fiscal_year', 'quarter', 'metric'], keep='last')
    long = raw.melt(id_vars=['ticker', 'fiscal_year', 'quarter'], value_vars=list(RAW), var_name='metric', value_name='cumulative')
    previous = long.assign(quarter=long.quarter + 1).rename(columns={'cumulative': 'previous_cumulative'})
    joined = base.merge(long, on=['ticker', 'fiscal_year', 'quarter', 'metric'], how='left').merge(previous, on=['ticker', 'fiscal_year', 'quarter', 'metric'], how='left')
    expected = np.where((joined.metric == 'eps') | (joined.quarter == 1) | joined.previous_cumulative.isna(), joined.cumulative, joined.cumulative - joined.previous_cumulative)
    standalone_expected = joined.REPORTING_BASIS.eq('STANDALONE') & joined.metric.ne('eps')
    ok_cum = np.isclose(joined.source_value, joined.cumulative, rtol=1e-9, atol=1e-9)
    ok_value = np.isclose(joined.value, np.where(standalone_expected, expected, joined.cumulative), rtol=1e-9, atol=1e-9)
    detail = {'base_rows': int(len(joined)), 'raw_missing': int(joined.cumulative.isna().sum()), 'source_value_mismatch': int((~ok_cum & joined.cumulative.notna()).sum()),
              'value_mismatch': int((~ok_value & joined.cumulative.notna()).sum())}
    q4 = joined[(joined.quarter == 4) & standalone_expected]
    detail['q4_standalone_rows'] = int(len(q4))
    detail['q4_standalone_mismatch'] = int((~np.isclose(q4.value, q4.cumulative - q4.previous_cumulative, rtol=1e-9, atol=1e-9) & q4.previous_cumulative.notna()).sum())
    detail['standalone_rows_without_previous_quarter_in_raw'] = int((standalone_expected & (joined.quarter > 1) & joined.previous_cumulative.isna()).sum())
    return detail


def remediate():
    if NEW.exists():
        raise SystemExit(f'{NEW} exists; a dataset version is never overwritten')
    old_hash_before = {p.name: sha(p) for p in sorted(OLD.iterdir()) if p.is_file()}
    records = pd.read_parquet(OLD / 'fundamental_records.parquet')
    raw = raw_cumulative()
    pub = pd.read_csv(RUN / 'publication_records_full.csv', dtype={'ticker': str})
    published = set(zip(pub.ticker, pub.fiscal_year.astype(int), pub.quarter.astype(int)))
    chain_complete = lambda ticker, year: all((ticker, year, q - 1) in published for q in range(2, 5) if (ticker, year, q) in published)  # standalone_values: one gap makes the whole year CUMULATIVE
    gaps = lambda ticker, year: ','.join(str(q) for q in range(1, 5) if (ticker, year, q) not in published)
    base = records[records.metric.isin(RAW)].drop_duplicates(['ticker', 'fiscal_year', 'quarter', 'metric'], keep='last').set_index(['ticker', 'fiscal_year', 'quarter', 'metric'])
    raw_index = raw.set_index(['ticker', 'fiscal_year', 'quarter'])
    rows, keep = [], np.ones(len(records), dtype=bool)
    for position, r in enumerate(records.itertuples(index=False)):
        if r.metric not in YOY:
            continue
        source = YOY[r.metric]
        cur, prior = base.REPORTING_BASIS.get((r.ticker, r.fiscal_year, r.quarter, source)), base.REPORTING_BASIS.get((r.ticker, r.fiscal_year - 1, r.quarter, source))
        prior_value = base.value.get((r.ticker, r.fiscal_year - 1, r.quarter, source))
        cur_value = base.value.get((r.ticker, r.fiscal_year, r.quarter, source))
        same = cur is not None and cur == prior
        recomputed = cur_value / prior_value - 1 if same and prior_value not in (None, 0) else None
        item = {'ticker': r.ticker, 'fiscal_year': int(r.fiscal_year), 'quarter': int(r.quarter), 'metric': r.metric, 'stored_value': float(r.value), 'current_basis': cur, 'prior_year_basis': prior,
                'same_basis': bool(same), 'recomputed_same_basis': recomputed}
        if same:
            if recomputed is None or not np.isclose(recomputed, r.value, rtol=1e-9, atol=1e-12):
                item['action'] = 'FORMULA_DIFFERENCE'
                keep[position] = False  # a stored value that the same-basis formula does not reproduce is not carried over
            else:
                continue
        else:
            keep[position] = False
            reason = ('PRIOR_YEAR_QUARTER_CHAIN_INCOMPLETE' if not chain_complete(r.ticker, r.fiscal_year - 1) else
                      'CURRENT_YEAR_QUARTER_CHAIN_INCOMPLETE' if not chain_complete(r.ticker, r.fiscal_year) else 'BASIS_DIFFERENT_OTHER')
            item.update(action='REMOVED_BASIS_MISMATCH', reason=reason, prior_year_quarters_without_publication_record=gaps(r.ticker, r.fiscal_year - 1),
                        current_year_quarters_without_publication_record=gaps(r.ticker, r.fiscal_year))
            try:  # diagnostic only, NOT point-in-time: what the raw cache would give on a common basis
                cur_raw, prior_raw = raw_index.loc[(r.ticker, r.fiscal_year, r.quarter), source], raw_index.loc[(r.ticker, r.fiscal_year - 1, r.quarter), source]
                if source != 'eps' and r.quarter > 1 and cur == 'STANDALONE':
                    cur_raw -= raw_index.loc[(r.ticker, r.fiscal_year, r.quarter - 1), source]
                    prior_raw -= raw_index.loc[(r.ticker, r.fiscal_year - 1, r.quarter - 1), source]
                item['diagnostic_raw_cache_same_basis_NOT_PIT'] = float(cur_raw / prior_raw - 1) if prior_raw not in (0, None) and not pd.isna(prior_raw) else None
            except KeyError:
                item['diagnostic_raw_cache_same_basis_NOT_PIT'] = None
        rows.append(item)
    detail = pd.DataFrame(rows)
    new_records = records[keep].reset_index(drop=True)
    NEW.mkdir(parents=True)
    new_records.to_parquet(NEW / 'fundamental_records.parquet', index=False)
    detail.to_csv(NEW / 'correction_detail.csv', index=False, encoding='utf-8-sig')
    removed = detail[detail.action == 'REMOVED_BASIS_MISMATCH']
    q4 = raw_check(records, raw)
    old_hash_after = {p.name: sha(p) for p in sorted(OLD.iterdir()) if p.is_file()}
    manifest = {
        'dataset_version': 'fundamental-pit-v3.1-yoy-same-basis', 'created_at': datetime.now().isoformat(timespec='seconds'), 'change': 'openspec/changes/2026-10-24-remediate-mops-yoy-basis',
        'rule': 'a YoY row exists only if the current value and the prior-year value of the same quarter have the same REPORTING_BASIS; other rows are removed, never imputed; no raw-cache value fills a PIT gap',
        'old_dataset': {'path': str(OLD.relative_to(ROOT)), 'records_sha256': old_hash_before['fundamental_records.parquet'], 'all_files_unchanged_by_this_run': old_hash_before == old_hash_after, 'files_checked': len(old_hash_before)},
        'new_dataset': {'path': str(NEW.relative_to(ROOT)), 'records_sha256': sha(NEW / 'fundamental_records.parquet'), 'correction_detail_sha256': sha(NEW / 'correction_detail.csv'),
                        'records_old': int(len(records)), 'records_new': int(len(new_records))},
        'removed_rows': {'total': int(len(removed)), 'by_metric': removed.metric.value_counts().to_dict(), 'by_reason': removed.reason.value_counts().to_dict(),
                         'by_basis_pair': {f'{a}/{b}': int(n) for (a, b), n in removed.groupby(['current_basis', 'prior_year_basis']).size().items()},
                         'by_quarter': {int(k): int(v) for k, v in removed.groupby('quarter').size().items()}, 'by_fiscal_year': {int(k): int(v) for k, v in removed.groupby('fiscal_year').size().items()}},
        'formula_difference_rows': int((detail.action == 'FORMULA_DIFFERENCE').sum()),
        'yoy_rows_before': {m: int((records.metric == m).sum()) for m in YOY}, 'yoy_rows_after': {m: int((new_records.metric == m).sum()) for m in YOY},
        'raw_cache_check_of_base_values': q4,
        'not_a_pit_certification': 'G2/G3 stay DATA_BLOCKED: the dataset still has only one 2026-09-15 retrieval of values, no first-published value, and 200-filing sampling only; the matrix is not rebuilt in this step'}
    (NEW / 'manifest.json').write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: manifest[k] for k in ('removed_rows', 'formula_difference_rows', 'yoy_rows_before', 'yoy_rows_after', 'raw_cache_check_of_base_values')}, indent=1, ensure_ascii=False)); print('old unchanged:', manifest['old_dataset']['all_files_unchanged_by_this_run'])


def matrix():
    """Rebuild the matrix from the new records with the exact method that reproduces the v3 matrix (checked: rebuild(old records) == old matrix), then diff every cell."""
    import warnings

    from core.research.fundamental_pit_v3 import build_matrix, ticker_hash

    warnings.filterwarnings('ignore', category=FutureWarning)
    target = NEW / 'fundamental_matrix.parquet'
    if target.exists():
        raise SystemExit(f'{target} exists; never overwritten')
    old = pd.read_parquet(OLD / 'fundamental_matrix.parquet')
    tickers, asof = sorted(old.ticker.astype(str).unique()), tuple(pd.to_datetime(old.asof_date).drop_duplicates().sort_values())
    rebuilt_old, _ = build_matrix(pd.read_parquet(OLD / 'fundamental_records.parquet'), tickers, asof)
    pd.testing.assert_frame_equal(old.reset_index(drop=True), rebuilt_old.reset_index(drop=True), check_dtype=False, rtol=0, atol=0)  # the method reproduces v3 exactly
    new, _ = build_matrix(pd.read_parquet(NEW / 'fundamental_records.parquet'), tickers, asof)
    new.to_parquet(target, index=False)
    a, b = old.reset_index(drop=True), new.reset_index(drop=True)
    same_keys = (a.asof_date.equals(b.asof_date)) and (a.ticker.equals(b.ticker))
    metrics = [c for c in a.columns if c not in ('asof_date', 'ticker')]
    changed = {}
    for c in metrics:
        diff = ~((a[c] == b[c]) | (a[c].isna() & b[c].isna()))
        became_nan, other = diff & b[c].isna() & a[c].notna(), diff & b[c].notna()
        changed[c] = {'cells_changed': int(diff.sum()), 'value_to_missing': int(became_nan.sum()), 'value_to_other_value': int(other.sum()),
                      'asof_dates_affected': int(a.loc[diff, 'asof_date'].nunique()), 'tickers_affected': int(a.loc[diff, 'ticker'].nunique())}
    write_json = lambda path, payload: Path(path).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding='utf-8')
    write_json(NEW / 'matrix_manifest.json', {
        'dataset_version': 'fundamental-pit-v3.1-yoy-same-basis', 'method': 'core.research.fundamental_pit_v3.build_matrix on the new records; v3 matrix reproduced exactly from v3 records first (assert_frame_equal, rtol=0, atol=0)',
        'tickers': len(tickers), 'ticker_sha256': ticker_hash(tickers), 'asof_dates': len(asof), 'asof_range': [str(asof[0].date()), str(asof[-1].date())], 'rows': int(len(new)), 'same_row_keys_as_v3': bool(same_keys),
        'matrix_sha256': sha(target), 'v3_matrix_sha256': sha(OLD / 'fundamental_matrix.parquet'), 'v3_matrix_unchanged': True, 'changed_cells_by_metric': changed,
        'columns_not_changed': [c for c in metrics if changed[c]['cells_changed'] == 0],
        'not_a_pit_certification': 'G2/G3 stay DATA_BLOCKED; changed cells show what the same-basis rule alone does, not that the remaining values are certified'})
    print(json.dumps({c: v for c, v in changed.items() if v['cells_changed']}, indent=1)); print('same row keys:', same_keys, '| unchanged columns:', [c for c in metrics if changed[c]['cells_changed'] == 0])


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    {'records': remediate, 'matrix': matrix}[sys.argv[1] if len(sys.argv) > 1 else 'records']()
