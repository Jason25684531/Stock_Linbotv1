"""Fundamental PIT v3.2: revision audit from the cached official MOPS t57sb01 filing lists and a verified G2/G3 dataset (offline).

Evidence: each cached listing row carries the filing's upload time and the official financial-report correction column.
A period is VERIFIED when its row is found, the correction column is NONE_MARK and the upload time equals the recorded
publication time; a corrected period is EXCLUDED (the original values are no longer published); anything else is UNVERIFIED."""
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

SOURCE = ROOT / 'data/processed/fundamental_pit_v3_yoy_same_basis'
CACHE = ROOT / 'outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z/publication_cache/mops_publication'
TARGET = ROOT / 'data/processed/fundamental_pit_v3_2_verified'
BASE = {'operating_income_yoy': 'operating_profit', 'eps_yoy': 'eps'}
SEASON = {'第一季': 1, '第二季': 2, '第三季': 3, '第四季': 4}  # first..fourth quarter
DEADLINE = {1: '-05-15', 2: '-08-14', 3: '-11-14'}  # Q4 deadline is March 31 of the next year
NONE_MARK = '無'  # the listing's correction column reads this when no correction was ever filed
PERIOD = re.compile('(\\d+) *年 *(第.季)')  # roc year, then the quarter label


def listing_rows(html):
    """Data rows of one t57sb01 listing: (roc_year, season, filename, upload Taipei time, correction text)."""
    text = html.decode('cp950', errors='replace')
    rows = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.S | re.I):
        raw_cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)
        cells = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', c)).replace('&nbsp;', '').strip() for c in raw_cells]
        if len(cells) >= 11 and not cells[10]:
            link = re.search(r"location\.href='([^']+)'", raw_cells[10])  # a details button: a correction or supplement record exists
            cells[10] = 'CORRECTION_RECORD ' + link[1] if link else ''
        if len(cells) < 11 or not cells[0].isdigit():
            continue
        period = PERIOD.match(cells[1])
        upload = re.match(r'(\d+)/(\d+)/(\d+) (\d+:\d+:\d+)', cells[9])
        if not period or not upload or period[2] not in SEASON:
            continue
        stamp = f'{int(upload[1]) + 1911}-{upload[2]}-{upload[3]}T{upload[4]}+08:00'
        rows.append({'roc_year': int(period[1]), 'season': SEASON[period[2]], 'kind': cells[2], 'nature': cells[4], 'filename': cells[7],
                     'upload_timestamp': stamp, 'correction': cells[10]})
    return rows


def audit(records, since=2021):
    """One row per (ticker, fiscal year, quarter) base filing from fiscal year since on."""
    base = records.loc[records['metric'].isin(BASE.values()) & (records['fiscal_year'] >= since)]
    periods = base.drop_duplicates(['ticker', 'fiscal_year', 'quarter'])[['ticker', 'fiscal_year', 'quarter', 'publication_timestamp', 'source_record_id']]
    parsed, out = {}, []
    for p in periods.itertuples(index=False):
        roc = p.fiscal_year - 1911
        key = f'mops.t57sb01|ticker={p.ticker}|roc_year={roc}|season={p.quarter}|statement_type=income_statement'
        html_path = CACHE / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.html"
        filename = str(p.source_record_id).rsplit(':', 1)[-1]
        row = {'ticker': p.ticker, 'fiscal_year': p.fiscal_year, 'quarter': p.quarter, 'filename': filename,
               'dataset_publication_timestamp': p.publication_timestamp, 'evidence_file': html_path.name if html_path.exists() else '',
               'evidence_sha256': '', 'listing_upload_timestamp': '', 'correction': '', 'matching_rows': 0}
        if html_path.exists():
            if html_path not in parsed:
                raw = html_path.read_bytes()
                parsed[html_path] = (hashlib.sha256(raw).hexdigest(), listing_rows(raw))
            digest, rows = parsed[html_path]
            hits = [r for r in rows if r['roc_year'] == roc and r['season'] == p.quarter and r['filename'] == filename]
            row.update(evidence_sha256=digest, matching_rows=len(hits))
            if hits:
                row.update(listing_upload_timestamp=hits[0]['upload_timestamp'], correction=hits[0]['correction'])
        deadline = f'{p.fiscal_year + 1}-03-31' if p.quarter == 4 else f'{p.fiscal_year}{DEADLINE[p.quarter]}'
        same_time = row['listing_upload_timestamp'] != '' and pd.Timestamp(row['listing_upload_timestamp']) == pd.Timestamp(p.publication_timestamp)
        if row['matching_rows'] != 1 or not same_time:
            status, reason = 'UNVERIFIED', 'listing row missing, duplicated or upload time differs from recorded publication time'
        elif row['correction'].startswith('CORRECTION_RECORD'):
            status, reason = 'EXCLUDED', ('official correction or supplement record (t56sb31_q1 details, outside the public fetch scope); '
                                          'whether values changed cannot be shown, so first-published values are not confirmed')
        elif row['correction'] != NONE_MARK:
            status, reason = 'UNVERIFIED', 'correction column unreadable'
        else:
            status, reason = 'VERIFIED', 'official listing shows no correction; upload time equals recorded publication time'
        late = row['listing_upload_timestamp'] != '' and pd.Timestamp(row['listing_upload_timestamp']).date() > (pd.Timestamp(deadline) + pd.Timedelta(days=15)).date()
        out.append({**row, 'statutory_deadline': deadline, 'late_upload': late, 'verification_status': status, 'reason': reason})
    return pd.DataFrame(out)


