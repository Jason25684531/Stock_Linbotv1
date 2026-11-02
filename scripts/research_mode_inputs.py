"""Offline development-window raw input conversion for Research Mode."""
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import pandas as pd

from core.backtest.execution import limit_prices
from core.research.adjustment.normalization import normalize_twse_closing_table
from core.research.sources import RawResponse
from core.research.sources.twse import ResponseKind, classify, find_closing_table
from scripts.p0_p4_research import split_event
from core.research.adjustment.normalization import parse_number


def build_raw_bars(paths, end='2025-12-31'):
    """Read only dated development files; the prior session supplies liquidity and reference close."""
    if date.fromisoformat(end) > date(2025, 12, 31):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    selected = {}
    for path in map(Path, paths):
        try:
            day = datetime.strptime(path.stem.removeprefix('MI_INDEX_'), '%Y%m%d').date()
        except ValueError:
            raise ValueError('INVALID_CLOSING_FILE_DATE') from None
        if day.isoformat() <= end:
            if day in selected:
                raise ValueError('DUPLICATE_CLOSING_SESSION')
            selected[day] = path
    bars, previous, source_hashes = {}, {}, {}
    for day, path in sorted(selected.items()):
        raw = path.read_bytes()
        payload = json.loads(raw.decode('utf-8'))
        stamp = datetime.combine(day, datetime.min.time())
        kind = classify(RawResponse('twse', 'MI_INDEX', {}, stamp, None, payload, None)).kind
        if kind == ResponseKind.NON_TRADING_DAY:
            continue
        if kind != ResponseKind.TRADING_DAY:
            raise ValueError(f'INVALID_CLOSING_SESSION:{day}')
        table = find_closing_table(payload)
        frame = normalize_twse_closing_table(table, day, stamp)
        daily = {}
        for row in frame.itertuples(index=False):
            values = (row.raw_open, row.raw_high, row.raw_low, row.raw_close, row.volume, row.amount)
            if not all(math.isfinite(float(value)) for value in values):
                continue
            if row.stock_id in daily:
                raise ValueError(f'DUPLICATE_STOCK:{day}:{row.stock_id}')
            prior = previous.get(row.stock_id)
            up, down = limit_prices(prior['close']) if prior and prior['close'] > 0 else (None, None)
            daily[row.stock_id] = dict(zip(('open', 'high', 'low', 'close', 'volume', 'amount'), map(float, values))) | {
                'prior_amount': prior['amount'] if prior else 0.0,
                'limit_up': float(up) if up is not None else None, 'limit_down': float(down) if down is not None else None,
                'limit_source': 'COMPUTED', 'close_available_at': day.isoformat() + 'T13:30:00+08:00'}
        bars[day.isoformat()] = daily
        source_hashes[day.isoformat()] = hashlib.sha256(raw).hexdigest()
        previous = daily
    return {'price_basis': 'RAW', 'source': 'twse_rwd', 'bars': bars, 'source_sha256_by_date': source_hashes}


