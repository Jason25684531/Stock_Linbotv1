import pandas as pd
import pytest

from core.research.history import categories as cats
from core.research.history import coverage as cov
from core.research.history import snapshot as hs
import json

from test_history_snapshot import mi_index, quote, sources  # noqa: F401  (fixture)


def hs_quote(sid):
    return quote(sid, 'X', '10.00', '10.00', '10.00', '10.00')


def _cells(snap, **kw):
    return {(c['year'], c['category']): c for c in cov.evaluate(snap, **kw)}


def _mysql(tmp_path, bars, delta=0.0):
    traded = bars[bars.status == 'TRADED']
    frame = pd.DataFrame({'trade_date': traded.trade_date, 'stock_id': traded.stock_id, 'close_price': traded.close + delta})
    path = tmp_path / 'mysql.parquet'
    frame.to_parquet(path, index=False)
    return path


def test_every_year_and_category_has_a_cell(sources, tmp_path):
    snap, _ = hs.build(sources, tmp_path / 'out')
    cells = cov.evaluate(snap)
    assert len(cells) == len(cats.CATEGORIES) * len(cats.YEARS)
    assert {c['status'] for c in cells} <= set(cats.STATUSES)


def test_missing_chip_history_is_access_blocked_by_budget(sources, tmp_path):
    snap, _ = hs.build(sources, tmp_path / 'out')
    cell = _cells(snap)[(2024, 't86_flows')]
    assert cell['status'] == 'ACCESS_BLOCKED' and cell['reasons'][0] == 'BUDGET_UNASSIGNED:hist_foundation_chips'


def test_prices_reconciled_against_mysql(sources, tmp_path):
    bars = hs.build(sources, tmp_path / 'a')[0].frame('bars')
    good = hs.Sources(**{**sources.__dict__, 'reconcile': {'mysql_daily_market_data': _mysql(tmp_path, bars)}})
    cell = _cells(hs.build(good, tmp_path / 'b')[0])[(2024, 'prices')]
    assert cell['checks'][0]['against'] == 'mysql_close' and cell['checks'][0]['rate'] == 1.0
    assert cell['status'] == 'VERIFIED'


def test_price_disagreement_fails_validation(sources, tmp_path):
    bars = hs.build(sources, tmp_path / 'a')[0].frame('bars')
    bad = hs.Sources(**{**sources.__dict__, 'reconcile': {'mysql_daily_market_data': _mysql(tmp_path, bars, delta=1.0)}})
    assert _cells(hs.build(bad, tmp_path / 'b')[0])[(2024, 'prices')]['status'] == 'FAILED_VALIDATION'


def test_dividend_prior_close_matches_mi_index(sources, tmp_path):
    cell = _cells(hs.build(sources, tmp_path / 'out')[0])[(2024, 'dividends')]
    assert cell['checks'][0] == {'against': 'mi_index_prior_close', 'population': 1, 'compared': 1, 'agreed': 1, 'rate': 1.0}


def test_later_delisted_stock_found_before_delisting(sources, tmp_path):
    cell = _cells(hs.build(sources, tmp_path / 'out')[0])[(2024, 'delistings')]
    assert cell['expected'] == 1 and cell['present'] == 1


def test_missing_delisting_source_is_never_verified(sources, tmp_path):
    snap, _ = hs.build(hs.Sources(**{**sources.__dict__, 'delisting_file': None}), tmp_path / 'out')
    assert _cells(snap)[(2024, 'delistings')]['status'] == 'SOURCE_UNAVAILABLE'


def test_universe_reverse_check_finds_stock_missing_locally(sources, tmp_path):
    bars = hs.build(sources, tmp_path / 'a')[0].frame('bars')
    extract = _mysql(tmp_path, bars)
    frame = pd.read_parquet(extract)
    frame = pd.concat([frame, pd.DataFrame({'trade_date': [pd.Timestamp('2024-01-02')], 'stock_id': ['2002'], 'close_price': [30.0]})])
    frame.to_parquet(extract, index=False)
    snap = hs.build(hs.Sources(**{**sources.__dict__, 'reconcile': {'mysql_daily_market_data': extract}}), tmp_path / 'b')[0]
    cell = _cells(snap)[(2024, 'universe')]
    reverse = next(k for k in cell['checks'] if k['against'] == 'mysql_stocks_present_locally')
    assert reverse['compared'] == reverse['agreed'] + 1 and cell['status'] != 'VERIFIED'


