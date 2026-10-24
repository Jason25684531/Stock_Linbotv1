"""200-filing reproducible MOPS re-parse (closure of task 1.3): cached raw source -> values, publication time, availability, revisions.

Reads only the existing raw caches of the PIT v3 run (no network): publication_cache (t57sb01 HTML) and financial_cache (t163sb04 parquet).
Each sampled filing is graded PASS / FAIL / BLOCKED. 200 PASS filings do not certify the other 38,414: the summary says so.
"""
import hashlib
import json
import math
import sys
from datetime import datetime, time as clock_time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.fundamental_pit import PublicationRequest, publication_request_key
from core.research.fundamentals.pit import available_date_for_publication, load_research_trading_calendar, parse_mops_publication_html
from scripts.audit_strategy_history import write

RUN = ROOT / 'outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z'
RECORDS = ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet'
OUT = ROOT / 'outputs/dual_track_validation/2026-10-23/closure'
SEED, N, PER_STRATUM = 20261024, 200, 3
RAW_METRICS = ('revenue', 'operating_profit', 'eps')
TOL = {'rtol': 1e-9, 'atol': 1e-9}
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sample(filings):
    """3 filings per (fiscal_year, quarter) stratum, then uniformly random fillers from the rest; fixed seed."""
    rng = np.random.default_rng(SEED)
    picked = []
    for _, group in filings.groupby(['fiscal_year', 'quarter']):
        picked += list(rng.choice(group.index.to_numpy(), size=min(PER_STRATUM, len(group)), replace=False))
    rest = filings.index.difference(picked).to_numpy()
    picked += list(rng.choice(rest, size=N - len(picked), replace=False))
    return filings.loc[picked].sort_values(['fiscal_year', 'quarter', 'ticker'])


class Raw:
    """Cumulative t163sb04 values from the cached parquet files, with the SHA-256 of every file actually read."""

    def __init__(self):
        self.cache, self.hashes = {}, {}

    def frame(self, fiscal_year, quarter):
        key = (fiscal_year, quarter)
        if key not in self.cache:
            parts = []
            for market in ('sii', 'otc'):
                path = RUN / 'financial_cache' / f'mops_t163sb04_{fiscal_year - 1911}_q{quarter}_{market}.parquet'
                if path.exists():
                    self.hashes[path.name] = sha(path)
                    parts.append(pd.read_parquet(path))
            self.cache[key] = pd.concat(parts, ignore_index=True) if parts else None
        return self.cache[key]

    def cumulative(self, ticker, fiscal_year, quarter, metric):
        frame = self.frame(fiscal_year, quarter)
        if frame is None or not ticker.isdigit():
            return None
        row = frame[frame.stock_id.astype('Int64') == int(ticker)]
        value = row[metric].iloc[0] if len(row) else np.nan
        return None if pd.isna(value) else float(value)

    def expected(self, ticker, fiscal_year, quarter, metric):
        """(value, basis) the way standalone_values defines it: eps stays cumulative; other metrics are cumulative(q) - cumulative(q-1) when q-1 exists."""
        current = self.cumulative(ticker, fiscal_year, quarter, metric)
        if current is None:
            return None, None
        if metric == 'eps' or quarter == 1:
            return current, 'CUMULATIVE' if metric == 'eps' else 'STANDALONE'
        previous = self.cumulative(ticker, fiscal_year, quarter - 1, metric)
        return (current - previous, 'STANDALONE') if previous is not None else (current, 'CUMULATIVE')


def close(a, b):
    return a is not None and b is not None and math.isclose(float(a), float(b), rel_tol=TOL['rtol'], abs_tol=TOL['atol'])


def expected_derived(raw, ticker, fiscal_year, quarter, metric):
    if metric == 'operating_margin':
        op, rev = raw.expected(ticker, fiscal_year, quarter, 'operating_profit'), raw.expected(ticker, fiscal_year, quarter, 'revenue')
        if None in (op[0], rev[0]) or op[1] != 'STANDALONE' or rev[1] != 'STANDALONE' or rev[0] == 0:
            return None
        return op[0] / rev[0]
    base = {'revenue_yoy': 'revenue', 'operating_income_yoy': 'operating_profit', 'eps_yoy': 'eps',
            'revenue_cumulative_yoy': 'revenue', 'operating_income_cumulative_yoy': 'operating_profit'}[metric]
    current, previous = raw.expected(ticker, fiscal_year, quarter, base), raw.expected(ticker, fiscal_year - 1, quarter, base)
    if None in (current[0], previous[0]) or previous[0] == 0:
        return None
    return current[0] / previous[0] - 1


