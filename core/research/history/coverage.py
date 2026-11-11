"""Per (year, category) evidence for a snapshot; categories.decide turns it into the status (design D3). Reads only snapshot parts and the
pinned / reconcile files the manifest hashes. FinLab and MySQL are reconciliation sources only, never primary.
"""
from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from core.research.history import categories as cats
from core.research.history.categories import Check, Evidence

PRICE_TOLERANCE = 0.005  # TWSE quotes have two decimals; anything above half a tick is a different price
LIMIT_SWITCH = pd.Timestamp('2015-06-01')  # daily limit 7% before, 10% from this date
IPO_FREE_SESSIONS = 5  # newly listed stocks trade without a limit for their first five sessions
FINLAB_END = pd.Timestamp('2023-12-29')
# TWSE company profile (codes and listing dates of companies listed when it was saved): an audit population only, so a currently listed
# code missing from a year's MI_INDEX files is caught even if it never appears locally. It never feeds a universe, a factor or a signal.
LISTING_PROFILE = Path(__file__).resolve().parents[3] / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd/t187ap03_L.json'


@functools.lru_cache(maxsize=2)
def _listing_dates(path, _mtime):
    from core.research.adjustment.normalization import normalize_company_profile_listing_dates
    return normalize_company_profile_listing_dates(json.loads(Path(path).read_text(encoding='utf-8')))


def route_status(category) -> str | None:
    """Denial code of the category's legal route; None when no legal source is known. Never sends a request."""
    route = category.route
    if route is None:
        return None
    if route.budget_job is None:
        return f'NO_ALLOWLISTED_ROUTE:{route.endpoint}'
    from core.runtime import research_budget
    try:
        research_budget.check(route.budget_job)
    except research_budget.BudgetDenied as exc:
        return f'{str(exc).split(":")[0]}:{route.budget_job}'
    return f'BUDGET_READY_NOT_FETCHED:{route.budget_job}'


class Facts:
    """Lazy views of one snapshot shared by every evaluator."""

    def __init__(self, snap, route=route_status):
        self.snap, self.route = snap, route

    def part(self, name):
        frame = self.snap.frame(name)
        dates = [c for c in ('date', 'trade_date', 'ex_date', 'delisting_date', 'first_listed', 'last_listed', 'last_traded') if c in frame]
        return frame.astype({c: 'datetime64[ns]' for c in dates}) if dates else frame  # an empty part comes back with object columns

    @functools.cached_property
    def calendar(self):
        cal = self.part('calendar')
        return cal.assign(year=cal.date.dt.year)

    @functools.cached_property
    def sessions(self):
        return pd.DatetimeIndex(self.calendar.loc[self.calendar.kind == 'TRADING', 'date'])

    @functools.cached_property
    def bars(self):
        bars = self.part('bars')
        return bars.assign(year=bars.trade_date.dt.year)

    @functools.cached_property
    def traded(self):
        return self.bars[self.bars.status == 'TRADED']

    @functools.cached_property
    def finlab_close(self):
        path = self.snap.source('reconcile', 'finlab_close')
        if path is None:
            return None
        wide = pd.read_parquet(path)
        wide = wide.loc[(wide.index >= self.sessions.min()) & (wide.index <= FINLAB_END), [c for c in wide.columns if cats_stock(c)]]
        long = wide.stack().rename('finlab_close').reset_index()
        return long.rename(columns={long.columns[0]: 'trade_date', long.columns[1]: 'stock_id'})

    @functools.cached_property
    def mysql(self):
        path = self.snap.source('reconcile', 'mysql_daily_market_data')
        return None if path is None else pd.read_parquet(path)

    @functools.cached_property
    def missing_sessions(self) -> pd.DatetimeIndex:
        """Sessions an independent source traded (weekends included) that have no local MI_INDEX file."""
        days = set()
        if self.finlab_close is not None:
            days |= set(self.finlab_close.trade_date.unique())
        if self.mysql is not None:
            days |= set(self.mysql.trade_date.unique())
        start, end = self.calendar.date.min(), self.calendar.date.max()
        return pd.DatetimeIndex(sorted(d for d in days if start <= d <= end and d not in set(self.sessions)))

    @functools.cached_property
    def jumps(self):
        return price_jumps(self.bars, self.part('events'), self.part('capital_changes'), self.missing_sessions)