def build_research_universe(market, calendar_evidence=None):
    """Use a conservative listing-age proxy and resolve execution at the next session event."""
    from core.research.repair_accounting import _timestamp
    bars, hashes = market['bars'], market['source_sha256_by_date']
    days = sorted(bars)
    if not days or days[-1] > '2025-12-31' or set(hashes) != set(days):
        raise ValueError('INVALID_DEVELOPMENT_CALENDAR')
    positions = {day: index for index, day in enumerate(days)}
    if calendar_evidence is not None:
        for index, day in enumerate(days[:-1]):
            item = calendar_evidence.get(day, {})
            if (item.get('execution_date') != days[index + 1]
                    or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('source_sha256', '')))
                    or _timestamp(item.get('available_at')) > _timestamp(day + 'T15:00:00+08:00')):
                raise ValueError(f'CALENDAR_NOT_AVAILABLE_AT_SIGNAL:{day}')
    records = [{'asof_date': day, 'asset_id': asset, 'close': row['close'], 'amount': row['amount'], 'volume': row['volume']}
               for day in days for asset, row in bars[day].items()]
    frame = pd.DataFrame(records).sort_values(['asset_id', 'asof_date'], kind='stable')
    if frame.duplicated(['asset_id', 'asof_date']).any():
        raise ValueError('DUPLICATE_UNIVERSE_ROW')
    frame['position'] = frame['asof_date'].map(positions)
    frame['observed_age'] = frame['position'] - frame.groupby('asset_id')['position'].transform('min') + 1
    frame['liquidity'] = frame.groupby('asset_id')['amount'].transform(lambda values: values.rolling(60, min_periods=60).mean())
    frame['member'] = (frame['observed_age'].ge(252) & frame['close'].ge(10) & frame['liquidity'].ge(20_000_000) & frame['volume'].gt(0))
    frame = frame.loc[frame['position'] < len(days) - 1].copy()
    frame['execution_date'] = frame['position'].map(lambda index: days[index + 1])
    frame['membership_available_at'] = frame['asof_date'] + 'T13:30:00+08:00'
    frame['calendar_available_at'] = frame['asof_date'].map(lambda day: calendar_evidence[day]['available_at']) if calendar_evidence is not None else None
    frame['membership_source_sha256'] = frame['asof_date'].map(hashes)
    frame['calendar_source_sha256'] = frame['asof_date'].map(lambda day: calendar_evidence[day]['source_sha256']) if calendar_evidence is not None else None
    return frame[['asof_date', 'asset_id', 'member', 'execution_date', 'membership_available_at',
                  'calendar_available_at', 'membership_source_sha256', 'calendar_source_sha256']]


def convert_events(rows, source_sha256, last_session):
    """Convert prefiltered development events; never infer unknown terms."""
    if not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        raise ValueError('INVALID_EVENT_SOURCE_SHA')
    end = date.fromisoformat(last_session)
    if end > date(2025, 12, 31):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    actions, unresolved, seen = [], [], set()
    for row in rows:
        day = date.fromisoformat(row['ex_date'])
        if day > end:
            raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
        asset = str(row['stock_id'])
        if not re.fullmatch(r'[1-9]\d{3}', asset) or (day, asset) in seen:
            raise ValueError('INVALID_OR_DUPLICATE_EVENT')
        seen.add((day, asset))
        try:
            values = [float(row[key]) for key in ('pre_close', 'ex_reference', 'dividend_reference')]
        except (KeyError, TypeError, ValueError):
            values = []
        if len(values) != 3 or not all(math.isfinite(v) and v > 0 for v in values):
            unresolved.append({'ex_date': day.isoformat(), 'stock_id': asset,
                               'missing_terms': ['valid_reference_prices'], 'source_sha256': source_sha256})
            continue
        split = split_event(row['kind'], *values)
        # Official total entitlement value (pre-close minus ex reference); it does not reveal the cash/stock split.
        value_per_share = round(values[0] - values[1], 6)
        if split['status'] != 'SPLIT_PROVEN':
            unresolved.append({'ex_date': day.isoformat(), 'stock_id': asset,
                               'missing_terms': ['cash_dividend', 'stock_dividend'] if split['cash'] is None else ['rights_terms'],
                               'source_sha256': source_sha256, 'value_per_share': value_per_share})
            continue
        cash, stock = split['cash'], split['stock_ratio']
        if cash < 0 or stock < 0 or not (cash or stock):
            unresolved.append({'ex_date': day.isoformat(), 'stock_id': asset,
                               'missing_terms': ['valid_dividend_amounts'], 'source_sha256': source_sha256})
            continue
        actions.append({'ex_date': day.isoformat(), 'stock_id': asset,
                        'type': 'cash_stock' if cash and stock else 'cash' if cash else 'stock',
                        'cash_per_share': cash, 'stock_ratio': stock,
                        'cash_pay_date': last_session if cash else None,
                        'stock_credit_date': last_session if stock else None,
                        'source': 'LOCAL_CACHE_UNCERTIFIED', 'source_sha256': source_sha256,
                        'value_per_share': value_per_share})
    return {'actions': actions, 'unresolved_events': unresolved}