def prior_later(records):
    """YoY rows whose prior-year base value became available after the stored YoY availability (the A4 defect), all years."""
    base = records.loc[records['metric'].isin(BASE.values())].set_index(['ticker', 'fiscal_year', 'quarter', 'metric'])['available_date']
    rows = []
    for metric, base_metric in BASE.items():
        yoy = records.loc[records['metric'].eq(metric)]
        prior = base.reindex(pd.MultiIndex.from_arrays([yoy['ticker'], yoy['fiscal_year'] - 1, yoy['quarter'], [base_metric] * len(yoy)])).to_numpy()
        late = pd.to_datetime(prior) > pd.to_datetime(yoy['available_date']).to_numpy()
        rows.append(yoy.loc[late, ['ticker', 'metric', 'fiscal_year', 'quarter', 'publication_date', 'available_date']].assign(prior_available_date=prior[late]))
    return pd.concat(rows, ignore_index=True)


def verified_records(records, audit_frame, start=2022):
    """G2/G3 rows whose YoY availability is the later of current and prior-year base availability, kept only when both filings are VERIFIED."""
    status = audit_frame.set_index(['ticker', 'fiscal_year', 'quarter'])['verification_status']
    base = records.loc[records['metric'].isin(BASE.values())].set_index(['ticker', 'fiscal_year', 'quarter', 'metric'])
    out = []
    for metric, base_metric in BASE.items():
        yoy = records.loc[records['metric'].eq(metric) & (records['fiscal_year'] >= start)].copy()
        prior = base.reindex(pd.MultiIndex.from_arrays([yoy['ticker'], yoy['fiscal_year'] - 1, yoy['quarter'], [base_metric] * len(yoy)]))
        for col in ('available_date', 'publication_date'):
            yoy[col] = pd.concat([pd.to_datetime(yoy[col]).reset_index(drop=True), pd.to_datetime(prior[col]).reset_index(drop=True)],
                                 axis=1).max(axis=1).dt.strftime('%Y-%m-%d').to_numpy()
        current = status.reindex(pd.MultiIndex.from_arrays([yoy['ticker'], yoy['fiscal_year'], yoy['quarter']])).to_numpy()
        previous = status.reindex(pd.MultiIndex.from_arrays([yoy['ticker'], yoy['fiscal_year'] - 1, yoy['quarter']])).to_numpy()
        yoy['verification_status'] = ['VERIFIED' if a == 'VERIFIED' and b == 'VERIFIED' else 'EXCLUDED' if 'EXCLUDED' in (a, b) else 'UNVERIFIED'
                                      for a, b in zip(current, previous)]
        out.append(yoy)
    return pd.concat(out, ignore_index=True)


def build():
    records = pd.read_parquet(SOURCE / 'fundamental_records.parquet')
    records['ticker'] = records['ticker'].astype(str)
    frame = audit(records)
    defects = prior_later(records)
    yoy = verified_records(records, frame)
    TARGET.mkdir(parents=True, exist_ok=True)
    frame.to_csv(TARGET / 'publication_revision_audit.csv', index=False, encoding='utf-8-sig')
    defects.to_csv(TARGET / 'prior_year_available_later.csv', index=False, encoding='utf-8-sig')
    kept = yoy.loc[yoy['verification_status'].eq('VERIFIED')]
    kept.to_parquet(TARGET / 'fundamental_records.parquet', index=False)
    manifest = {'dataset_version': 'fundamental-pit-v3.2-verified', 'created_from': 'fundamental-pit-v3.1-yoy-same-basis (files unchanged)',
                'source_records_sha256': workflow.sha256(SOURCE / 'fundamental_records.parquet'),
                'records_sha256': workflow.sha256(TARGET / 'fundamental_records.parquet'),
                'audit_sha256': workflow.sha256(TARGET / 'publication_revision_audit.csv'),
                'rules': ['YoY available_date and publication_date = later of current and prior-year base filing',
                          'keep a YoY row only when both base filings are VERIFIED in the official listing audit'],
                'audit_status_counts': frame['verification_status'].value_counts().to_dict(),
                'correction_values': frame['correction'].value_counts().head(20).to_dict(),
                'late_uploads': int(frame['late_upload'].sum()),
                'prior_year_available_later_rows_all_years': len(defects),
                'yoy_rows_by_status': yoy.groupby(['metric', 'verification_status']).size().rename('rows').reset_index().to_dict('records'),
                'yoy_rows_kept': len(kept), 'external_requests': 0,
                'evidence': 'cached official doc.twse.com.tw t57sb01 listings retrieved 2026-09-15; SHA-256 per row in the audit'}
    (TARGET / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    return manifest


def matrix():
    """As-of matrix of the verified rows with the existing builder, on v3.1 trading days up to 2025-12-31 and the same 890 tickers."""
    from core.research.fundamental_pit_v3 import build_matrix
    old = pd.read_parquet(SOURCE / 'fundamental_matrix.parquet', columns=['asof_date', 'ticker'])
    dates = sorted(d for d in pd.to_datetime(old['asof_date']).unique() if d <= pd.Timestamp('2025-12-31'))
    frame, _ = build_matrix(pd.read_parquet(TARGET / 'fundamental_records.parquet'), old['ticker'].astype(str).unique(), dates)
    frame.to_parquet(TARGET / 'fundamental_matrix.parquet', index=False)
    manifest = json.loads((TARGET / 'manifest.json').read_text(encoding='utf-8'))
    manifest.update(matrix_sha256=workflow.sha256(TARGET / 'fundamental_matrix.parquet'), matrix_rows=len(frame),
                    matrix_dates=[str(dates[0].date()), str(dates[-1].date()), len(dates)])
    (TARGET / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    return {k: manifest[k] for k in ('matrix_sha256', 'matrix_rows', 'matrix_dates')}


if __name__ == '__main__':
    print(json.dumps(matrix() if sys.argv[1:] == ['matrix'] else build(), ensure_ascii=False, indent=1, default=str))