def spans_missing(prev, day, missing: pd.DatetimeIndex) -> pd.Series:
    """True where a missing session lies strictly between `prev` and `day` (element-wise)."""
    if len(missing) == 0:
        return pd.Series(False, index=prev.index)
    return pd.Series(missing.searchsorted(day.to_numpy(), side='left') > missing.searchsorted(prev.to_numpy(), side='right'), index=prev.index) & prev.notna()


def cats_stock(code) -> bool:
    return isinstance(code, str) and len(code) == 4 and code.isdigit() and code[0] != '0'


def price_jumps(bars, events, capital, missing_sessions=pd.DatetimeIndex([])) -> pd.DataFrame:
    """Opens beyond the compounded daily limit (+1 pt) vs the previous traded close, outside a stock's first five traded sessions, and
    whether any TWT49U or capital-change event of that stock falls in (previous traded session, session]. Sessions missing on disk count
    as steps too, so a gap across them is a jump only beyond every limit it could have used; after_missing_session is informational."""
    traded = bars[bars.status == 'TRADED'].sort_values(['stock_id', 'trade_date'])[['stock_id', 'trade_date', 'open', 'close']].copy()
    grouped = traded.groupby('stock_id')
    traded['prev_close'] = grouped['close'].shift()
    traded['prev_date'] = grouped['trade_date'].shift()
    traded['session_no'] = grouped.cumcount()
    sessions = pd.DatetimeIndex(sorted(bars.trade_date.unique()))
    missing = pd.DatetimeIndex(missing_sessions)
    prev = traded['prev_date'].fillna(traded['trade_date']).to_numpy()
    # untraded sessions in between (locked limits, suspensions, sessions missing on disk) each allow one more daily limit, compounded
    gap = missing.searchsorted(traded['trade_date'].to_numpy(), side='left') - missing.searchsorted(prev, side='right') if len(missing) else 0
    steps = sessions.searchsorted(traded['trade_date'].to_numpy()) - sessions.searchsorted(prev) + np.maximum(gap, 0)
    limit = np.where(traded['trade_date'] < LIMIT_SWITCH, 0.07, 0.10)
    upper, lower = (1 + limit) ** np.maximum(steps, 1) - 1 + 0.01, (1 - limit) ** np.maximum(steps, 1) - 1 - 0.01
    traded['move'] = traded['open'].astype(float) / traded['prev_close'].astype(float) - 1
    jumps = traded[(traded['session_no'] >= IPO_FREE_SESSIONS) & ((traded['move'] > upper) | (traded['move'] < lower))]
    marks = pd.concat([events[['stock_id', 'ex_date']].rename(columns={'ex_date': 'event_date'}),
                       capital[['stock_id', 'date']].rename(columns={'date': 'event_date'})], ignore_index=True).dropna()
    marks['event_date'] = pd.to_datetime(marks['event_date'])
    if jumps.empty:
        return jumps.assign(explained=pd.Series(dtype=bool), after_missing_session=pd.Series(dtype=bool), year=pd.Series(dtype=int))
    matched = pd.merge_asof(jumps.sort_values('trade_date'), marks.sort_values('event_date'), left_on='trade_date', right_on='event_date',
                            by='stock_id', direction='backward')
    matched['explained'] = matched['event_date'].notna() & (matched['event_date'] > matched['prev_date'])
    matched['after_missing_session'] = spans_missing(matched['prev_date'], matched['trade_date'], pd.DatetimeIndex(missing_sessions))
    return matched.assign(year=matched['trade_date'].dt.year).drop(columns=['session_no'])


def _price_check(name, mine, other, column):
    merged = mine.merge(other[['trade_date', 'stock_id', column]].dropna(), on=['trade_date', 'stock_id'], how='left')
    compared = merged[column].notna()
    agreed = (merged.close[compared] - merged[column][compared]).abs() <= PRICE_TOLERANCE
    sample = merged[compared & ~((merged.close - merged[column]).abs() <= PRICE_TOLERANCE)].head(5)
    return Check(name, len(mine), int(compared.sum()), int(agreed.sum())), [f'{r.trade_date.date()}:{r.stock_id}:{r.close}!={r[column]}'
                                                                             for _, r in sample.iterrows()]


