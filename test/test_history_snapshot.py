import json
import os

import pandas as pd
import pytest

from core.research.history import snapshot as hs

FIELDS = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價', '漲跌(+/-)', '漲跌價差',
          '最後揭示買價', '最後揭示買量', '最後揭示賣價', '最後揭示賣量', '本益比']
NO_DATA = '很抱歉，沒有符合條件的資料!'


def quote(sid, name, o, h, l, c, vol=1000):
    return [sid, name, f'{vol:,}', '10', f'{vol * float(c):,.0f}', o, h, l, c, '', '0', '', '', '', '', '']


def mi_index(rows, price=9000.0, tri=15000.0):
    return {'stat': 'OK', 'tables': [
        {'title': '價格指數(臺灣證券交易所)', 'fields': ['指數', '收盤指數'], 'data': [['發行量加權股價指數', f'{price:,.2f}']]},
        {'title': '報酬指數(臺灣證券交易所)', 'fields': ['指數', '收盤指數'], 'data': [['發行量加權股價報酬指數', f'{tri:,.2f}']]},
        {'title': '每日收盤行情(全部)', 'fields': FIELDS, 'data': rows}]}


def twt49u(rows):
    return {'stat': 'OK', 'fields': ['資料日期', '股票代號', '股票名稱', '除權息前收盤價', '除權息參考價', '權值+息值', '權/息', '漲停價格', '跌停價格',
                                     '開盤競價基準', '減除股利參考價', '詳細資料', '最近一次申報資料 季別/日期', '最近一次申報每股 (單位)淨值',
                                     '最近一次申報每股 (單位)盈餘'], 'data': rows}


@pytest.fixture
def sources(tmp_path, monkeypatch):
    from core.research.history import coverage
    monkeypatch.setattr(coverage, 'LISTING_PROFILE', tmp_path / 'no_profile.json')  # tests never read the repository's profile file
    raw = tmp_path / 'raw'
    raw.mkdir()
    days = {
        '20240102': [quote('1101', 'A', '10.00', '10.50', '9.90', '10.00'), quote('2330', 'TSMC', '500', '510', '495', '500'),
                     quote('9999', 'GONE', '5.00', '5.10', '4.90', '5.00'), quote('030001', 'WARRANT', '1.00', '1.00', '1.00', '1.00')],
        '20240103': [quote('1101', 'A', '9.50', '9.60', '9.40', '9.50'), quote('2330', 'TSMC', '505', '506', '500', '505'),
                     ['9999', 'GONE', '0', '0', '0', '--', '--', '--', '--', '', '', '', '', '', '', '']],
        '20240104': [quote('1101', 'A', '9.60', '9.70', '9.50', '9.60'), quote('2330', 'TSMC', '510', '512', '505', '510')],
    }
    for day, rows in days.items():
        (raw / f'MI_INDEX_{day}.json').write_text(json.dumps(mi_index(rows), ensure_ascii=False), encoding='utf-8')
    (raw / 'MI_INDEX_20240105.json').write_text(json.dumps({'stat': NO_DATA}, ensure_ascii=False), encoding='utf-8')
    events = raw / 'TWT49U_20240101_20241231.json'
    events.write_text(json.dumps(twt49u([['113年01月03日', '1101', 'A', '10.00', '9.50', '0.50', '息', '', '', '9.50', '9.50', '', '', '', '']]),
                                 ensure_ascii=False), encoding='utf-8')
    delist = raw / 'suspendListing.json'
    delist.write_text(json.dumps([{'DelistingDate': '113/01/04', 'Company': 'GONE', 'Code': '9999'}]), encoding='utf-8')
    return hs.Sources(mi_index_dirs=(raw,), twt49u_files=(events,), capital_files=(), delisting_file=delist, fetch_log=None,
                      chip_dir=None, pinned={}, reconcile={}, window=('2024-01-01', '2024-01-05'))


