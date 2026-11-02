"""Inventory local development-window evidence without opening any 2026 market file."""
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.evidence_ledger import append, read, summarize
from core.research.sources.twse import find_closing_table, SchemaDriftError

ORIGINAL = ROOT / 'outputs/research_repair/autonomous_evidence_20261030/evidence.jsonl'
STOCKS = ROOT / 'outputs/research_repair/phase2_20261030/a191_stock_coverage.csv'
RAW = ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd'
DEST = ROOT / 'outputs/research_repair/autonomous_evidence_inventory_20261030'
UNKNOWN = 'UNKNOWN'


def market_records(path, stocks):
    """Source date is checked before opening, so OOS filenames cannot enter this reader."""
    match = re.fullmatch(r'MI_INDEX_(\d{8})\.json', path.name)
    if not match or not '20230213' <= match[1] <= '20251231':
        raise ValueError('OUTSIDE_DEVELOPMENT_WINDOW')
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw.decode('utf-8'))
    day = f'{match[1][:4]}-{match[1][4:6]}-{match[1][6:]}'
    values = {}
    try:
        if payload.get('stat') != 'OK' or str(payload.get('date', '')).replace('-', '').replace('/', '') != match[1]:
            raise ValueError('SOURCE_DATE_OR_STATUS_INVALID')
        table = find_closing_table(payload)
        indices = [table['fields'].index(x) for x in ('證券代號', '收盤價', '成交股數')]
        for row in table['data']:
            if len(row) != len(table['fields']):
                raise ValueError('SOURCE_ROW_WIDTH_INVALID')
            ticker = str(row[indices[0]]).strip()
            if ticker in stocks:
                if ticker in values:
                    raise ValueError('DUPLICATE_STOCK')
                values[ticker] = (row[indices[1]], row[indices[2]])
    except (ValueError, KeyError, TypeError, SchemaDriftError):
        values = {}  # A malformed response is not evidence of absent securities.
    result = []
    for stock in stocks:
        for field, value in zip(('close_price', 'volume'), values.get(stock, (UNKNOWN, UNKNOWN))):
            try:
                number = float(str(value).replace(',', ''))
                value = number if math.isfinite(number) and (number > 0 if field == 'close_price' else number >= 0) else UNKNOWN
            except (ValueError, TypeError):
                value = UNKNOWN
            result.append({'stock_id': stock, 'event_date': day, 'field': field, 'value_or_UNKNOWN': value,
                           'source': str(path.relative_to(ROOT)), 'sha256': digest, 'fetched_at': None,
                           'grant_scope': 'local historical research cache; retrieval time unknown', 'tier': 'LOCAL_CACHE_UNCERTIFIED'})
    return result


def main():
    previous = read(ORIGINAL)
    with STOCKS.open(encoding='utf-8-sig', newline='') as stream:
        stocks = sorted(row['stock_id'] for row in csv.DictReader(stream))
    if len(stocks) != 144 or len(set(stocks)) != 144:
        raise ValueError('EXPECTED_144_UNIQUE_STOCKS')
    DEST.mkdir(parents=True, exist_ok=True)
    ledger = DEST / 'evidence.jsonl'
    if ledger.exists():
        raise FileExistsError('preserve existing inventory; use a new output directory')
    records = [{key: row[key] for key in ('stock_id', 'event_date', 'field', 'value_or_UNKNOWN', 'source', 'sha256',
                                            'fetched_at', 'grant_scope', 'tier')} for row in previous]
    for row in previous:
        if row['field'] == 'ex_date':
            for field in ('rights_terms', 'revision'):
                records.append({**{key: row[key] for key in ('stock_id', 'event_date', 'source', 'sha256', 'fetched_at', 'grant_scope', 'tier')},
                                'field': field, 'value_or_UNKNOWN': UNKNOWN})
    for stock in stocks:
        for field in ('financial_publication_time', 'financial_available_time', 'revenue_publication_time',
                      'revenue_available_time', 'suspension', 'delisting'):
            records.append({'stock_id': stock, 'event_date': '', 'field': field, 'value_or_UNKNOWN': UNKNOWN,
                            'source': 'no certified complete historical record identified', 'sha256': None, 'fetched_at': None,
                            'grant_scope': 'UNKNOWN', 'tier': 'UNKNOWN'})
    files = sorted(path for path in RAW.glob('MI_INDEX_*.json') if re.fullmatch(r'MI_INDEX_\d{8}\.json', path.name)
                   and '20230213' <= path.stem[-8:] <= '20251231')
    if not files:
        raise ValueError('NO_DEVELOPMENT_MARKET_CACHE')
    for index, path in enumerate(files, 1):
        records.extend(market_records(path, stocks))
        if index % 100 == 0:
            print(f'processed {index}/{len(files)} development cache files', flush=True)
    append(ledger, records)
    summary = summarize(ledger, stocks)
    summary.update({'market_cache_files': len(files), 'date_scope': '2023-02-13..2025-12-31',
                    'certification': 'DATA_BLOCKED', 'retrieval_times': 'UNKNOWN',
                    'field_coverage': 'observed local cache only; event denominator and completeness unknown'})
    (DEST / 'evidence_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