def calendar(f: Facts, year: int) -> Evidence:
    cal = f.calendar[f.calendar.year == year]
    if cal.kind.isin(['TRADING', 'NON_TRADING']).sum() == 0:
        return Evidence(local=False, route=f.route(cats.BY_ID['calendar']))
    integrity = []
    if (n := int((cal.kind == 'UNCLASSIFIED').sum())):
        integrity.append(f'UNCLASSIFIED_FILE:{n}')
    if (n := int((cal.logged_sha256.notna() & (cal.logged_sha256 != cal.sha256)).sum())):
        integrity.append(f'RAW_SHA_DIFFERS_FROM_FETCH_LOG:{n}')
    sessions = set(cal.loc[cal.kind == 'TRADING', 'date'])
    weekend = [d for d in f.missing_sessions if d.year == year and d.weekday() >= 5]
    checks, other_days = [], set()
    if f.finlab_close is not None and year <= FINLAB_END.year:
        finlab_days = set(f.finlab_close.loc[f.finlab_close.trade_date.dt.year == year, 'trade_date'])
        other_days |= finlab_days
        days = cal.date[(cal.date <= FINLAB_END) & cal.kind.isin(['TRADING', 'NON_TRADING'])]  # missing files are incompleteness, not contradiction
        checks.append(Check('finlab_trading_days', len(days), len(days), int(sum((d in sessions) == (d in finlab_days) for d in days))))
    if f.mysql is not None and year >= 2022:
        db_days = set(f.mysql.loc[f.mysql.trade_date.dt.year == year, 'trade_date'])
        other_days |= db_days
        classified = db_days & set(cal.loc[cal.kind.isin(['TRADING', 'NON_TRADING']), 'date'])
        checks.append(Check('mysql_trading_days', len(sessions), len(classified), len(classified & sessions)))
    no_file = cal[cal.kind == 'NO_FILE'].date
    agreed_closed = [d for d in no_file if checks and d not in other_days]
    missing = sorted([d for d in no_file if d not in agreed_closed] + weekend)
    return Evidence(local=True, expected=len(cal) + len(weekend), present=int(cal.kind.isin(['TRADING', 'NON_TRADING']).sum()) + len(agreed_closed),
                    checks=tuple(checks), integrity=tuple(integrity),
                    caveats=(f'MISSING_SESSIONS_NOT_LOCAL:{len(missing)}:{f.route(cats.BY_ID["calendar"])}',) if missing else (),
                    metrics={'weekdays': len(cal), 'sessions': len(sessions), 'non_trading_files': int((cal.kind == 'NON_TRADING').sum()),
                             'no_file_weekdays': len(no_file), 'no_file_agreed_closed': len(agreed_closed),
                             'missing_sessions': [str(d.date()) for d in missing]})


def prices(f: Facts, year: int) -> Evidence:
    sessions = f.sessions[f.sessions.year == year]
    bars = f.bars[f.bars.year == year]
    if bars.empty:
        return Evidence(local=False, route=f.route(cats.BY_ID['prices']))
    integrity_rows = f.part('integrity')
    integrity_rows = integrity_rows[integrity_rows.stock_id.notna() & (pd.to_datetime(integrity_rows.date).dt.year == year)]
    integrity = tuple(f'{issue}:{n}' for issue, n in integrity_rows.issue.value_counts().items())
    traded = bars[bars.status == 'TRADED'][['trade_date', 'stock_id', 'close']]
    checks, samples = [], {}
    if f.finlab_close is not None and year <= FINLAB_END.year:
        check, samples['finlab_close'] = _price_check('finlab_close', traded, f.finlab_close, 'finlab_close')
        checks.append(check)
    if f.mysql is not None and year >= 2022:
        check, samples['mysql_close'] = _price_check('mysql_close', traded, f.mysql.rename(columns={'close_price': 'db_close'}), 'db_close')
        checks.append(check)
    complete = bars[['open', 'high', 'low', 'close', 'volume', 'amount']].notna().all(axis=1) | (bars.status == 'NO_TRADE')
    missing = [d for d in f.missing_sessions if d.year == year]
    return Evidence(local=True, expected=len(sessions) + len(missing), present=int(bars.trade_date.nunique()), checks=tuple(checks), integrity=integrity,
                    metrics={'missing_sessions': [str(d.date()) for d in missing],'stocks': int(bars.stock_id.nunique()), 'rows': len(bars), 'traded_rows': len(traded),
                             'no_trade_rows': int((bars.status == 'NO_TRADE').sum()), 'ohlcv_complete_share': float(complete.mean()),
                             'mismatch_samples': samples})


