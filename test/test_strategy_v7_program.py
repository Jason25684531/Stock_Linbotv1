"""V7 (change 2026-11-18-core-satellite-portfolio-v7): point-in-time revenue, the revenue momentum selection, the ETF switch with
next-session fills, and the temporary engine adjustments that must restore on exit."""
import numpy as np
import pandas as pd
import pytest

from core.backtest import runner
from scripts import strategy_v7_rules as rules


def _revenue(values_by_stock, first='2020-02-10'):
    """Wide frame indexed by release date; one row per month, values in dollars."""
    dates = pd.date_range(first, periods=len(next(iter(values_by_stock.values()))), freq='MS') + pd.Timedelta(days=9)
    return pd.DataFrame(values_by_stock, index=dates)


def test_revenue_is_usable_only_after_its_release_date():
    frame = _revenue({'2330': list(range(1, 25))})
    release = frame.index[-1]
    assert rules.usable_revenue(frame, release).index[-1] < release
    assert rules.usable_revenue(frame, release + pd.Timedelta(days=1)).index[-1] == release
    assert rules.usable_revenue(frame, frame.index[0]).empty


def test_momentum_requires_three_month_average_above_twelve_month_average_and_a_full_year_of_months():
    rising = [100.0] * 9 + [200.0, 210.0, 220.0]
    falling = [200.0] * 9 + [100.0, 90.0, 80.0]
    frame = _revenue({'1101': rising, '1102': falling, '1103': [np.nan] + rising[1:]}, first='2021-01-10')
    asof = frame.index[-1] + pd.Timedelta(days=1)
    assert rules.revenue_momentum_ids(frame, asof) == ['1101']
    gap = frame.drop(frame.index[5])
    assert rules.revenue_momentum_ids(gap, asof) == []


def _history(paths, volume=800_000.0):
    end = pd.Timestamp('2022-01-07')
    return pd.concat([pd.DataFrame({'stock_id': sid, 'trade_date': pd.bdate_range(end=end, periods=len(p)), 'close_price': p, 'volume': volume})
                      for sid, p in paths.items()], ignore_index=True)


def test_price_filters_need_a_close_above_the_20_60_and_120_session_averages_and_enough_volume():
    up = np.linspace(50, 100, 160)
    down = np.linspace(100, 60, 160)
    short = np.linspace(50, 100, 80)
    assert rules.price_trend_ids(_history({'1101': up, '1102': down, '1103': short}), min_volume=500_000) == ['1101']
    assert rules.price_trend_ids(_history({'1101': up}, volume=100_000.0), min_volume=500_000) == []


def test_selection_ranks_by_the_five_session_average_change_and_keeps_the_top_n_in_that_order():
    base = np.linspace(50, 80, 160)
    fast, slow = base.copy(), base.copy()
    fast[-5:] *= np.array([1.01, 1.02, 1.03, 1.04, 1.05])
    slow[-5:] *= np.array([1.001, 1.002, 1.003, 1.004, 1.005])
    history = _history({'1101': slow, '1102': fast, '1103': slow * 0.99})
    revenue = _revenue({sid: [100.0] * 9 + [200.0] * 3 for sid in ('1101', '1102', '1103')}, first='2020-12-10')
    assert rules.revenue_momentum_selection(history, revenue, top=2, min_volume=500_000)[0] == '1102'
    assert len(rules.revenue_momentum_selection(history, revenue, top=2, min_volume=500_000)) == 2


def test_etf_switch_trades_at_the_next_close_and_pays_the_etf_tax_only_when_selling():
    up, down = list(np.linspace(100, 200, 60)), list(np.linspace(200, 120, 40))
    frame = pd.DataFrame({'date': pd.bdate_range('2020-01-01', periods=100), 'close': up + down})
    free = rules.etf_switch(frame, ma=20, costs={'buy': 0.0, 'sell': 0.0}, multiple=1)
    paid = rules.etf_switch(frame, ma=20, costs={'buy': 0.002, 'sell': 0.003}, multiple=1)
    assert paid.nav.iloc[-1] < free.nav.iloc[-1]
    first_below = next(i for i, (p, m) in enumerate(zip(up + down, pd.Series(up + down).rolling(20).mean())) if i > 60 and p < m)
    assert free.position.iloc[first_below + 1] == 1 and free.position.iloc[first_below + 2] == 0
    assert free.nav.iloc[first_below + 1] == pytest.approx(free.nav.iloc[first_below] * frame.close.iloc[first_below + 1] / frame.close.iloc[first_below])
    assert free.nav.iloc[first_below + 2] == pytest.approx(free.nav.iloc[first_below + 1])


