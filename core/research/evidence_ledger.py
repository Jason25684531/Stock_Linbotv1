"""Hash-chained, provenance-labelled research evidence; unknown is never zero."""
import hashlib
import json
import os
from pathlib import Path

from core.research.sources.twse import _exclusive

FIELDS = ('stock_id', 'event_date', 'field', 'value_or_UNKNOWN', 'source', 'sha256',
          'fetched_at', 'grant_scope', 'tier')
GENESIS = '0' * 64


def _hash(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def read(path):
    path = Path(path)
    previous, rows = GENESIS, []
    if not path.exists():
        return rows
    with path.open(encoding='utf-8') as stream:
        for number, line in enumerate(stream, 1):
            try:
                row = json.loads(line)
                digest = row.pop('hash')
                if row['previous_hash'] != previous or digest != _hash(row) or any(k not in row for k in FIELDS):
                    raise ValueError('invalid chain')
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f'INVALID_EVIDENCE_CHAIN at line {number}') from exc
            row['hash'] = digest
            rows.append(row)
            previous = digest
    return rows


def append(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive(path.with_suffix(path.suffix + '.lock')):
        rows = read(path)
        previous = rows[-1]['hash'] if rows else GENESIS
        encoded = []
        for record in records:
            if any(k not in record for k in FIELDS):
                raise ValueError('missing evidence field')
            row = {k: record[k] for k in FIELDS}
            row['previous_hash'] = previous
            previous = row['hash'] = _hash(row)
            encoded.append(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n')
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.writelines(encoded)
            stream.flush()
            os.fsync(stream.fileno())


def summarize(path, stocks):
    rows = read(path)  # Never summarize a broken chain.
    event_stocks = {r['stock_id'] for r in rows if r['event_date'] and r['field'] == 'ex_date'
                    and r['value_or_UNKNOWN'] not in (None, '', 'UNKNOWN')}
    return {'status': 'DATA_BLOCKED', 'coverage': None, 'reason': 'event denominator unknown',
            'stocks': len(stocks), 'unknown_events': sorted(set(stocks) - event_stocks),
            'records': len(rows)}