def universe(f: Facts, year: int) -> Evidence:
    bars = f.bars[f.bars.year == year]
    if bars.empty:
        return Evidence(local=False, route=f.route(cats.BY_ID['universe']))
    delist = f.part('delistings')
    later = delist[delist.first_listed.notna() & (delist.first_listed.dt.year <= year)]
    first_day = bars.trade_date.min()
    members = set(bars.loc[bars.trade_date == first_day, 'stock_id'])
    checks, omitted = [], {}
    local = set(bars.stock_id)
    listed = set(bars.loc[bars.status == 'TRADED', 'stock_id'])
    twse_ever = set(f.bars.stock_id)  # FinLab mixes TPEx in: the reverse check only asks about codes seen on TWSE in some year
    if f.finlab_close is not None and year <= FINLAB_END.year:
        known = set(f.finlab_close.loc[f.finlab_close.trade_date.dt.year == year, 'stock_id'])
        checks.append(Check('finlab_traded_stocks', len(listed), len(listed), len(listed & known)))
        # day level, inside each code's own TWSE span (a TPEx <-> TWSE transfer happens outside it and is not an omission)
        span = f.bars.groupby('stock_id').trade_date.agg(['min', 'max'])
        theirs = f.finlab_close[(f.finlab_close.trade_date.dt.year == year) & f.finlab_close.stock_id.isin(twse_ever)]
        theirs = theirs[theirs.trade_date.isin(set(f.sessions))].join(span, on='stock_id')
        theirs = theirs[(theirs.trade_date >= theirs['min']) & (theirs.trade_date <= theirs['max'])]
        present = theirs.merge(bars[['trade_date', 'stock_id']], on=['trade_date', 'stock_id'], how='left', indicator=True)['_merge'] == 'both'
        checks.append(Check('finlab_stock_days_present_locally', len(theirs), len(theirs), int(present.sum())))
        omitted['finlab'] = [f'{r.trade_date.date()}:{r.stock_id}' for r in theirs[~present.to_numpy()].head(20).itertuples()]
    if f.mysql is not None and year >= 2022:
        db = f.mysql[(f.mysql.trade_date.dt.year == year) & f.mysql.close_price.notna()] if 'close_price' in f.mysql else f.mysql.iloc[0:0]
        known = set(db.stock_id)
        checks.append(Check('mysql_stocks_present_locally', len(known), len(known), len(known & local)))
        omitted['mysql'] = sorted(known - local)[:20]
    if LISTING_PROFILE.exists():
        listed_by = _listing_dates(str(LISTING_PROFILE), LISTING_PROFILE.stat().st_mtime_ns)
        should = {sid for sid, day in listed_by.items() if day <= pd.Timestamp(year, 12, 31)}
        checks.append(Check('twse_profile_listed_present_locally', len(should), len(should), len(should & local)))
        omitted['twse_profile'] = sorted(should - local)[:20]
        omitted['twse_profile_sha256'] = hashlib.sha256(LISTING_PROFILE.read_bytes()).hexdigest()
    in_year = delist[delist.delisting_date.dt.year == year]
    absent = in_year[in_year.first_listed.isna()]
    suspended = suspended_before_window(f, absent.stock_id)
    missing = int(len(absent) - len(suspended))
    caveats = (f'DELISTED_NOT_FOUND_IN_UNIVERSE:{missing}',) if missing else ()
    sessions = f.sessions[f.sessions.year == year]
    return Evidence(local=True, expected=len(sessions), present=int(bars.trade_date.nunique()), checks=tuple(checks), caveats=caveats,
                    metrics={'listed_first_session': len(members), 'listed_any_session': int(bars.stock_id.nunique()), 'omitted_locally': omitted,
                             'delisted_suspended_before_window': suspended,
                             'members_later_delisted': len(members & set(later.stock_id)), 'six_digit_codes': int((bars.stock_id.str.len() != 4).sum()),
                             'scope': 'TWSE_LISTED_FOUR_DIGIT_EQUITIES (TPEx excluded; TDR codes 91xx are four-digit and included)'})