def test_the_market_gate_removes_entry_sessions_when_the_index_is_below_its_average(monkeypatch):
    sessions = pd.bdate_range('2021-01-01', periods=120)
    bench = pd.DataFrame({'date': sessions, 'taiex_total_return': list(np.linspace(100, 200, 60)) + list(np.linspace(200, 80, 60))})
    seen = {}
    from scripts import strategy_v6_rules
    real = strategy_v6_rules.market_above
    monkeypatch.setattr(strategy_v6_rules, 'market_above', lambda b, ma: seen.setdefault('gate', real(b, ma)))
    params = {'host': 'volatility_adjusted_momentum', 'top': 20, 'min_volume': 1, 'hold_sessions': 21, 'entry': 'monthly', 'market_ma': 20}
    with rules.portfolio_rule(params, sessions, pd.DataFrame(), bench):
        pass
    assert seen['gate'] and max(seen['gate']) < sessions[-1] and sessions[30] in seen['gate'] and sessions[-1] not in seen['gate']


def test_the_after_12_rebalance_takes_the_first_session_from_the_13th_of_each_month():
    sessions = pd.bdate_range('2021-01-01', '2021-04-30')
    picked = sorted(rules.first_sessions(sessions, 'monthly_after_12'))
    expected = [next(s for s in sessions if (s.year, s.month) == (2021, month) and s.day >= 13) for month in (1, 2, 3, 4)]
    assert picked == expected


def test_engine_adjustments_apply_inside_the_context_and_restore_after():
    before = (runner.MAX_HOLDINGS, runner.POSITION_SIZE, runner.BacktestEngine.process_close_signals)
    params = {'host': 'volatility_adjusted_momentum', 'top': 20, 'min_volume': 500_000, 'hold_sessions': 21, 'entry': 'monthly'}
    sessions = pd.bdate_range('2021-01-01', periods=60)
    with rules.portfolio_rule(params, sessions, revenue=pd.DataFrame()):
        assert runner.MAX_HOLDINGS == 20 and 1_000_000 * runner.POSITION_SIZE == pytest.approx(50_000)
        assert runner.BacktestEngine.process_close_signals is not before[2]
    assert (runner.MAX_HOLDINGS, runner.POSITION_SIZE, runner.BacktestEngine.process_close_signals) == before


class _Engine:
    """Only what rebalance_step touches: positions, cash, one bar per name, and the order queue."""

    def __init__(self, held, cash=0.0, bars=True):
        self.positions = {sid: {'shares': 1000, 'cost': 10.0, 'days': 0, **extra} for sid, extra in held.items()}
        self.capital, self.pending_orders, self.exits, self.bars = cash, [], [], bars

    def get_data(self, sid, session):
        return {'close_price': 12.0} if self.bars else None

    def _exit_at_signal(self, sid, price, session, reason):
        self.exits.append((sid, price, reason))


DAY = pd.Timestamp('2021-03-15')


def _book(target, rebalance=(DAY,), entries=(DAY,), top=3):
    return {'rebalance': set(rebalance), 'entries': set(entries), 'weight': rules._Weight(top), 'choose': lambda engine, session: list(target)}


def test_a_session_that_is_not_a_rebalance_day_places_no_order_and_ages_nothing():
    engine, book = _Engine({'A': {}}), _book(['B'])
    rules.rebalance_step(engine, '2021-03-16', book)
    assert engine.exits == [] and engine.pending_orders == [] and engine.positions['A']['days'] == 0 and book['weight'].equity is None


def test_a_rebalance_sells_only_the_names_that_dropped_out_and_buys_only_the_new_ones():
    engine, book = _Engine({'A': {}, 'B': {}, 'C': {}}), _book(['B', 'C', 'D'])
    rules.rebalance_step(engine, '2021-03-15', book)
    assert [(sid, reason) for sid, _, reason in engine.exits] == [('A', 'REBALANCE')]
    assert [(o['side'], o['stock_id']) for o in engine.pending_orders] == [('BUY', 'D')]
    assert all(info['days'] == 1 for info in engine.positions.values())


def test_a_gap_shorter_than_the_old_21_session_hold_still_rebalances_and_a_repeating_name_is_kept():
    engine = _Engine({'B': {}})
    for day in (pd.Timestamp('2021-03-15'), pd.Timestamp('2021-03-30')):
        rules.rebalance_step(engine, day, _book(['B', 'E'], rebalance=(pd.Timestamp('2021-03-15'), pd.Timestamp('2021-03-30')),
                                                entries=(pd.Timestamp('2021-03-15'), pd.Timestamp('2021-03-30'))))
    assert engine.exits == [] and [o['stock_id'] for o in engine.pending_orders] == ['E', 'E']