def check(item, records, raw, calendar, stored_rules):
    ticker, fy, q = item.ticker, int(item.fiscal_year), int(item.quarter)
    out = {'ticker': ticker, 'fiscal_year': fy, 'quarter': q, 'revision_id': item.revision_id, 'source_record_id': item.source_record_id, 'problems': [], 'blocked': []}
    # 1. source and hash
    request = PublicationRequest(ticker, fy - 1911, q, publication_request_key(ticker, fy - 1911, q))
    digest = hashlib.sha256(request.request_key.encode('utf-8')).hexdigest()  # the cache file name is the SHA-256 of the request key
    html_path, meta_path = RUN / 'publication_cache/mops_publication' / f'{digest}.html', RUN / 'publication_cache/mops_publication' / f'{digest}.json'
    if not html_path.exists() or not meta_path.exists():
        out['blocked'].append('PUBLICATION_CACHE_MISSING')
        out.update(html_sha256=None, meta_sha256=None)
    else:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        out.update(html_sha256=sha(html_path), meta_sha256=sha(meta_path), classification=meta.get('classification'), meta_request_key=meta.get('request_key'))
        if meta.get('classification') != 'REAL_DATA_PAGE':
            out['problems'].append(f"CLASSIFICATION_{meta.get('classification')}")
        if meta.get('request_key') != publication_request_key(ticker, fy - 1911, q):
            out['problems'].append('META_REQUEST_KEY_DIFFERS_FROM_FILE_NAME_HASH')
        # 2. publication time and availability, re-parsed from the HTML
        try:
            parsed = [p for p in parse_mops_publication_html(html_path.read_bytes(), request, calendar) if (p.fiscal_year, p.quarter) == (fy, q)]
        except Exception as exc:  # parser refusing a page is a result, not an exception to hide
            parsed, out['problems'] = [], out['problems'] + [f'REPARSE_FAILED: {type(exc).__name__}: {exc}']
        out['html_rows_for_period'] = len(parsed)
        if len(parsed) > 1:
            out['problems'].append('REVISION_IN_SOURCE_NOT_IN_DATASET')  # the dataset keeps one filing per period
        filename = lambda record_id: record_id.rsplit(':', 1)[-1]  # the stored id carries the request context (batch page: ...:0:Q0:), the filing file name is the identity
        match = [p for p in parsed if filename(p.source_record_id) == filename(item.source_record_id)]
        if parsed and not match:
            out['problems'].append('FILING_FILE_NAME_NOT_FOUND_IN_HTML')
        if match:
            p = match[0]
            stored_ts = pd.Timestamp(item.publication_timestamp)
            if str(p.publication_date) != str(item.publication_date) or pd.Timestamp(p.publication_timestamp) != stored_ts:
                out['problems'].append(f'PUBLICATION_TIME_MISMATCH html={p.publication_timestamp} stored={item.publication_timestamp}')
            if str(p.available_date) != str(item.available_date):
                out['problems'].append(f'AVAILABLE_DATE_MISMATCH recomputed={p.available_date} stored={item.available_date}')
            if p.availability_rule != stored_rules.get((ticker, fy, q)):
                out['problems'].append(f'AVAILABILITY_RULE_MISMATCH {p.availability_rule} vs {stored_rules.get((ticker, fy, q))}')
            out.update(publication_timestamp=str(p.publication_timestamp), available_date=str(p.available_date), availability_rule=p.availability_rule)
            # 4. usable at the signal date: never before publication, same day only when published before the 13:30 close
            if p.available_date < p.publication_date:
                out['problems'].append('AVAILABLE_BEFORE_PUBLICATION')
            if p.available_date == p.publication_date and not (p.publication_timestamp and p.publication_timestamp.timetz().replace(tzinfo=None) < clock_time(13, 30)):
                out['problems'].append('SAME_DAY_AVAILABLE_AFTER_CLOSE')
            earlier = [d for d in calendar if d < p.available_date]
            out['usable_on_signal_date'] = p.available_date in calendar or p.available_date >= min(calendar)
            out['not_usable_before'] = (earlier[-1].isoformat() if earlier else None)
            out['calendar_start_clamp'] = bool(p.publication_date < min(calendar))  # filings older than the research calendar become usable on its first date
    # 2b. the batch page lists every filing of the ticker: a second row for this period would be an amendment the dataset does not carry
    batch_request = PublicationRequest(ticker, 0, 0, publication_request_key(ticker, 0, 0))
    batch_html = RUN / 'publication_cache/mops_publication' / f"{hashlib.sha256(batch_request.request_key.encode('utf-8')).hexdigest()}.html"
    if batch_html.exists():
        try:
            rows = [p for p in parse_mops_publication_html(batch_html.read_bytes(), batch_request, calendar) if (p.fiscal_year, p.quarter) == (fy, q)]
            out['batch_rows_for_period'] = len(rows)
            if len(rows) > 1:
                out['problems'].append('REVISION_IN_BATCH_PAGE_NOT_IN_DATASET')
        except Exception as exc:
            out['blocked'].append(f'BATCH_PAGE_REPARSE_FAILED:{type(exc).__name__}')
    else:
        out['batch_rows_for_period'] = None
    # 3. values against the cached t163sb04 rows
    metrics = records[(records.ticker == ticker) & (records.fiscal_year == fy) & (records.quarter == q) & (records.revision_id == item.revision_id)]
    checked = {}
    for row in metrics.itertuples():
        if row.metric in RAW_METRICS:
            value, basis = raw.expected(ticker, fy, q, row.metric)
            if value is None:
                out['blocked'].append(f'RAW_VALUE_MISSING:{row.metric}')
                continue
            ok = close(value, row.value) and close(value, row.source_value if row.metric == 'eps' or q == 1 else value) and basis == row.REPORTING_BASIS
            if row.metric != 'eps' and q > 1 and basis == 'STANDALONE':  # source_value is the cumulative figure of the filing
                ok = ok and close(raw.cumulative(ticker, fy, q, row.metric), row.source_value)
        else:
            value = expected_derived(raw, ticker, fy, q, row.metric)
            if value is None:
                out['blocked'].append(f'DERIVED_PREREQUISITE_MISSING:{row.metric}')
                continue
            ok = close(value, row.value)
        checked[row.metric] = bool(ok)
        if not ok:
            out['problems'].append(f'VALUE_MISMATCH:{row.metric} expected={value} stored={row.value}')
    out.update(metrics_checked=len(checked), metrics_passed=sum(checked.values()), metrics_total=len(metrics))
    # 5. revision
    out['revisions_in_dataset'] = int(records[(records.ticker == ticker) & (records.fiscal_year == fy) & (records.quarter == q)].revision_id.nunique())
    out['status'] = 'FAIL' if out['problems'] else 'BLOCKED' if out['blocked'] else 'PASS'
    return out


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    target_csv, target_json = OUT / 'mops_sample_200.csv', OUT / 'mops_sample_summary.json'
    if target_csv.exists() or target_json.exists():
        raise SystemExit('closure sample outputs exist; evidence is never overwritten')
    records = pd.read_parquet(RECORDS)
    filings = records.drop_duplicates(['ticker', 'fiscal_year', 'quarter', 'revision_id']).reset_index(drop=True)
    pub = pd.read_csv(RUN / 'publication_records_full.csv', dtype={'ticker': str})
    stored_rules = {(r.ticker, int(r.fiscal_year), int(r.quarter)): r.availability_rule for r in pub.itertuples()}
    chosen = sample(filings)
    calendar, raw = load_research_trading_calendar(ROOT / 'data/processed/research_universe.parquet'), Raw()
    results = [check(item, records, raw, calendar, stored_rules) for item in chosen.itertuples()]
    frame = pd.DataFrame(results)
    frame['problems'], frame['blocked'] = frame.problems.map('; '.join), frame.blocked.map('; '.join)
    frame.to_csv(target_csv, index=False, encoding='utf-8-sig')
    markets = {t: ('sii' if (RUN / 'financial_cache' / f'mops_t163sb04_{y - 1911}_q{qq}_sii.parquet').exists() and
                   raw.cumulative(t, y, qq, 'eps') is not None and t in set(pd.read_parquet(RUN / 'financial_cache' / f'mops_t163sb04_{y - 1911}_q{qq}_sii.parquet').stock_id.astype(int).astype(str)) else 'otc')
               for t, y, qq in zip(frame.ticker, frame.fiscal_year, frame.quarter) if t.isdigit()}
    write(target_json, {
        'seed': SEED, 'sample_size': len(frame), 'population_filings': int(len(filings)), 'population_tickers': int(filings.ticker.nunique()),
        'design': f'{PER_STRATUM} filings per (fiscal_year, quarter) stratum, then uniformly random fillers; numpy default_rng(seed)',
        'coverage': {'fiscal_years': sorted(frame.fiscal_year.unique().tolist()), 'quarters': sorted(frame.quarter.unique().tolist()), 'tickers': int(frame.ticker.nunique()),
                     'markets': pd.Series(markets).value_counts().to_dict(),
                     'revision_ids_in_sample': int(frame.revision_id.nunique()), 'periods_with_more_than_one_revision_in_dataset': int((frame.revisions_in_dataset > 1).sum()),
                     'periods_with_more_than_one_filing_row_in_source_html': int((frame.html_rows_for_period.fillna(1) > 1).sum()),
                     'periods_with_more_than_one_row_in_batch_page': int((frame.batch_rows_for_period.fillna(1) > 1).sum()), 'batch_page_missing': int(frame.batch_rows_for_period.isna().sum()),
                     'filings_before_research_calendar_start': int(frame.calendar_start_clamp.astype('boolean').fillna(False).sum())},
        'status_counts': frame.status.value_counts().to_dict(),
        'problem_kinds': frame.problems[frame.problems != ''].str.split('; ').explode().str.split(r'[ :]').str[0].value_counts().to_dict(),
        'metrics_checked': int(frame.metrics_checked.sum()), 'metrics_passed': int(frame.metrics_passed.sum()),
        'source_hashes': {'financial_cache_files_read': raw.hashes, 'records_parquet': sha(RECORDS), 'publication_records_full_csv': sha(RUN / 'publication_records_full.csv')},
        'NOT_CERTIFIED': ['The sample does not certify the other filings: a population gate needs every filing or a statistically justified acceptance rule approved by the Owner.',
                          'Values come from one 2026-09-15 retrieval of t163sb04 (latest figures). The value as first published, and as restated later, is not recoverable from the cache.',
                          'Filings published before 2023-01-03 are all made available on 2023-01-03 by the research-calendar clamp; this is conservative, not a historical availability date.',
                          'The dataset keeps one filing per period; a source page with several rows for a period is reported as REVISION_IN_SOURCE_NOT_IN_DATASET.']})
    print(frame.status.value_counts().to_dict(), 'problems:', frame.problems[frame.problems != ''].head(5).tolist(), 'blocked:', frame.blocked[frame.blocked != ''].head(5).tolist())