def suspended_before_window(f: Facts, codes) -> dict:
    """{stock_id: last independent trade date} for delisted codes absent from every local session whose last trade (FinLab) precedes the
    window: suspended before the window started, so their absence from the official files is expected (2384, 5280 in 2015)."""
    path = f.snap.source('reconcile', 'finlab_close')
    if path is None:
        return {}
    wide = pd.read_parquet(path, columns=[c for c in codes]) if codes is not None and len(codes) else None
    if wide is None:
        return {}
    start = pd.Timestamp(f.snap.manifest['window'][0])  # Facts.finlab_close starts at the window: the pre-window history is read here
    last = {sid: wide[sid].dropna().index.max() for sid in wide.columns}
    return {sid: str(pd.Timestamp(day).date()) for sid, day in last.items() if pd.notna(day) and pd.Timestamp(day) < start}


def delistings(f: Facts, year: int) -> Evidence:
    delist = f.part('delistings')
    rows = delist[delist.delisting_date.dt.year == year]
    if not f.snap.manifest['sources'].get('delisting'):
        return Evidence(local=False, route=None)  # no delisting list at all: an empty table proves nothing
    if f.bars[f.bars.year == year].empty:
        return Evidence(local=False, route=f.route(cats.BY_ID['delistings']))
    suspended = suspended_before_window(f, rows.loc[rows.first_listed.isna(), 'stock_id'])
    found = rows[(rows.first_listed.notna() & (rows.last_listed <= rows.delisting_date)) | rows.stock_id.isin(suspended)]
    checks = []
    traded_here = found[~found.stock_id.isin(suspended)]
    for name, other, ok in (('finlab_last_trade_date', f.finlab_close, year <= FINLAB_END.year), ('mysql_last_trade_date', f.mysql, year >= 2022)):
        if other is not None and ok and len(traded_here):
            close = 'finlab_close' if name.startswith('finlab') else 'close_price'
            last = other[other[close].notna()].groupby('stock_id').trade_date.max()
            lag = (traded_here.last_traded.to_numpy() - last.reindex(traded_here.stock_id).to_numpy())
            days = pd.Series(lag).dt.days.abs()
            agreed = int((days <= 7).sum()) + len(suspended)  # a pre-window suspension agrees by construction: no trade on either side
            checks.append(Check(name, len(found), int(days.notna().sum()) + len(suspended), agreed))
    lags = (rows.delisting_date - rows.last_listed).dt.days.dropna()
    listed_years = delist.delisting_date.dt.year
    covered = bool(len(delist)) and listed_years.min() <= year <= listed_years.max()  # the list has entries on both sides of the year
    return Evidence(local=True, expected=len(rows), present=len(found), checks=tuple(checks), covered=covered,
                    metrics={'delistings': len(rows), 'found_before_delisting': len(found), 'suspended_before_window': suspended,
                             'median_days_last_listed_to_delisting': float(lags.median()) if len(lags) else None,
                             'not_found': sorted(set(rows.stock_id) - set(found.stock_id))[:20]})


