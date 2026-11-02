"""Import observed, uncertified action gaps without asserting event completeness."""
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.evidence_ledger import append, summarize

SOURCE = ROOT / 'outputs/research_repair/phase2_20261030'
DEST = ROOT / 'outputs/research_repair/autonomous_evidence_20261030'


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    ledger = DEST / 'evidence.jsonl'
    if ledger.exists():
        raise FileExistsError('evidence already imported; preserve the original ledger')
    with (SOURCE / 'observed_event_gaps.csv').open(encoding='utf-8-sig', newline='') as stream:
        events = list(csv.DictReader(stream))
    fields = ('ex_date', 'cash_dividend', 'stock_dividend', 'cash_pay_date', 'stock_credit_date', 'publication_time')
    records = []
    for event in events:
        for field in fields:
            records.append({'stock_id': event['stock_id'], 'event_date': event['ex_date'], 'field': field,
                            'value_or_UNKNOWN': event[field] if field == 'ex_date' else 'UNKNOWN',
                            'source': event['source'], 'sha256': event['source_sha256'],
                            'fetched_at': event['retrieved_at'], 'grant_scope': 'historical local research cache',
                            'tier': 'LOCAL_CACHE_UNCERTIFIED'})
    append(ledger, records)
    with (SOURCE / 'a191_stock_coverage.csv').open(encoding='utf-8-sig', newline='') as stream:
        stocks = [row['stock_id'] for row in csv.DictReader(stream)]
    result = summarize(ledger, stocks)
    result['observed_events'] = len(events)
    (DEST / 'evidence_summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