def convert_reference_changes(rows, source_sha256, last_session):
    """Keep reference-price changes unresolved until their full share terms are known."""
    if not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        raise ValueError('INVALID_EVENT_SOURCE_SHA')
    end = date.fromisoformat(last_session)
    if end > date(2025, 12, 31):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    result, seen = [], set()
    for row in rows:
        day, asset, kind = date.fromisoformat(row['ex_date']), str(row['stock_id']), row['kind']
        if day > end:
            raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
        if not re.fullmatch(r'[1-9]\d{3}', asset) or kind not in ('CAPITAL_REDUCTION', 'PAR_VALUE_CHANGE') or (day, asset) in seen:
            raise ValueError('INVALID_OR_DUPLICATE_EVENT')
        seen.add((day, asset))
        result.append({'ex_date': day.isoformat(), 'stock_id': asset, 'missing_terms': ['share_count_change', 'settlement_terms'],
                       'kind': kind, 'source_sha256': source_sha256})
    return result


def convert_reference_exits(rows, source_sha256, last_session):
    """V5: a reference change becomes an exit at the official pre-suspension close; rows without prices stay unresolved."""
    if not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        raise ValueError('INVALID_EVENT_SOURCE_SHA')
    end = date.fromisoformat(last_session)
    if end > date(2025, 12, 31):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    actions, unresolved, seen = [], [], set()
    for row in rows:
        day, asset, kind = date.fromisoformat(row['ex_date']), str(row['stock_id']), row['kind']
        if day > end:
            raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
        if not re.fullmatch(r'[1-9]\d{3}', asset) or kind not in ('CAPITAL_REDUCTION', 'PAR_VALUE_CHANGE') or (day, asset) in seen:
            raise ValueError('INVALID_OR_DUPLICATE_EVENT')
        seen.add((day, asset))
        try:
            pre_close, reference = float(row['pre_close']), float(row['reference_price'])
        except (KeyError, TypeError, ValueError):
            pre_close = reference = float('nan')
        if not (math.isfinite(pre_close) and pre_close > 0 and math.isfinite(reference) and reference > 0):
            unresolved.append({'ex_date': day.isoformat(), 'stock_id': asset, 'missing_terms': ['share_count_change', 'settlement_terms'],
                               'kind': kind, 'source_sha256': source_sha256})
            continue
        actions.append({'ex_date': day.isoformat(), 'stock_id': asset, 'type': 'reference_change_exit', 'kind': kind,
                        'pre_close': pre_close, 'reference_price': reference, 'reason': str(row.get('reason') or kind),
                        'source': 'LOCAL_CACHE_UNCERTIFIED', 'source_sha256': source_sha256})
    return {'actions': actions, 'unresolved_events': unresolved}