def dividends(f: Facts, year: int) -> Evidence:
    events = f.part('events')
    events = events[events.ex_date.dt.year == year]
    if events.empty:
        return Evidence(local=False, route=f.route(cats.BY_ID['dividends']))
    terms = (events.pre_ex_close > 0) & (events.ex_reference_price > 0)
    previous = pd.Series(f.sessions[(f.sessions.searchsorted(events.ex_date.to_numpy()) - 1).clip(0)], index=events.index)
    after_gap = spans_missing(previous, events.ex_date, f.missing_sessions)  # the true prior session is not on disk
    with_prior = events.prior_session_close.notna() & ~after_gap
    agreed = (events.pre_ex_close - events.prior_session_close).abs() <= PRICE_TOLERANCE
    formula = (events.value - (events.pre_ex_close - events.ex_reference_price)).abs() <= 0.011
    caveats = (f'VALUE_FORMULA_MISMATCH:{int((terms & events.value.notna() & ~formula).sum())}',) if (terms & events.value.notna() & ~formula).any() else ()
    if after_gap.any():
        caveats += (f'PRIOR_SESSION_MISSING_LOCALLY:{int(after_gap.sum())}',)
    return Evidence(local=True, expected=len(events), present=int(terms.sum()),
                    checks=(Check('mi_index_prior_close', len(events), int(with_prior.sum()), int((with_prior & agreed).sum())),), caveats=caveats,
                    metrics={'events': len(events), 'events_after_missing_session': int(after_gap.sum()),
                             'cash': int(events.action_type.astype(str).str.contains('息').sum()),
                             'stock': int(events.action_type.astype(str).str.contains('權').sum()),
                             'prior_close_mismatch_samples': [f'{r.ex_date.date()}:{r.stock_id}:{r.pre_ex_close}!={r.prior_session_close}'
                                                              for r in events[with_prior & ~agreed].head(5).itertuples()]})


def _covered(spans, year) -> bool:
    return any(pd.Timestamp(s) <= pd.Timestamp(year, 1, 5) and pd.Timestamp(e) >= pd.Timestamp(year, 12, 28) for s, e in spans)


def capital_changes(f: Facts, year: int) -> Evidence:
    spans = f.snap.manifest.get('capital_spans', {})
    covered = [k for k in ('TWTAUU', 'TWTB8U') if _covered(spans.get(k, []), year)]
    jumps = f.jumps[f.jumps.year == year] if len(f.jumps) else f.jumps
    open_ = (~jumps.explained) if len(jumps) else pd.Series(dtype=bool)
    unexplained = int(open_.sum())
    metrics = {'datasets_covering_year': covered, 'price_jumps': len(jumps), 'unexplained_price_jumps': unexplained,
               'jumps_after_missing_session': int(jumps.after_missing_session.sum()) if len(jumps) else 0,
               'unexplained_samples': [f'{r.trade_date.date()}:{r.stock_id}:{r.move:+.3f}' for r in jumps[open_].head(8).itertuples()]
               if len(jumps) else []}
    if not covered:
        return Evidence(local=False, route=f.route(cats.BY_ID['capital_changes']), metrics=metrics)
    capital = f.part('capital_changes')
    capital = capital[capital.date.dt.year == year]
    checks = []
    path = f.snap.source('reconcile', 'finlab_capital_reduction')
    if path is not None and year <= FINLAB_END.year:
        wide = pd.read_parquet(path)
        theirs = wide.stack().reset_index()
        theirs.columns = ['date', 'stock_id', 'ratio']
        theirs = theirs[pd.to_datetime(theirs.date).dt.year == year]
        mine = capital[capital.kind == 'CAPITAL_REDUCTION']
        hits = sum(((mine.stock_id == r.stock_id) & ((mine.date - pd.Timestamp(r.date)).abs().dt.days <= 31)).any() for r in theirs.itertuples())
        checks.append(Check('finlab_capital_reduction', len(theirs), len(theirs), int(hits)))
    caveats = (f'UNEXPLAINED_PRICE_JUMPS:{unexplained}',) if unexplained else ()
    return Evidence(local=True, expected=2, present=len(covered), checks=tuple(checks), caveats=caveats,
                    metrics=metrics | {'events': len(capital)})


def _index(f, year, column):
    bench = f.part('benchmark')
    bench = bench[bench.date.dt.year == year]
    sessions = f.sessions[f.sessions.year == year]
    if bench.empty:
        return Evidence(local=False, route=None)
    present = int(bench[column].notna().sum())
    return Evidence(local=True, expected=len(sessions), present=present, checks=(),
                    metrics={'sessions': len(sessions), 'values': present, 'source': 'MI_INDEX index rows (same files as prices)'})


def benchmark_price(f, year):
    return _index(f, year, 'taiex_price')


def benchmark_total_return(f, year):
    return _index(f, year, 'taiex_total_return')


