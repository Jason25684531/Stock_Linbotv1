"""v3.1 same-basis YoY dataset: no mixed-basis YoY row remains, retained rows are v3's rows, v3 itself is untouched."""
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OLD, NEW = ROOT / 'data/processed/fundamental_pit_v3', ROOT / 'data/processed/fundamental_pit_v3_yoy_same_basis'
YOY = {'revenue_yoy': 'revenue', 'operating_income_yoy': 'operating_profit', 'revenue_cumulative_yoy': 'revenue', 'operating_income_cumulative_yoy': 'operating_profit', 'eps_yoy': 'eps'}


def test_no_yoy_row_mixes_standalone_and_cumulative_values():
    records = pd.read_parquet(NEW / 'fundamental_records.parquet')
    base = records[records.metric.isin(['revenue', 'operating_profit', 'eps'])].drop_duplicates(['ticker', 'fiscal_year', 'quarter', 'metric'], keep='last').set_index(['ticker', 'fiscal_year', 'quarter', 'metric']).REPORTING_BASIS
    for derived, source in YOY.items():
        d = records[records.metric == derived]
        current = [base.get((t, y, q, source)) for t, y, q in zip(d.ticker, d.fiscal_year, d.quarter)]
        prior = [base.get((t, y - 1, q, source)) for t, y, q in zip(d.ticker, d.fiscal_year, d.quarter)]
        assert current == prior, derived


def test_only_the_listed_rows_were_removed_and_v3_is_unchanged():
    manifest = json.loads((NEW / 'manifest.json').read_text(encoding='utf-8'))
    old, new = pd.read_parquet(OLD / 'fundamental_records.parquet'), pd.read_parquet(NEW / 'fundamental_records.parquet')
    detail = pd.read_csv(NEW / 'correction_detail.csv', dtype={'ticker': str})
    removed = detail[detail.action == 'REMOVED_BASIS_MISMATCH']
    assert len(old) - len(new) == len(removed) == manifest['removed_rows']['total'] == 944 and manifest['formula_difference_rows'] == 0
    key = ['ticker', 'fiscal_year', 'quarter', 'metric', 'revision_id']
    gone = old.merge(removed[key[:4]], on=key[:4], how='inner')
    assert len(gone) == len(removed) and set(gone.metric) <= set(YOY)
    kept = old.merge(removed[key[:4]], on=key[:4], how='left', indicator=True).query('_merge == "left_only"').drop(columns='_merge')
    pd.testing.assert_frame_equal(kept.reset_index(drop=True), new.reset_index(drop=True))  # every other row is exactly v3's row
    for name in ('fundamental_records.parquet', 'fundamental_matrix.parquet'):
        assert hashlib.sha256((OLD / name).read_bytes()).hexdigest() == (manifest['old_dataset']['records_sha256'] if name.endswith('records.parquet') else json.loads((NEW / 'matrix_manifest.json').read_text(encoding='utf-8'))['v3_matrix_sha256'])