def population_basis_scan():
    """Whole-dataset check of the defect the sample found: a yoy ratio whose current value and prior-year value are on different bases (standalone / cumulative)."""
    target = OUT / 'mops_population_basis_scan.json'
    if target.exists():
        raise SystemExit(f'{target} exists; evidence is never overwritten')
    r = pd.read_parquet(RECORDS)
    base = r[r.metric.isin(['revenue', 'operating_profit'])].drop_duplicates(['ticker', 'fiscal_year', 'quarter', 'metric'], keep='last').set_index(['ticker', 'fiscal_year', 'quarter', 'metric']).REPORTING_BASIS
    out = {}
    for derived, source in (('revenue_yoy', 'revenue'), ('operating_income_yoy', 'operating_profit'), ('revenue_cumulative_yoy', 'revenue'), ('operating_income_cumulative_yoy', 'operating_profit')):
        d = r[r.metric == derived][['ticker', 'fiscal_year', 'quarter']]
        d = d.assign(cur=[base.get((t, y, q, source)) for t, y, q in zip(d.ticker, d.fiscal_year, d.quarter)], prev=[base.get((t, y - 1, q, source)) for t, y, q in zip(d.ticker, d.fiscal_year, d.quarter)])
        bad = d[d.cur != d.prev]
        out[derived] = {'rows': int(len(d)), 'basis_differs_from_prior_year': int(len(bad)), 'share': round(len(bad) / len(d), 5), 'pairs_current_prior': {f'{a}/{b}': int(n) for (a, b), n in bad.groupby(['cur', 'prev']).size().items()},
                        'by_fiscal_year': {int(k): int(v) for k, v in bad.groupby('fiscal_year').size().items()}, 'by_quarter': {int(k): int(v) for k, v in bad.groupby('quarter').size().items()}}
    write(target, {'defect': 'add_derived_metrics divides the current value by the prior-year value of the same quarter without requiring the same REPORTING_BASIS; '
                             'when the prior year chain of quarters is incomplete the prior value stays cumulative while the current one is standalone',
                   'strategies_reading_these_metrics': 'G2 uses operating_income_yoy; revenue_yoy is read by Day3/D5 research inputs where listed; G3 uses eps_yoy (eps is always cumulative: not affected)',
                   'metrics': out, 'records_sha256': sha(RECORDS), 'not_modified': 'the dataset was not rewritten; the fix (require equal basis or recompute the prior standalone value) needs Owner approval and a dataset version bump'})
    print({k: (v['rows'], v['basis_differs_from_prior_year'], v['share']) for k, v in out.items()})


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    {'sample': run, 'scan': population_basis_scan}[sys.argv[1] if len(sys.argv) > 1 else 'sample']()