def test_unexplained_jump_is_counted_and_explained_jump_is_not():
    days = pd.bdate_range('2024-01-01', periods=8)
    rows = [{'trade_date': d, 'stock_id': sid, 'open': o, 'close': o, 'status': 'TRADED'}
            for sid, opens in (('1101', [10] * 6 + [6, 6]), ('2330', [10] * 6 + [6, 6])) for d, o in zip(days, opens)]
    bars = pd.DataFrame(rows)
    events = pd.DataFrame({'stock_id': ['2330'], 'ex_date': [days[6]]})
    capital = pd.DataFrame({'stock_id': pd.Series(dtype=str), 'date': pd.Series(dtype='datetime64[ns]')})
    jumps = cov.price_jumps(bars, events, capital).set_index('stock_id')
    assert not jumps.loc['1101', 'explained'] and jumps.loc['2330', 'explained']


def test_jump_across_missing_saturday_session_is_attributed_to_calendar():
    days = list(pd.bdate_range('2016-05-26', periods=6)) + [pd.Timestamp('2016-06-06')]  # 2016-06-04 Saturday session not on disk
    bars = pd.DataFrame({'trade_date': days, 'stock_id': '1101', 'open': [10] * 6 + [12.0], 'close': [10] * 6 + [12.0], 'status': 'TRADED'})
    empty = pd.DataFrame({'stock_id': pd.Series(dtype=str), 'ex_date': pd.Series(dtype='datetime64[ns]')})
    assert len(cov.price_jumps(bars, empty, empty.rename(columns={'ex_date': 'date'}))) == 1  # +20% in one visible step
    assert cov.price_jumps(bars, empty, empty.rename(columns={'ex_date': 'date'}), pd.DatetimeIndex(['2016-06-04'])).empty  # two limits


def test_weekend_session_in_independent_source_makes_calendar_partial(sources, tmp_path):
    bars = hs.build(sources, tmp_path / 'a')[0].frame('bars')
    extract = _mysql(tmp_path, bars)
    frame = pd.read_parquet(extract)
    frame = pd.concat([frame, pd.DataFrame({'trade_date': [pd.Timestamp('2024-01-06')], 'stock_id': ['1101'], 'close_price': [9.6]})])
    frame.to_parquet(extract, index=False)
    snap = hs.build(hs.Sources(**{**sources.__dict__, 'window': ('2024-01-01', '2024-01-08'), 'reconcile': {'mysql_daily_market_data': extract}}),
                    tmp_path / 'b')[0]
    cell = _cells(snap)[(2024, 'calendar')]
    assert cell['status'] == 'PARTIAL' and cell['metrics']['missing_sessions'] == ['2024-01-06']
    # the route names the Owner budget job; its budget state (unassigned, invalid in tests, ready) is read live and not part of the rule
    assert any(r.startswith('MISSING_SESSIONS_NOT_LOCAL:1:') and r.endswith(':hist_foundation_quotes') for r in cell['reasons'])


def test_jump_across_missing_session_beyond_two_limits_stays_unexplained():
    days = list(pd.bdate_range('2016-05-26', periods=6)) + [pd.Timestamp('2016-06-06')]
    bars = pd.DataFrame({'trade_date': days, 'stock_id': '1101', 'open': [10] * 6 + [14.0], 'close': [10] * 6 + [14.0], 'status': 'TRADED'})
    empty = pd.DataFrame({'stock_id': pd.Series(dtype=str), 'ex_date': pd.Series(dtype='datetime64[ns]')})
    jumps = cov.price_jumps(bars, empty, empty.rename(columns={'ex_date': 'date'}), pd.DatetimeIndex(['2016-06-04']))
    assert len(jumps) == 1 and not jumps.explained.iloc[0]  # +40% exceeds two compounded 10% limits: still an unexplained jump


def test_finlab_day_level_reverse_check_counts_a_hole_inside_the_span(sources, tmp_path):
    bars = hs.build(sources, tmp_path / 'a')[0].frame('bars')
    days = pd.DatetimeIndex(['2016-01-04', '2016-01-05', '2016-01-06'])
    wide = pd.DataFrame({'1101': [10.0, 10.0, 10.0]}, index=days)
    path = tmp_path / 'finlab.parquet'
    wide.to_parquet(path)
    rows = {'20160104': [hs_quote('1101')], '20160105': [hs_quote('2330')], '20160106': [hs_quote('1101')]}
    raw = sources.mi_index_dirs[0]
    for day, quotes in rows.items():
        (raw / f'MI_INDEX_{day}.json').write_text(json.dumps(mi_index(quotes), ensure_ascii=False), encoding='utf-8')
    snap = hs.build(hs.Sources(**{**sources.__dict__, 'window': ('2016-01-01', '2016-01-08'), 'reconcile': {'finlab_close': path}}), tmp_path / 'b')[0]
    cell = _cells(snap)[(2016, 'universe')]
    check = next(k for k in cell['checks'] if k['against'] == 'finlab_stock_days_present_locally')
    assert (check['compared'], check['agreed']) == (3, 2) and cell['metrics']['omitted_locally']['finlab'] == ['2016-01-05:1101']