def _chips(f, year, dataset, category):
    chips = f.part('chips')
    rows = chips[(chips.dataset == dataset) & (chips.date.dt.year == year)]
    sessions = f.sessions[f.sessions.year == year]
    ok = int((rows.stat == 'OK').sum())
    if not ok:
        return Evidence(local=False, route=f.route(cats.BY_ID[category]))
    # stock-days, not files: a traded stock without a row is excluded from candidates by the engine's frame
    traded = f.traded[f.traded.year == year].groupby('trade_date').stock_id.agg(set)
    covered = 0
    for row in rows[rows.stat == 'OK'].itertuples():
        path = f.snap.source('chips', f'{dataset}_{row.date:%Y%m%d}')
        covered += len(chip_codes(path) & traded.get(row.date, set()))
    missing_files = len(sessions) - ok
    return Evidence(local=True, expected=int(traded.map(len).sum()), present=covered, checks=(),
                    caveats=(f'SESSIONS_WITHOUT_FILE:{missing_files}',) if missing_files > 0 else (),
                    metrics={'files': len(rows), 'ok_files': ok, 'traded_stock_days': int(traded.map(len).sum()), 'stock_days_with_row': covered,
                             'note': 'no independent value-level source for these years'})


def chip_codes(path) -> set:
    """Four-digit codes listed in a cached T86 / MI_MARGN response (top-level data or any table's data)."""
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    rows = list(payload.get('data') or [])
    for table in payload.get('tables') or []:
        rows += (table.get('data') or []) if isinstance(table, dict) else []
    return {str(r[0]).strip() for r in rows if r and cats_stock(str(r[0]).strip())}


def t86_flows(f, year):
    return _chips(f, year, 'T86', 't86_flows')


def margin(f, year):
    return _chips(f, year, 'MI_MARGN', 'margin')


def _period_mask(frame, column, periods) -> pd.Series:
    return pd.Series([(y, p) in periods for y, p in zip(frame.year, frame[column])], index=frame.index, dtype=bool)


def _filings_used(year):
    """Fiscal periods whose statutory filing falls in calendar `year`: Q4 of year-1, Q1..Q3 of year."""
    return [(year - 1, 4), (year, 1), (year, 2), (year, 3)]


@functools.lru_cache(maxsize=4)
def _publications(path, _sha):
    from core.research.native_frame import PIT_RECORDS, load_publication_dates
    if Path(path).resolve() != PIT_RECORDS.resolve():
        raise ValueError(f'PUBLICATION_SOURCE_NOT_ENGINE_SOURCE: {path}')
    return load_publication_dates()  # the engine's own derivation of the first MOPS publication date


def _listed_mid_year(f, year):
    bars = f.bars[f.bars.year == year]
    return set(bars.loc[bars.trade_date == bars.trade_date.min(), 'stock_id']) if len(bars) else set()


def financial_statements(f, year):
    path = f.snap.source('pinned', 'financial_statements')
    fin = pd.read_parquet(path) if path else pd.DataFrame(columns=['stock_id', 'year', 'quarter', 'eps'])
    keys = set(_filings_used(year))
    fin = fin.loc[_period_mask(fin, "quarter", keys)]
    listed = _listed_mid_year(f, year)
    fin = fin[fin.stock_id.astype(str).isin(listed)]
    if fin.empty:
        return Evidence(local=False, route=f.route(cats.BY_ID['financial_statements']))
    checks = []
    pit = f.snap.source('pinned', 'publication_records')
    if pit is not None:
        records = pd.read_parquet(pit, columns=['ticker', 'metric', 'value', 'fiscal_year', 'quarter'])
        eps = records[records.metric == 'eps'].drop_duplicates(['ticker', 'fiscal_year', 'quarter'], keep='first')
        merged = fin.assign(stock_id=fin.stock_id.astype(str)).merge(
            eps.rename(columns={'ticker': 'stock_id', 'fiscal_year': 'year', 'value': 'mops_eps'}), on=['stock_id', 'year', 'quarter'], how='left')
        both = merged.eps.notna() & merged.mops_eps.notna()
        checks.append(Check('mops_pit_eps', int(merged.eps.notna().sum()), int(both.sum()),
                            int(((merged.eps - merged.mops_eps).abs() <= 0.01)[both].sum())))
    return Evidence(local=True, expected=len(listed) * 4, present=len(fin), checks=tuple(checks),
                    caveats=('ENGINE_FINANCIALS_FROM_MYSQL_SNAPSHOT',),
                    metrics={'filings': len(fin), 'listed_stocks': len(listed), 'periods': [f'{y}Q{q}' for y, q in sorted(keys)]})