def test_a_closed_entry_gate_still_sells_but_buys_nothing():
    engine, book = _Engine({'A': {}}), _book(['B'], entries=())
    rules.rebalance_step(engine, '2021-03-15', book)
    assert [sid for sid, _, _ in engine.exits] == ['A'] and engine.pending_orders == []


def test_a_name_without_a_bar_is_not_sold_into_a_missing_price():
    engine, book = _Engine({'A': {}}, bars=False), _book(['B'])
    rules.rebalance_step(engine, '2021-03-15', book)
    assert engine.exits == []


def test_each_buy_is_one_equal_share_of_what_the_book_is_worth_at_the_last_rebalance():
    engine = _Engine({'A': {'last_price': 11.0}, 'B': {'last_valid_close': 13.0, 'last_price': 99.0}, 'C': {}}, cash=100_000.0)
    book = _book(['A', 'B', 'C'])
    assert 1_000_000 * book['weight'] == pytest.approx(1_000_000 / 3)
    rules.rebalance_step(engine, '2021-03-15', book)
    assert book['weight'].equity == pytest.approx(100_000 + 1000 * (11.0 + 13.0 + 10.0))
    assert 1_000_000 * book['weight'] == pytest.approx(134_000 / 3)


def _manifest(tmp_path, monkeypatch, name='revenue_usable.parquet'):
    import hashlib
    import json
    from scripts import strategy_v7_program as program
    monkeypatch.setattr(rules, 'DATA', tmp_path)
    (tmp_path / name).write_bytes(b'data')
    (tmp_path / 'manifest.json').write_text(json.dumps({'outputs': {name: hashlib.sha256(b'data').hexdigest()}}, ensure_ascii=False), encoding='utf-8')
    return program


def test_the_data_files_are_re_hashed_against_the_manifest_at_run_time_and_a_non_ascii_name_reads_back(tmp_path, monkeypatch):
    program = _manifest(tmp_path, monkeypatch, name='數據.parquet')
    assert list(program.verified_data()) == ['數據.parquet']
    (tmp_path / '數據.parquet').write_bytes(b'changed')
    with pytest.raises(RuntimeError, match='DATA_CHANGED_SINCE_BUILD'):
        program.verified_data()


def test_the_registration_binding_covers_the_data_manifest_the_rebalance_step_and_the_gate_functions(tmp_path, monkeypatch):
    program = _manifest(tmp_path, monkeypatch)
    portfolio, switch = program.bound_code('portfolio'), program.bound_code('etf_switch')
    assert {'rules7.rebalance_step', 'rules7._Weight', 'rules6.market_above', 'rules6.until', 'data_manifest'} <= set(portfolio)
    assert 'data_manifest' in switch
    (tmp_path / 'manifest.json').write_text('{"outputs": {}}', encoding='utf-8')
    assert program.bound_code('portfolio') != portfolio


def test_the_installed_program_restores_every_v6_attribute_on_exit():
    from scripts import strategy_v6_program as v6
    from scripts import strategy_v7_program as program
    names = ('OUT', 'BUDGET', 'PROXY_KINDS', 'bound_code', 'numbers_for', 'measurement_code')
    before = {n: getattr(v6, n) for n in names}
    with program.installed():
        assert v6.OUT == program.OUT and v6.bound_code is program.bound_code
    assert {n: getattr(v6, n) for n in names} == before


def _registered(cells):
    return [{'event': 'REGISTERED', 'spec': {'variants': {str(i): {} for i in range(n)}}} for n in cells]


def test_the_seventh_cell_and_an_unsourced_free_slot_are_refused_before_the_v6_admission(monkeypatch):
    from scripts import strategy_v6_program as v6
    from scripts import strategy_v7_program as program
    monkeypatch.setattr(v6, 'queue', lambda: _registered([1, 2, 1, 1]))
    monkeypatch.setattr(v6, 'admit', lambda spec: 'admitted')
    with pytest.raises(ValueError, match='SEVENTH_CELL'):
        program.admit({'variants': {'a': {}, 'b': {}}, 'parent': 'A1'})
    with pytest.raises(ValueError, match='FREE_SLOT_NEEDS_A_FAILED_PARENT'):
        program.admit({'variants': {'a': {}}})
    assert program.admit({'variants': {'a': {}}, 'parent': 'A1'}) == 'admitted'


