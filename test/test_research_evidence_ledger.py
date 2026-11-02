import json

import pytest

from core.research.evidence_ledger import append, read, summarize


def test_unknown_and_tamper(tmp_path):
    path = tmp_path / 'evidence.jsonl'
    record = dict(stock_id='1303', event_date='2024-01-01', field='ex_date', value_or_UNKNOWN='2024-01-01',
                  source='cache', sha256='abc', fetched_at='2024-01-02', grant_scope='research', tier='LOCAL_CACHE_UNCERTIFIED')
    append(path, [record])
    append(path, [dict(record, field='cash_dividend', value_or_UNKNOWN='UNKNOWN')])
    assert len(read(path)) == 2
    summary = summarize(path, ['1303', '9999'])
    assert summary['coverage'] is None and summary['unknown_events'] == ['9999']
    lines = path.read_text(encoding='utf-8').splitlines()
    row = json.loads(lines[0]); row['value_or_UNKNOWN'] = 0
    path.write_text(json.dumps(row) + '\n' + lines[1] + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='line 1'):
        summarize(path, ['1303'])