def test_chip_coverage_counts_stock_days_not_files(sources, tmp_path):
    chips = tmp_path / 'chips'
    chips.mkdir()
    for day in ('20240102', '20240103', '20240104'):
        (chips / f'T86_{day}.json').write_text(json.dumps({'stat': 'OK', 'data': [['1101', 'A', '1']]}), encoding='utf-8')
    snap = hs.build(hs.Sources(**{**sources.__dict__, 'chip_dir': chips}), tmp_path / 'out')[0]
    cell = _cells(snap)[(2024, 't86_flows')]
    traded = snap.frame('bars').query("status == 'TRADED'")
    assert cell['present'] == 3 and cell['expected'] == len(traded)


def test_listed_company_never_seen_locally_is_caught(sources, tmp_path, monkeypatch):
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps([{'公司代號': '1101', '上市日期': '19620209'}, {'公司代號': '2330', '上市日期': '19940905'},
                                   {'公司代號': '1216', '上市日期': '19870127'}], ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(cov, 'LISTING_PROFILE', profile)
    cell = _cells(hs.build(sources, tmp_path / 'out')[0])[(2024, 'universe')]
    check = next(k for k in cell['checks'] if k['against'] == 'twse_profile_listed_present_locally')
    assert (check['population'], check['agreed']) == (3, 2) and cell['status'] == 'FAILED_VALIDATION'
    assert cell['metrics']['omitted_locally']['twse_profile'] == ['1216']


def test_ipo_sessions_are_not_jumps():
    days = pd.bdate_range('2024-01-01', periods=3)
    bars = pd.DataFrame({'trade_date': days, 'stock_id': '1101', 'open': [10, 20, 40], 'close': [10, 20, 40], 'status': 'TRADED'})
    assert cov.price_jumps(bars, pd.DataFrame(columns=['stock_id', 'ex_date']), pd.DataFrame(columns=['stock_id', 'date'])).empty


def test_capital_changes_blocked_when_no_file_covers_year(sources, tmp_path):
    cell = _cells(hs.build(sources, tmp_path / 'out')[0])[(2024, 'capital_changes')]
    assert cell['status'] == 'ACCESS_BLOCKED' and cell['reasons'][0].endswith(':hist_foundation_events')


def test_revenue_announcement_time_never_local(sources, tmp_path):
    cells = _cells(hs.build(sources, tmp_path / 'out')[0])
    assert all(cells[(y, 'revenue_announcement_time')]['status'] == 'ACCESS_BLOCKED' for y in cats.YEARS)


@pytest.mark.parametrize('category', ['benchmark_price', 'benchmark_total_return'])
def test_benchmark_without_independent_source_is_partial(sources, tmp_path, category):
    assert _cells(hs.build(sources, tmp_path / 'out')[0])[(2024, category)]['status'] == 'PARTIAL'


def test_delisted_code_suspended_before_the_window_is_found_not_missing(sources, tmp_path):
    """2384 / 5280 in 2015: delisted inside the year, absent from every session, last independent trade before the window starts."""
    delist = sources.delisting_file
    rows = json.loads(delist.read_text(encoding='utf-8')) + [{'DelistingDate': '113/01/03', 'Company': 'OLD', 'Code': '8888'},
                                                             {'DelistingDate': '113/01/03', 'Company': 'LATE', 'Code': '7777'}]
    delist.write_text(json.dumps(rows), encoding='utf-8')
    finlab = pd.DataFrame({'8888': [5.0, None], '7777': [None, 6.0]}, index=pd.to_datetime(['2023-12-20', '2024-01-02']))
    path = tmp_path / 'finlab_close.parquet'
    finlab.to_parquet(path)
    snap = hs.build(hs.Sources(**{**sources.__dict__, 'reconcile': {'finlab_close': path}}), tmp_path / 'out')[0]
    cells = _cells(snap)
    delisted = cells[(2024, 'delistings')]
    assert delisted['metrics']['suspended_before_window'] == {'8888': '2023-12-20'}
    assert delisted['metrics']['not_found'] == ['7777']  # traded inside the window per the second source, yet absent locally: still a gap
    assert cells[(2024, 'universe')]['metrics']['delisted_suspended_before_window'] == {'8888': '2023-12-20'}