def test_the_report_carries_the_v7_wording_a_not_met_outcome_when_nothing_blocked_and_the_stored_notes(tmp_path, monkeypatch):
    import json
    from scripts import strategy_v6_program as v6
    from scripts import strategy_v7_program as program
    (tmp_path / 'results').mkdir()
    (tmp_path / 'results' / 'A1__b__development.json').write_text(json.dumps({'hypothesis': 'A1', 'variant': 'b', 'window': 'development', 'note': 'odd lots', 'span': [2017, 2023],
                                                                              'verdict': 'FAILED', 'series': None, 'benchmark': {}}), encoding='utf-8')
    monkeypatch.setattr(v6, 'charter', lambda: {'windows': {'development': [2017, 2023]}})

    def fake(items, final):
        (tmp_path / 'V6_ACCEPTANCE_REPORT.md').write_text('# V6 \u5e74\u5831\u916c\u76ee\u6a19\u7814\u7a76\u8a08\u756b\uff1a\u9a57\u6536\u5831\u8868\n' + program.CLOSEST + program.BLOCKED_OUTCOME[0] + '\n', encoding='utf-8')
        (tmp_path / 'report.json').write_text('{"outcome": "STOPPED_BY_BLOCKER"}', encoding='utf-8')
        return 'STOPPED_BY_BLOCKER'

    monkeypatch.setattr(program, 'OUT', tmp_path)
    monkeypatch.setattr(v6, 'write_report', fake)
    monkeypatch.setattr(v6, 'queue', lambda: [{'event': 'REGISTERED'}])
    monkeypatch.setattr(v6, 'write', lambda path, body: path.write_text(json.dumps(body), encoding='utf-8'))
    assert program.write_report((), final=True) == 'TARGET_NOT_MET'
    text = (tmp_path / 'V7_ACCEPTANCE_REPORT.md').read_text(encoding='utf-8')
    assert text.startswith('# V7') and 'TARGET_NOT_MET' in text and 'STOPPED_BY_BLOCKER' not in text and 'A1b development' in text and 'odd lots' in text and 'SAMPLE' in text.split('A1b development')[0]
    assert not (tmp_path / 'V6_ACCEPTANCE_REPORT.md').exists() and json.loads((tmp_path / 'report.json').read_text())['outcome'] == 'TARGET_NOT_MET'


def test_a_cash_gate_sells_every_held_name_when_entries_are_closed_and_buys_nothing():
    engine, book = _Engine({'A': {}, 'B': {}}), _book(['A', 'C'], entries=())
    book['exit_when_closed'] = True
    rules.rebalance_step(engine, '2021-03-15', book)
    assert sorted(sid for sid, _, _ in engine.exits) == ['A', 'B'] and engine.pending_orders == []


def test_a_cash_gate_changes_nothing_while_entries_are_open():
    engine, book = _Engine({'A': {}, 'B': {}}), _book(['A', 'C'])
    book['exit_when_closed'] = True
    rules.rebalance_step(engine, '2021-03-15', book)
    assert [sid for sid, _, _ in engine.exits] == ['B'] and [o['stock_id'] for o in engine.pending_orders] == ['C']


def test_the_report_note_warns_when_the_stored_series_is_shorter_than_the_registered_span():
    from scripts import strategy_v7_program as program
    short = {'span': [2017, 2023], 'series': {'date': ['2018-12-28', '2021-12-30']}}
    full = {'span': [2019, 2023], 'series': {'date': ['2018-12-28', '2023-12-29']}}
    assert '2018-12-28' in program._coverage_note(short) and program._coverage_note(full) == '' and program._coverage_note({'span': [2017, 2023], 'series': None}) == ''