def financial_publication_time(f, year):
    path = f.snap.source('pinned', 'financial_statements')
    pit = f.snap.source('pinned', 'publication_records')
    if path is None or pit is None:
        return Evidence(local=False, route=f.route(cats.BY_ID['financial_publication_time']))
    fin = pd.read_parquet(path, columns=['stock_id', 'year', 'quarter'])
    keys = set(_filings_used(year))
    fin = fin.loc[_period_mask(fin, "quarter", keys)].assign(stock_id=lambda x: x.stock_id.astype(str))
    fin = fin[fin.stock_id.isin(_listed_mid_year(f, year))]
    if fin.empty:
        return Evidence(local=False, route=f.route(cats.BY_ID['financial_publication_time']))
    pubs = _publications(str(pit), f.snap.manifest['sources']['pinned']['publication_records']['sha256'])
    merged = fin.merge(pubs, on=['stock_id', 'year', 'quarter'], how='left')
    dated = int(merged.publication_date.notna().sum())
    return Evidence(local=True, expected=len(fin), present=dated, checks=(),
                    caveats=('CORRECTION_HISTORY_UNVERIFIED',),
                    metrics={'filings': len(fin), 'with_mops_publication_time': dated,
                             'note': 'first-publication time from MOPS t57sb01 lists; amended-version history not recoverable for every filing'})


def monthly_revenue(f, year):
    path = f.snap.source('pinned', 'monthly_revenue')
    rev = pd.read_parquet(path) if path else pd.DataFrame(columns=['stock_id', 'year', 'month'])
    months = {(year - 1, 12), *((year, m) for m in range(1, 12))}
    rev = rev.loc[_period_mask(rev, "month", months)]
    listed = _listed_mid_year(f, year)
    rev = rev[rev.stock_id.astype(str).isin(listed)]
    if rev.empty:
        return Evidence(local=False, route=f.route(cats.BY_ID['monthly_revenue']))
    return Evidence(local=True, expected=len(listed) * 12, present=len(rev), checks=(), caveats=('NO_ANNOUNCEMENT_TIME',),
                    metrics={'rows': len(rev), 'listed_stocks': len(listed)})


def revenue_announcement_time(f, year):
    return Evidence(local=False, route=f.route(cats.BY_ID['revenue_announcement_time']))


EVALUATORS = {'calendar': calendar, 'prices': prices, 'universe': universe, 'delistings': delistings, 'dividends': dividends,
              'capital_changes': capital_changes, 'benchmark_price': benchmark_price, 'benchmark_total_return': benchmark_total_return,
              't86_flows': t86_flows, 'margin': margin, 'financial_statements': financial_statements,
              'financial_publication_time': financial_publication_time, 'monthly_revenue': monthly_revenue,
              'revenue_announcement_time': revenue_announcement_time}
if set(EVALUATORS) != set(cats.BY_ID):
    raise ImportError('EVALUATORS must cover every category exactly')


def code_sha() -> str:
    digest = hashlib.sha256()
    for module in (cats.__file__, __file__):
        digest.update(Path(module).read_bytes())
    return digest.hexdigest()


def evaluate(snap, route=route_status) -> list[dict]:
    """Every (year, category) cell; never skipped."""
    facts, cells = Facts(snap, route), []
    for category in cats.CATEGORIES:
        for year in cats.YEARS:
            evidence = EVALUATORS[category.id](facts, year)
            status, reasons = cats.decide(evidence)
            cells.append({'year': year, 'category': category.id, 'label': category.label, 'role': category.role, 'status': status,
                          'reasons': list(reasons), 'expected': evidence.expected, 'present': evidence.present,
                          'checks': [asdict(c) | {'rate': c.rate} for c in evidence.checks], 'caveats': list(evidence.caveats),
                          'route': evidence.route, 'metrics': json.loads(json.dumps(evidence.metrics, default=str))})
    return cells