def test_build_then_reuse_writes_nothing(sources, tmp_path):
    snap, state = hs.build(sources, tmp_path / 'out')
    assert state == 'BUILT' and snap.snapshot_id.startswith('HS1-')
    stamp = {p: p.stat().st_mtime_ns for p in snap.root.rglob('*')}
    again, state = hs.build(sources, tmp_path / 'out')
    assert state == 'REUSE' and again.snapshot_id == snap.snapshot_id
    assert {p: p.stat().st_mtime_ns for p in snap.root.rglob('*')} == stamp
    assert hs.verify(again) == []


def test_source_byte_change_changes_id(sources, tmp_path):
    first, _ = hs.build(sources, tmp_path / 'out')
    path = sources.mi_index_dirs[0] / 'MI_INDEX_20240104.json'
    path.write_bytes(path.read_bytes() + b' ')
    second, state = hs.build(sources, tmp_path / 'out')
    assert state == 'BUILT' and second.snapshot_id != first.snapshot_id


def test_raw_files_are_never_written(sources, tmp_path):
    before = {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in sources.mi_index_dirs[0].iterdir()}
    hs.build(sources, tmp_path / 'out')
    assert {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in sources.mi_index_dirs[0].iterdir()} == before


def test_bars_keep_untraded_rows_and_drop_warrants(sources, tmp_path):
    snap, _ = hs.build(sources, tmp_path / 'out')
    bars = snap.frame('bars')
    assert not bars.stock_id.str.len().ne(4).any()
    gone = bars[(bars.stock_id == '9999')].set_index('trade_date').status
    assert gone[pd.Timestamp('2024-01-02')] == 'TRADED' and gone[pd.Timestamp('2024-01-03')] == 'NO_TRADE'
    assert bars.loc[bars.stock_id == '2330', 'name'].iloc[0] == 'TSMC'


def test_universe_includes_later_delisted_stock_before_delisting(sources, tmp_path):
    snap, _ = hs.build(sources, tmp_path / 'out')
    assert '9999' in hs.universe(snap, '2024-01-02')
    assert '9999' not in hs.universe(snap, '2024-01-04')
    delist = snap.frame('delistings').set_index('stock_id')
    assert delist.loc['9999', 'last_listed'] == pd.Timestamp('2024-01-03')


def test_calendar_classifies_every_weekday(sources, tmp_path):
    snap, _ = hs.build(sources, tmp_path / 'out')
    cal = snap.frame('calendar').set_index('date').kind
    assert cal[pd.Timestamp('2024-01-05')] == 'NON_TRADING' and cal[pd.Timestamp('2024-01-02')] == 'TRADING'
    assert len(cal) == 5 and cal[pd.Timestamp('2024-01-01')] == 'NO_FILE'


def test_saturday_session_file_is_read(sources, tmp_path):
    raw = sources.mi_index_dirs[0]
    (raw / 'MI_INDEX_20240106.json').write_text(json.dumps(mi_index([quote('1101', 'A', '9.60', '9.70', '9.50', '9.70')]), ensure_ascii=False),
                                               encoding='utf-8')
    snap, _ = hs.build(hs.Sources(**{**sources.__dict__, 'window': ('2024-01-01', '2024-01-08')}), tmp_path / 'out')
    cal = snap.frame('calendar').set_index('date').kind
    assert cal[pd.Timestamp('2024-01-06')] == 'TRADING'
    assert pd.Timestamp('2024-01-06') in set(snap.frame('bars').trade_date)


def test_benchmark_has_price_and_total_return(sources, tmp_path):
    snap, _ = hs.build(sources, tmp_path / 'out')
    bench = snap.frame('benchmark')
    assert list(bench.columns) == ['date', 'taiex_price', 'taiex_total_return'] and bench.taiex_price.iloc[0] == 9000.0


def test_events_carry_prior_session_close(sources, tmp_path):
    snap, _ = hs.build(sources, tmp_path / 'out')
    ev = snap.frame('events').iloc[0]
    assert ev.stock_id == '1101' and ev.pre_ex_close == 10.0 and ev.prior_session_close == 10.0 and ev.value == 0.5


