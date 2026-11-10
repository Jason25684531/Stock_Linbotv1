"""Corporate action events for research valuation checks (change 2026-11-09-autonomous-data-readiness-v1, design D2).

Source: outputs/research_mode/prepared_v6/actions.json only. Its input manifest declares development_end 2025-12-31 and pins the file
hash; the raw TWT49U caches that run into 2026 are never read here (no read-then-filter of sealed data). The source is labelled
LOCAL_CACHE_UNCERTIFIED, so the coverage is PARTIALLY_VERIFIED and no verdict built on it is a certification.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.backtest.execution import stale_valuation_verdict

ROOT = Path(__file__).resolve().parents[2]
PREPARED = ROOT / 'outputs/research_mode/prepared_v6'
DEV_END = '2025-12-31'
COVERAGE_STATUS = 'PARTIALLY_VERIFIED'
COLUMNS = ('ticker', 'event_type', 'announcement_date', 'ex_date', 'effective_date', 'source', 'source_version', 'verification_status',
           'null_reason')
EVENT_TYPES = {'cash': 'CASH_DIVIDEND', 'stock': 'STOCK_DIVIDEND', 'reference_change_exit': 'REFERENCE_PRICE_CHANGE'}
SOURCE = 'prepared_v6/actions.json (TWT49U, TWTAUU, TWTB8U local caches; LOCAL_CACHE_UNCERTIFIED)'
# ponytail: the prepared source has no announcement date, and cash_pay_date is a fixed placeholder (2025-12-31) there, so neither is used.
NULL_REASON = 'announcement_date:NOT_IN_SOURCE;effective_date:PAYMENT_DATE_PLACEHOLDER_IN_SOURCE'


def _row(event, status):
    missing = event.get('missing_terms') or []
    kind = 'DELISTING' if 'delisting_settlement_terms' in missing else EVENT_TYPES.get(event.get('type'), 'UNRESOLVED_EVENT')
    return {'ticker': str(event['stock_id']), 'event_type': kind, 'announcement_date': None, 'ex_date': event['ex_date'],
            'effective_date': None, 'source': SOURCE, 'source_version': event.get('source_sha256'), 'verification_status': status,
            'null_reason': NULL_REASON + (';missing_terms:' + ','.join(missing) if missing else '')}


def load(prepared=PREPARED) -> dict:
    """{'events': [rows], 'tickers': covered listed tickers, 'source_sha256', 'status'}; refuses any source that reaches past 2025-12-31."""
    prepared = Path(prepared)
    manifest = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
    raw = (prepared / 'actions.json').read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if manifest.get('development_end') != DEV_END:
        raise ValueError(f"SOURCE_NOT_DEVELOPMENT_WINDOW: development_end={manifest.get('development_end')}")
    if manifest.get('files', {}).get('actions.json') != digest:
        raise ValueError('SOURCE_HASH_MISMATCH: actions.json differs from its input manifest')
    data = json.loads(raw)
    events = [_row(e, 'EXCHANGE_REPORT_LOCAL_CACHE') for e in data['actions']] + [_row(e, 'UNRESOLVED_TERMS') for e in data['unresolved_events']]
    if late := sorted({e['ex_date'] for e in events if e['ex_date'] > DEV_END}):
        raise ValueError(f'SEALED_WINDOW_IN_SOURCE: {late[:3]}')
    # ponytail: coverage is a per-ticker list of covered session dates; its first and last date bound the window the source can speak for
    coverage = {t: (days[0], days[-1]) for t, days in data.get('coverage', {}).items() if days}
    return {'events': events, 'coverage': coverage, 'source_sha256': digest, 'status': COVERAGE_STATUS}


def action_dates(table) -> dict:
    """{stock_id: sorted ex-dates}; unresolved events count too (an event happened even when its terms are unknown)."""
    dates = {}
    for event in table['events']:
        dates.setdefault(event['ticker'], set()).add(event['ex_date'])
    return {sid: sorted(days) for sid, days in dates.items()}


def match(event, dates, coverage) -> str:
    if any(event['price_source_date'] < day <= event['date'] for day in dates.get(event['stock_id'], ())):
        return 'MATCHED_ACTION'
    first, last = coverage.get(event['stock_id'], ('9999', '0000'))
    return 'NO_EVENT_IN_SOURCE' if first <= event['price_source_date'] and event['date'] <= last else 'OUTSIDE_SOURCE_COVERAGE'


def verdict(events, table=None) -> dict:
    """The shared stale-valuation verdict with known action dates; never STALE_LAST_OFFICIAL_CLOSE while coverage is partial.
    Fail-closed like execution.stale_valuation_verdict: a stale window the source does not cover stays VALUATION_BLOCKED_ACTIONS_UNKNOWN."""
    if table is None:
        return stale_valuation_verdict(events, None)
    dates = action_dates(table)
    result = stale_valuation_verdict(events, dates)
    if result['status'] == 'STALE_LAST_OFFICIAL_CLOSE':
        result['status'] = 'STALE_ACTIONS_PARTIALLY_VERIFIED'
    result['coverage'] = table['status']
    result['event_match'] = [{'date': e['date'], 'stock_id': e['stock_id'], 'match': match(e, dates, table['coverage'])} for e in events or []]
    unknown = sorted({m['stock_id'] for m in result['event_match'] if m['match'] == 'OUTSIDE_SOURCE_COVERAGE'})
    if unknown and result['status'] != 'VALUATION_BLOCKED_CORPORATE_ACTION':
        result.update(status='VALUATION_BLOCKED_ACTIONS_UNKNOWN', blocked=sorted(set(result['blocked']) | set(unknown)))
    return result
