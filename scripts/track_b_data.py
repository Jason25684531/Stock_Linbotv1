"""Official panel for Track B: bars, benchmark indices, corporate actions, delistings, and the certification of the D3 adjusted prices.

D3 prices are official-source and back-adjusted with the official ex-rights reference price (event factor = reference / previous close),
so strategy returns on them are adjusted-price (dividend- and rights-adjusted) returns and must be compared with a total-return index.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import digest, read, write
from scripts.track_b_validation import OUT

D3_ROOT = ROOT / 'artifacts/factors/d3_full_20230103_20260728'
INDEX_DIRS = (D3_ROOT / '_raw/twse_rwd', ROOT / 'artifacts/official_data_recovery_cache/mi_index')
EVENT_FILES = {'D3': D3_ROOT / '_raw/twse_rwd/TWT49U_20230103_20260728.json', '2022': ROOT / 'artifacts/official_dual_track_cache/TWT49U_20220103_20221230.json',
               'AFTER_D3': ROOT / 'artifacts/official_dual_track_cache/TWT49U_20260729_20261023.json'}
DELISTING = ROOT / 'artifacts/official_dual_track_cache/suspendListing.json'
PRICE_INDEX, TOTAL_RETURN_INDEX = '發行量加權股價指數', '發行量加權股價報酬指數'
PRICE_TOLERANCE = 1e-6  # relative; D3 computes the factor product in its own floating-point order


def parse_index(payload, name):
    for table in payload.get('tables', []):
        for row in table.get('data', []):
            if row and row[0] == name:
                return float(str(row[1]).replace(',', ''))
    return np.nan


def build_panel():
    """All official MI_INDEX sessions: stock bars, the two TAIEX indices, and the sessions list."""
    from core.research.adjustment.normalization import normalize_twse_closing_table
    from core.research.sources import RawResponse
    from core.research.sources.twse import ResponseKind, classify, find_closing_table

    stamp = pd.Timestamp('2026-10-23').to_pydatetime()
    files = {}
    for directory in INDEX_DIRS:
        for path in Path(directory).glob('MI_INDEX_*.json'):
            files.setdefault(path.stem[-8:], path)
    bars, indices, sessions, holidays = [], [], [], []
    for day, path in sorted(files.items()):
        payload = json.loads(path.read_text(encoding='utf-8'))
        kind = classify(RawResponse('twse', 'MI_INDEX', {}, stamp, None, payload, None)).kind
        date = pd.Timestamp(day)
        if kind == ResponseKind.NON_TRADING_DAY:
            holidays.append(date)
            continue
        if kind != ResponseKind.TRADING_DAY:
            continue
        sessions.append(date)
        bars.append(normalize_twse_closing_table(find_closing_table(payload), date, stamp)[['trade_date', 'stock_id', 'raw_open', 'raw_high', 'raw_low', 'raw_close',
                                                                                          'volume', 'amount']])
        indices.append({'date': date, 'taiex_price': parse_index(payload, PRICE_INDEX), 'taiex_total_return': parse_index(payload, TOTAL_RETURN_INDEX)})
    frame = pd.concat(bars, ignore_index=True).rename(columns={'raw_open': 'open', 'raw_high': 'high', 'raw_low': 'low', 'raw_close': 'close'})
    return frame, pd.DataFrame(indices), sessions, holidays


def load_events():
    """Official TWT49U events of every cached range, one row per (ex_date, stock)."""
    from datetime import datetime

    from core.research.adjustment.normalization import normalize_corporate_actions

    frames = [normalize_corporate_actions(json.loads(path.read_text(encoding='utf-8')), datetime(2026, 10, 23)).assign(range=name) for name, path in EVENT_FILES.items() if path.exists()]
    events = pd.concat(frames, ignore_index=True)
    events['ex_date'] = pd.to_datetime(events.ex_date)
    return events.drop_duplicates(['ex_date', 'stock_id']).sort_values(['stock_id', 'ex_date']).reset_index(drop=True)


def adjustment_factor(events, stock_ids, dates):
    """Product of event factors with ex_date after each (stock, date) - the same rule as core.research.adjustment.apply_adjustments."""
    factor = np.ones(len(stock_ids))
    frame = pd.DataFrame({'stock_id': np.asarray(stock_ids, dtype=str), 'date': pd.to_datetime(np.asarray(dates)), 'i': np.arange(len(stock_ids))})
    valid = events[events.pre_ex_close > 0]
    for stock, group in valid.groupby('stock_id'):
        rows = frame[frame.stock_id == stock]
        if rows.empty:
            continue
        ex, f = group.ex_date.to_numpy(), group.event_factor.to_numpy()
        suffix = np.append(np.cumprod(f[::-1])[::-1], 1.0)  # suffix[k] = product of events k..end
        factor[rows.i.to_numpy()] = suffix[np.searchsorted(ex, rows.date.to_numpy(), side='right')]
    return factor


def panel():
    bars, indices, sessions, holidays = build_panel()
    events = load_events()
    bars.to_parquet(OUT / 'official_bars.parquet', index=False)
    indices.to_csv(OUT / 'benchmarks.csv', index=False)
    events.to_csv(OUT / 'official_events.csv', index=False)
    print(len(sessions), 'sessions', len(holidays), 'non-trading weekdays', len(bars), 'bars', len(events), 'events', indices.taiex_total_return.isna().sum(), 'missing TR values')


def certify_d3():
    """D3 adjusted entry prices against the official open times the official event factors; execution dates and tradability against the bars."""
    import core.research.strategy_search as ss

    bars = pd.read_parquet(OUT / 'official_bars.parquet')
    events = load_events()
    universe = ss.load_universe(D3_ROOT)
    merged = universe.merge(bars[['trade_date', 'stock_id', 'open', 'volume']], left_on=['execution_date', 'asset_id'], right_on=['trade_date', 'stock_id'], how='left')
    factor = adjustment_factor(events[events.range.isin(['D3'])], merged.asset_id, merged.execution_date)  # D3 used only its own cached events
    expected = merged.open * factor
    priced = merged.entry_price.notna() & expected.notna()
    relative = (merged.entry_price[priced] - expected[priced]).abs() / expected[priced].abs()
    sessions = set(bars.trade_date.unique())
    exec_dates = pd.Series(universe.execution_date.dropna().unique())
    tradable_no_bar = merged[merged.is_tradable_t1 & ~(merged.volume > 0)]
    result = {
        'rows': len(universe), 'priced_rows': int(priced.sum()), 'price_match_rate': round(float((relative <= PRICE_TOLERANCE).mean()), 6),
        'price_mismatch_rows': int((relative > PRICE_TOLERANCE).sum()), 'max_relative_difference': float(relative.max()), 'tolerance': PRICE_TOLERANCE,
        'entry_price_missing_rows': int(universe.entry_price.isna().sum()), 'entry_price_present_without_official_bar': int((merged.entry_price.notna() & merged.open.isna()).sum()),
        'execution_dates': int(len(exec_dates)), 'execution_dates_not_official_sessions': [str(d.date()) for d in exec_dates if d not in sessions][:20],
        'tradable_rows_without_traded_official_bar': int(len(tradable_no_bar)), 'asset_count': int(universe.asset_id.nunique()),
        'events_used_by_D3': int(events.range.eq('D3').sum()), 'event_file_sha256': {k: digest(v) for k, v in EVENT_FILES.items() if v.exists()},
        'basis': 'official-event back-adjusted prices (reference price / previous close); returns on them are dividend- and rights-adjusted',
        'status': 'DATA_PASS' if relative.max() <= PRICE_TOLERANCE else 'DATA_FAIL',
        'exceptions': 'rows with a D3 price but no traded official bar are untradable valuation prices; see d3_nonofficial_prices.csv and the exposure check'}
    merged[merged.entry_price.notna() & merged.open.isna()][['asset_id', 'execution_date', 'entry_price', 'is_tradable_t1', 'member']].to_csv(OUT / 'd3_nonofficial_prices.csv', index=False)
    write(OUT / 'd3_certification.json', result)
    print({k: result[k] for k in ('rows', 'price_match_rate', 'price_mismatch_rows', 'execution_dates_not_official_sessions', 'tradable_rows_without_traded_official_bar', 'status')})


def delistings():
    """Official delisting list with the last official trading session of each stock (from the bars)."""
    bars = pd.read_parquet(OUT / 'official_bars.parquet')
    last = bars[bars.close.notna() & (bars.volume > 0)].groupby('stock_id').trade_date.agg(['min', 'max']).rename(columns={'min': 'first_traded', 'max': 'last_traded'})
    listing = pd.DataFrame(json.loads(DELISTING.read_text(encoding='utf-8')))
    listing['delisting_date'] = listing.DelistingDate.map(lambda s: pd.Timestamp(int(s.split('/')[0]) + 1911, int(s.split('/')[1]), int(s.split('/')[2])))
    listing = listing.rename(columns={'Code': 'stock_id', 'Company': 'name'})[['stock_id', 'name', 'delisting_date']]
    out = listing.merge(last, left_on='stock_id', right_index=True, how='left')
    out['last_traded_before_delisting'] = out.last_traded.notna() & (out.last_traded < out.delisting_date)
    out.to_csv(OUT / 'delistings.csv', index=False, encoding='utf-8-sig')
    in_window = out[out.last_traded.notna() & (out.delisting_date >= bars.trade_date.min())]
    print(len(out), 'official delistings;', len(in_window), 'with a traded bar inside the cached window')


STAGES = {'panel': panel, 'certify_d3': certify_d3, 'delistings': delistings}

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=tuple(STAGES))
    STAGES[parser.parse_args().stage]()