def test_window_after_2025_refused(sources, tmp_path):
    with pytest.raises(ValueError, match='RESEARCH_WINDOW'):
        hs.build(hs.Sources(**{**sources.__dict__, 'window': ('2025-12-01', '2026-01-05')}), tmp_path / 'out')


def test_total_value_kept_across_cash_and_stock_dividend():
    pre_close, cash, ratio = 100.0, 3.0, 0.1
    reference = (pre_close - cash) / (1 + ratio)
    shares, close_after = 1000, 92.0
    assert shares * (1 + ratio) * reference + shares * cash == pytest.approx(shares * pre_close)  # value kept at the ex reference price
    reinvested = shares * (1 + ratio) + shares * cash / reference  # cash reinvested at the ex reference price
    factor = reference / pre_close
    assert hs.adjusted_return(pre_close, close_after, factor) == pytest.approx(reinvested * close_after / (shares * pre_close) - 1)


def test_adjusted_close_scales_only_bars_before_ex_date():
    bars = pd.DataFrame({'trade_date': pd.to_datetime(['2024-01-02', '2024-01-03']), 'stock_id': ['1101', '1101'], 'close': [10.0, 9.6]})
    events = pd.DataFrame({'ex_date': pd.to_datetime(['2024-01-03']), 'stock_id': ['1101'], 'event_factor': [0.95]})
    assert hs.adjusted_close(bars, events).tolist() == pytest.approx([9.5, 9.6])
    assert bars.close.tolist() == [10.0, 9.6]


def test_short_index_labels_and_saturday_files_and_reduction_reasons(sources, tmp_path):
    """2019-04-29 style short index labels parse; a dated Saturday file is a TRADING session; TWTAUU rows keep their reason (ledger terms)."""
    raw = sources.mi_index_dirs[0]
    short = mi_index([quote('1101', 'A', '9.70', '9.80', '9.60', '9.70')], price=9100.0, tri=15100.0)
    short['tables'][0]['data'][0][0] = '加權股價指數'  # the short label TWSE used on 2019-04-29
    short['tables'][1]['data'][0][0] = '加權股價報酬指數'
    (raw / 'MI_INDEX_20240106.json').write_text(json.dumps(short, ensure_ascii=False), encoding='utf-8')  # 2024-01-06 is a Saturday
    capital = raw / 'TWTAUU_20240101_20241231.json'
    capital.write_text(json.dumps({'stat': 'OK', 'fields': ['恢復買賣日期', '股票代號', 'n', 'p', 'r', 'u', 'd', 'b', 'x',
                                                             '減資原因', 'detail'],
                                   'data': [['113/01/04', '2330', 'TSMC', '505.00', '600.00', '', '', '', '--', '退還股款', '']]}), encoding='utf-8')
    snap = hs.build(hs.Sources(**{**sources.__dict__, 'capital_files': (capital,), 'window': ('2024-01-01', '2024-01-06')}), tmp_path / 'out')[0]
    calendar = snap.frame('calendar').set_index('date')
    assert calendar.loc[pd.Timestamp('2024-01-06'), 'kind'] == 'TRADING'
    bench = snap.frame('benchmark').set_index('date')
    assert bench.loc[pd.Timestamp('2024-01-06'), 'taiex_price'] == 9100.0 and bench.loc[pd.Timestamp('2024-01-06'), 'taiex_total_return'] == 15100.0
    assert pd.Timestamp('2024-01-06') in set(snap.frame('bars').trade_date)
    reductions = snap.frame('capital_changes')
    assert reductions.reason.tolist() == ['退還股款'] and reductions.kind.tolist() == ['CAPITAL_REDUCTION']


def test_identical_dividend_rows_in_two_files_keep_both_files(sources, tmp_path):
    first = sources.twt49u_files[0]
    second = first.with_name('TWT49U_20240101_20240131.json')
    second.write_text(first.read_text(encoding='utf-8'), encoding='utf-8')
    snap = hs.build(hs.Sources(**{**sources.__dict__, 'twt49u_files': (first, second)}), tmp_path / 'out')[0]
    events = snap.frame('events')
    assert len(events) == 1 and events.source_file.iloc[0] == ';'.join(sorted([first.name, second.name]))