def load_development_events(dividend_file, reference_files, last_session, reference_policy='STOP'):
    """Filter local event payloads by development date before using event fields."""
    if reference_policy not in ('STOP', 'FORCED_EXIT'):
        raise ValueError('INVALID_REFERENCE_POLICY')
    end = date.fromisoformat(last_session)
    if end > date(2025, 12, 31):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    raw = Path(dividend_file).read_bytes()
    payload = json.loads(raw.decode('utf-8'))
    if payload.get('stat') != 'OK' or not isinstance(payload.get('data'), list):
        raise ValueError('INVALID_DIVIDEND_CACHE')
    rows = []
    for row in payload['data']:
        try:
            match = re.fullmatch(r'(\d{2,3})年(\d{1,2})月(\d{1,2})日', row[0])
            year, month, day_of_month = map(int, match.groups())
            day = date(year + 1911, month, day_of_month)
        except (ValueError, IndexError, AttributeError, TypeError):
            raise ValueError('INVALID_EVENT_DATE') from None
        if day > end:
            continue
        if len(row) < 11 or not re.fullmatch(r'[1-9]\d{3}', str(row[1])):
            continue
        rows.append({'ex_date': day.isoformat(), 'stock_id': row[1], 'kind': row[6],
                     'pre_close': parse_number(row[3]), 'ex_reference': parse_number(row[4]),
                     'dividend_reference': parse_number(row[10])})
    converted = convert_events(rows, hashlib.sha256(raw).hexdigest(), last_session)
    for kind, path in reference_files.items():
        raw = Path(path).read_bytes()
        payload = json.loads(raw.decode('utf-8'))
        if payload.get('stat') != 'OK' or not isinstance(payload.get('data'), list):
            raise ValueError('INVALID_REFERENCE_CACHE')
        refs = []
        for row in payload['data']:
            try:
                year, month, day = map(int, row[0].split('/'))
                event_day = date(year + 1911, month, day)
            except (TypeError, ValueError, IndexError):
                raise ValueError('INVALID_EVENT_DATE') from None
            if event_day > end:
                continue
            if len(row) < 2 or not re.fullmatch(r'[1-9]\d{3}', str(row[1])):
                continue
            refs.append({'ex_date': event_day.isoformat(), 'stock_id': row[1], 'kind': kind,
                         'pre_close': parse_number(row[3]) if len(row) > 4 else None, 'reference_price': parse_number(row[4]) if len(row) > 4 else None,
                         'reason': row[9] if kind == 'CAPITAL_REDUCTION' and len(row) > 9 else kind})
        digest = hashlib.sha256(raw).hexdigest()
        if reference_policy == 'FORCED_EXIT':
            exits = convert_reference_exits(refs, digest, last_session)
            converted['actions'].extend(exits['actions'])
            converted['unresolved_events'].extend(exits['unresolved_events'])
        else:
            converted['unresolved_events'].extend(convert_reference_changes(refs, digest, last_session))
    return converted


def compute_day3_scores(market, pool_path, master_path, composite_id, signal_days, source_sha256):
    """Recompute archived Day3 factor scores from raw bars, without adjusted-price results."""
    from core.research import strategy_search as search
    if not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        raise ValueError('INVALID_PRICE_SOURCE_SHA')
    bars = market['bars']
    if market.get('price_basis') != 'RAW' or not bars or max(bars) > '2025-12-31':
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    rows = [{'trade_date': day, 'stock_id': stock, **{field: values[field] for field in ('open', 'high', 'low', 'close', 'volume', 'amount')}}
            for day, daily in bars.items() for stock, values in daily.items()]
    quotes = pd.DataFrame(rows)
    quotes['trade_date'] = pd.to_datetime(quotes['trade_date'])
    frames = {field: quotes.pivot(index='trade_date', columns='stock_id', values=field).sort_index()
              for field in ('open', 'high', 'low', 'close', 'volume', 'amount')}
    columns = ['candidate_id', 'research_direction', 'mean_ic', 'icir', 'factor_score']
    pool = pd.read_csv(pool_path, usecols=columns)
    directions = search.resolve_effective_directions(pool)
    _, weights = search.search_grid(pool)
    if composite_id not in weights:
        raise ValueError('UNKNOWN_ARCHIVED_COMPOSITE')
    panels = search.compute_factor_panels(frames, master_path)
    ranks = search.build_ranks_long(panels, directions)
    ranks = ranks.loc[ranks['asof_date'].isin(pd.to_datetime(signal_days))]
    scores = search.build_all_composite_scores(ranks, {composite_id: weights[composite_id]})[composite_id]
    scores = scores[['asof_date', 'asset_id', 'composite_score']].copy()
    scores['asof_date'] = pd.to_datetime(scores['asof_date']).dt.strftime('%Y-%m-%d')
    scores['signal_at'] = scores['asof_date'] + 'T15:00:00+08:00'
    scores['score_available_at'] = scores['asof_date'] + 'T13:30:00+08:00'
    scores['score_source_sha256'] = source_sha256
    return scores.sort_values(['asof_date', 'asset_id'], kind='stable').reset_index(drop=True)
