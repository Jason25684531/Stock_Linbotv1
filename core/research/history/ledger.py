"""Corporate-action ledger for the position-ledger accounting model POSITION_LEDGER_CA_V1 (change 2026-11-10-engine-accounting-and-history-gaps-v1).

The engine trades and marks RAW TWSE prices; every official event is booked on the position instead (shares, cash, per-share basis), so a
price gap on an ex-date is never a gain or a loss. One row per official event. The identity is the event's official content (stock, date,
event type or reduction reason, pre-close, reference price), never the file it was read from: the same event read from two overlapping or
re-published files collapses to one row (every file kept as provenance), and two different terms for the same stock, date and kind are a
conflict that stops the build (fail-closed: never book both, never pick one silently).

TWSE: reference = (pre_close - cash) / (1 + stock_ratio), quoted rounded to the tick. Dividend terms, in order:
  any dividend                  FinLab announcement published by the ex-date, of the same type, reproducing the quoted reference within a tick
                                -> exact announced cash and ratio                                RECONCILED_FINLAB
  cash dividend (息)            cash = pre_close - reference, multiplier 1                       REFERENCE_DERIVED (error <= half a tick)
  stock dividend (權)           multiplier = pre_close / reference, no cash                      REFERENCE_DERIVED (error <= half a tick)
  cash and stock (權息)         multiplier = pre_close / reference, no cash                      ASSUMED_STOCK (flagged: cash part unknown)
Capital changes (derived from the quoted reference price, no announcement source):
  capital reduction 退還股款    multiplier = (pre_close - PAR) / (reference - PAR), cash PAR per cancelled share   REFERENCE_DERIVED
  capital reduction 彌補虧損    multiplier = pre_close / reference, no cash                      REFERENCE_DERIVED
  par value change (TWTB8U)     multiplier = pre_close / reference, no cash                      REFERENCE_DERIVED
Anything else, or a non-positive price, is UNRESOLVED: recorded, never booked.
"""
from __future__ import annotations

import hashlib
import json
import math

import pandas as pd

PAR = 10.0  # NT$ par value: cash returned per cancelled share and paid for fractional shares
RATIO_DECIMALS = 5  # per-thousand-share terms with two decimals
COLUMNS = ['event_id', 'stock_id', 'date', 'order', 'kind', 'cash_per_share', 'share_multiplier', 'price_factor', 'terms_status', 'terms_source',
           'pre_close', 'reference', 'source_file', 'source_version', 'sources']
KINDS = ('CASH_DIVIDEND', 'STOCK_DIVIDEND', 'CASH_AND_STOCK_DIVIDEND', 'CAPITAL_REDUCTION_CASH', 'CAPITAL_REDUCTION_LOSS', 'PAR_VALUE_CHANGE')
STATUSES = ('REFERENCE_DERIVED', 'RECONCILED_FINLAB', 'ASSUMED_STOCK', 'UNRESOLVED')
BOOKED = ('REFERENCE_DERIVED', 'RECONCILED_FINLAB', 'ASSUMED_STOCK')
CASH_RETURN, LOSS_OFFSET = '退還股款', '彌補虧損'  # 退還股款, 彌補虧損
CASH_MARK, STOCK_MARK = '息', '權'  # 息, 權
ANNOUNCEMENT_COLUMNS = {'ex_rights': '除權交易日', 'ex_dividend': '除息交易日',
                        'stock_earnings': '盈餘轉增資配股(元/股)',
                        'stock_reserve': '法定盈餘公積、資本公積轉增資配股(元/股)',
                        'cash_earnings': '盈餘分配之股東現金股利(元/股)',
                        'cash_reserve': '法定盈餘公積、資本公積發放之現金(元/股)',
                        'announced': '公告日期'}


def _tick(price: float) -> float:
    for limit, tick in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0)):
        if price < limit:
            return tick
    return 5.0