def test_a_cell_without_trades_prints_a_dash_not_zero_in_the_report_row_and_the_best_line_carries_the_full_window_drawdown(tmp_path, monkeypatch):
    from scripts import strategy_v7_program as program
    run = tmp_path / 'runs' / 'RUN1'
    run.mkdir(parents=True)
    dates = pd.bdate_range('2017-01-02', '2023-12-29')
    nav = np.concatenate([np.linspace(1, 2, len(dates) // 2), np.linspace(2, 1, len(dates) - len(dates) // 2)])
    pd.DataFrame({'date': dates, 'nav_base': nav}).to_csv(run / 'nav.csv', index=False)
    monkeypatch.setattr(program, 'OUT', tmp_path)
    cut = _cell(hypothesis='A1', variant='b', span=[2019, 2021], run_id='RUN1', series={'date': ['2018-12-28', '2021-12-30']})
    fixed = program._fix_rows('| A1 | b | development 2019–2021 | +54.2% | x |\n- 最佳年化：A1b +54.2%', [cut], WINDOWS)
    assert '-50.0%' in fixed.split('\n')[1]
    row = '| B1 | a | development 2017–2023 | +7.5% | -25.7% | — | 0 | +14.7% / -28.6% |'
    none = _cell(hypothesis='B1', variant='a', span=[2017, 2023], closed_trades=0, series=None)
    assert '| — | — | +14.7%' in program._fix_rows(row, [none], WINDOWS)


ALL_MET = '\u5168\u90e8\u9054\u6a19'
WINDOWS = {'development': [2017, 2023]}


def _cell(**extra):
    base = {'hypothesis': 'C1', 'variant': 'a', 'window': 'development', 'span': [2017, 2023], 'verdict': 'FAILED',
            'benchmark': {'cagr': {'value': 0.147}, 'max_drawdown': {'value': -0.286}}, 'series': {'date': ['2018-12-28', '2021-12-30']}}
    return base | extra


def test_only_a_promising_or_holdout_pass_verdict_may_keep_the_all_targets_met_wording():
    from scripts import strategy_v7_program as program
    row = f'| C1 | a | development 2017\u20132023 | +36.0% | {ALL_MET} |'
    closest = f'- best\uff1aC1a -16.3% ({ALL_MET})'
    assert ALL_MET not in program._fix_rows(row + '\n' + closest, [_cell()], WINDOWS)
    assert ALL_MET in program._fix_rows(row + '\n' + closest, [_cell(verdict='PROMISING', series=None)], WINDOWS)


def test_a_cell_on_fewer_years_shows_the_dates_it_covers_and_no_benchmark_in_the_report_row():
    from scripts import strategy_v7_program as program
    row = '| C1 | a | development 2017\u20132023 | +36.0% | +14.7% / -28.6% | x |'
    fixed = program._fix_rows(row, [_cell()], WINDOWS)
    assert '2018-12-28\u20132021-12-30' in fixed and '+14.7%' not in fixed


def test_a_verified_span_cut_names_the_years_left_out_and_the_same_runs_drawdown_over_the_full_window(tmp_path, monkeypatch):
    from scripts import strategy_v7_program as program
    run = tmp_path / 'runs' / 'RUN1'
    run.mkdir(parents=True)
    dates = pd.bdate_range('2017-01-02', '2023-12-29')
    nav = pd.Series(np.concatenate([np.linspace(1, 2, 700), np.linspace(2, 1, len(dates) - 700)]), index=dates)
    pd.DataFrame({'date': dates, 'nav_base': nav.values, 'nav_stress': nav.values}).to_csv(run / 'nav.csv', index=False)
    monkeypatch.setattr(program, 'OUT', tmp_path)
    note = program._cut_note({'window': 'development', 'span': [2019, 2021], 'run_id': 'RUN1'}, {'development': [2017, 2023]})
    assert '2017/2018/2022/2023' in note and '-50.0%' in note
    assert program._cut_note({'window': 'development', 'span': [2017, 2023], 'run_id': 'RUN1'}, {'development': [2017, 2023]}) == ''


def test_a_company_delisted_later_stays_eligible_in_the_months_it_reported():
    rising = [100.0] * 9 + [200.0, 210.0, 220.0]
    frame = _revenue({'1101': rising + [230.0] * 6, '9999': rising + [np.nan] * 6}, first='2021-01-10')
    asof = frame.index[11] + pd.Timedelta(days=1)
    assert rules.revenue_momentum_ids(frame, asof) == ['1101', '9999']
    assert rules.revenue_momentum_ids(frame, frame.index[-1] + pd.Timedelta(days=1)) == ['1101']


def test_a_cell_cut_to_its_verified_years_shows_the_years_left_out_in_its_report_row_and_in_the_best_lines():
    from scripts import strategy_v7_program as program
    cut = _cell(hypothesis='A1', variant='b', span=[2019, 2021], series={'date': ['2018-12-28', '2021-12-30']})
    row = '| A1 | b | development 2019\u20132021 | +54.2% | x |'
    best = '- \u6700\u4f73\u5e74\u5316\uff1aA1b +54.2%'
    fixed = program._fix_rows(row + '\n' + best, [cut], {'development': [2017, 2023]})
    assert '2017/2018/2022/2023' in fixed.split('\n')[0] and '2017/2018/2022/2023' in fixed.split('\n')[1]
    full = _cell(hypothesis='A1', variant='b', span=[2017, 2023], series={'date': ['2016-12-30', '2023-12-29']})
    assert program._fix_rows(row, [full], {'development': [2017, 2023]}) == row