def event_id(key: dict) -> str:
    """SHA256 of the official event content (prices rounded to 4 decimals, so '119.50' and 119.5 are the same event)."""
    key = {k: (round(float(v), 4) if k in ('pre_close', 'reference') and v is not None else v) for k, v in key.items()}
    body = json.dumps(key, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _positive(*values) -> bool:
    return all(v is not None and not (isinstance(v, float) and math.isnan(v)) and float(v) > 0 for v in values)


def announcement_terms(announcements: pd.DataFrame | None) -> dict:
    """{(stock_id, date): [(announced, cash_per_share, stock_ratio), ...]} from the FinLab dividend announcement file, oldest first."""
    if announcements is None or announcements.empty:
        return {}
    c = ANNOUNCEMENT_COLUMNS
    frame = announcements.copy()
    frame['stock_id'] = frame['stock_id'].astype(str).str.strip()
    frame = frame.sort_values(c['announced'])
    out = {}
    for record in frame.to_dict('records'):  # column names are not identifiers: never itertuples here
        cash = float(pd.to_numeric(record.get(c['cash_earnings']), errors='coerce') or 0) + float(pd.to_numeric(record.get(c['cash_reserve']), errors='coerce') or 0)
        stock = float(pd.to_numeric(record.get(c['stock_earnings']), errors='coerce') or 0) + float(pd.to_numeric(record.get(c['stock_reserve']), errors='coerce') or 0)
        announced = pd.to_datetime(record.get(c['announced']), errors='coerce')
        for column in (c['ex_rights'], c['ex_dividend']):
            day = pd.to_datetime(record.get(column), errors='coerce')
            if pd.notna(day):
                out.setdefault((record['stock_id'], pd.Timestamp(day).normalize()), []).append((announced, cash, stock / PAR))
    return out


def _known_terms(terms: dict, stock_id: str, day: pd.Timestamp, cash: bool, stock: bool, p0: float, ref: float):
    """The latest announcement that was public by the ex-date, has this event's type (cash and/or stock) and reproduces the quoted reference
    price within a tick. Eligibility is checked before choosing, so a later announcement of another type never hides a matching one."""
    matching = [(c, r) for announced, c, r in terms.get((stock_id, day), [])
                if pd.notna(announced) and announced <= day and (c > 0) == cash and (r > 0) == stock
                and abs((p0 - c) / (1 + r) - ref) <= _tick(ref) + 1e-6]
    return matching[-1] if matching else None


def _dividend(row, versions: dict, terms: dict) -> dict:
    p0, ref = float(row.pre_ex_close), float(row.ex_reference_price)
    kind_text = str(row.action_type)
    cash, stock = CASH_MARK in kind_text, STOCK_MARK in kind_text
    out = {'stock_id': str(row.stock_id), 'date': pd.Timestamp(row.ex_date).normalize(), 'order': 0, 'pre_close': p0, 'reference': ref,
           'source_file': row.source_file, 'source_version': versions.get(str(row.source_file).split(';')[0], ''), 'terms_source': 'TWSE_TWT49U_REFERENCE_PRICE',
           'kind': 'CASH_AND_STOCK_DIVIDEND' if cash and stock else 'STOCK_DIVIDEND' if stock else 'CASH_DIVIDEND'}
    out['event_id'] = event_id({'source': 'TWT49U', 'stock_id': out['stock_id'], 'date': str(out['date'].date()), 'type': kind_text,
                                'pre_close': row.pre_ex_close, 'reference': row.ex_reference_price})
    if not _positive(p0, ref) or not (cash or stock):
        return out | {'cash_per_share': 0.0, 'share_multiplier': 1.0, 'price_factor': 1.0, 'terms_status': 'UNRESOLVED'}
    out['price_factor'] = ref / p0
    # the exact announced terms win when they were public by the ex-date, match the event type and reproduce the quoted reference price
    announced = _known_terms(terms, out['stock_id'], out['date'], cash, stock, p0, ref)
    if announced is not None:
        cash_ps, ratio = announced
        return out | {'cash_per_share': cash_ps, 'share_multiplier': round(1 + ratio, 8), 'terms_status': 'RECONCILED_FINLAB',
                      'terms_source': 'FINLAB_DIVIDEND_ANNOUNCEMENT_VERIFIED_AGAINST_TWT49U'}
    # otherwise the terms are derived from the quoted reference price, which is rounded to the tick: error at most half a tick per share
    if cash and not stock:
        return out | {'cash_per_share': round(p0 - ref, 6), 'share_multiplier': 1.0, 'terms_status': 'REFERENCE_DERIVED'}
    if stock and not cash:
        return out | {'cash_per_share': 0.0, 'share_multiplier': round(p0 / ref, RATIO_DECIMALS), 'terms_status': 'REFERENCE_DERIVED'}
    return out | {'cash_per_share': 0.0, 'share_multiplier': round(p0 / ref, RATIO_DECIMALS), 'terms_status': 'ASSUMED_STOCK',
                  'terms_source': 'TWSE_TWT49U_REFERENCE_PRICE_CASH_PART_UNKNOWN'}


def _capital(row, versions: dict) -> dict:
    p0, ref = float(row.pre_close), float(row.reference)
    reason = str(getattr(row, 'reason', '') or '')
    out = {'stock_id': str(row.stock_id), 'date': pd.Timestamp(row.date).normalize(), 'order': 1, 'pre_close': p0, 'reference': ref,
           'source_file': row.source_file, 'source_version': versions.get(row.source_file, ''), 'terms_source': 'TWSE_TWTAUU_TWTB8U_REFERENCE_PRICE'}
    out['event_id'] = event_id({'source': row.kind, 'stock_id': out['stock_id'], 'date': str(out['date'].date()), 'reason': reason,
                                'pre_close': row.pre_close, 'reference': row.reference})
    unresolved = {'cash_per_share': 0.0, 'share_multiplier': 1.0, 'price_factor': 1.0, 'terms_status': 'UNRESOLVED'}
    if not _positive(p0, ref):
        return out | {'kind': 'PAR_VALUE_CHANGE' if row.kind == 'PAR_VALUE_CHANGE' else 'CAPITAL_REDUCTION_LOSS'} | unresolved
    out['price_factor'] = ref / p0
    if row.kind == 'PAR_VALUE_CHANGE':
        return out | {'kind': 'PAR_VALUE_CHANGE', 'cash_per_share': 0.0, 'share_multiplier': round(p0 / ref, RATIO_DECIMALS), 'terms_status': 'REFERENCE_DERIVED'}
    if reason == LOSS_OFFSET:
        return out | {'kind': 'CAPITAL_REDUCTION_LOSS', 'cash_per_share': 0.0, 'share_multiplier': round(p0 / ref, RATIO_DECIMALS), 'terms_status': 'REFERENCE_DERIVED'}
    if reason == CASH_RETURN:
        out['kind'] = 'CAPITAL_REDUCTION_CASH'
        if abs(ref - PAR) < 1e-9:
            return out | unresolved | {'price_factor': ref / p0}
        multiplier = round((p0 - PAR) / (ref - PAR), RATIO_DECIMALS)
        if not 0 <= multiplier <= 1:  # 0: every share cancelled for cash (the engine closes the position as a cash-out)
            return out | unresolved | {'price_factor': ref / p0}
        return out | {'cash_per_share': round(PAR * (1 - multiplier), 6), 'share_multiplier': multiplier, 'terms_status': 'REFERENCE_DERIVED'}
    return out | {'kind': 'CAPITAL_REDUCTION_LOSS'} | unresolved | {'price_factor': ref / p0, 'terms_source': f'UNKNOWN_REASON:{reason}'}


def build(events: pd.DataFrame, capital: pd.DataFrame, versions: dict, announcements: pd.DataFrame | None = None) -> pd.DataFrame:
    """One ledger row per official event, sorted by (date, stock, order: dividends before capital changes).

    The same event read from several files is one row (`sources` lists every file:version). Two events with the same stock, date and kind
    but different official content raise CONFLICTING_EVENT_TERMS."""
    terms = announcement_terms(announcements)
    rows = [_dividend(r, versions, terms) for r in events.itertuples(index=False)] + [_capital(r, versions) for r in capital.itertuples(index=False)]
    for row in rows:  # a snapshot row may already carry several files (';'-joined) for one identical official row
        row['sources'] = ';'.join(f"{name}:{versions.get(name, '')}" for name in str(row['source_file']).split(';'))
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if frame.empty:
        return frame
    provenance = frame.groupby('event_id').sources.agg(lambda s: ';'.join(sorted({x for v in s for x in v.split(';')})))
    frame = frame.drop_duplicates('event_id').assign(sources=lambda f: f.event_id.map(provenance))
    clash = frame[frame.duplicated(['stock_id', 'date', 'kind'], keep=False)]
    if len(clash):
        raise ValueError('CONFLICTING_EVENT_TERMS: ' + ','.join(f'{r.stock_id}:{r.date.date()}:{r.kind}' for r in clash.head(3).itertuples()))
    return frame.sort_values(['date', 'stock_id', 'order']).reset_index(drop=True)


def from_snapshot(snap) -> pd.DataFrame:
    versions = {name: entry['sha256'] for group in ('twt49u', 'capital') for name, entry in snap.manifest['sources'].get(group, {}).items()}
    path = snap.source('reconcile', 'finlab_dividend_announcement')
    announcements = pd.read_parquet(path) if path is not None else None
    return build(snap.frame('events'), snap.frame('capital_changes'), versions, announcements)


def version(ledger: pd.DataFrame) -> str:
    """Content hash of the ledger (what the engine booked), part of every run's identity."""
    body = ledger.astype(str).to_csv(index=False).encode('utf-8')
    return hashlib.sha256(body).hexdigest()


def to_engine(ledger: pd.DataFrame) -> dict[str, list[dict]]:
    """{'YYYY-MM-DD': [rows in booking order]} for BacktestEngine(corporate_actions=...)."""
    out: dict[str, list[dict]] = {}
    for record in ledger.sort_values(['date', 'stock_id', 'order']).to_dict('records'):
        record['date'] = pd.Timestamp(record['date']).strftime('%Y-%m-%d')
        out.setdefault(record['date'], []).append(record)
    return out


def summary(ledger: pd.DataFrame) -> dict:
    by_year = ledger.assign(year=pd.to_datetime(ledger.date).dt.year).groupby(['year', 'terms_status']).size()
    return {'rows': len(ledger), 'by_kind': ledger.kind.value_counts().to_dict(), 'by_status': ledger.terms_status.value_counts().to_dict(),
            'by_year_status': {f'{y}:{s}': int(n) for (y, s), n in by_year.items()}}
