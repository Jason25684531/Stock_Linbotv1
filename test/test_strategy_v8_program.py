"""V8 (change 2026-11-18-low-volatility-book-v8): the low-volatility selection step and the temporary context that must restore on exit."""
import numpy as np
import pandas as pd
import pytest

from core.backtest import runner
from scripts import strategy_v8_rules as v8

SESSIONS = 160


def _history(amplitudes, calm_after=None):
    """One rising name per amplitude whose closes alternate around the trend; the last close always sits above the trend."""
    rows = []
    dates = pd.bdate_range(end='2022-01-07', periods=SESSIONS)
    for n, amp in enumerate(amplitudes):
        base = np.linspace(50, 100, SESSIONS)
        sign = np.array([(-1) ** (i + 1) for i in range(SESSIONS)])
        size = np.full(SESSIONS, amp)
        if calm_after is not None and n in calm_after:
            size[calm_after[n]:] = 0.001
        rows.append(pd.DataFrame({'stock_id': f'11{n + 1:02d}', 'trade_date': dates, 'close_price': base * (1 + sign * size), 'volume': 800_000.0}))
    return pd.concat(rows, ignore_index=True)


def _revenue(count):
    dates = pd.date_range('2020-12-10', periods=12, freq='MS') + pd.Timedelta(days=9)
    return pd.DataFrame({f'11{n + 1:02d}': [100.0] * 9 + [200.0] * 3 for n in range(count)}, index=dates)


AMPLITUDES = [0.004, 0.008, 0.012, 0.02, 0.03, 0.04]


def _pick(history, keep, top=40):
    return v8.low_vol_selection(history, _revenue(len(AMPLITUDES)), top, 500_000, keep)


def test_the_lowest_half_and_the_lowest_third_of_the_filtered_names_are_kept():
    history = _history(AMPLITUDES)
    assert sorted(_pick(history, 0.5)) == ['1101', '1102', '1103']
    assert sorted(_pick(history, 1 / 3)) == ['1101', '1102']


def test_the_top_names_come_from_the_kept_names_only_and_the_book_may_hold_fewer_than_its_size():
    history = _history(AMPLITUDES)
    assert len(_pick(history, 0.5, top=40)) == 3
    assert len(_pick(history, 0.5, top=1)) == 1 and _pick(history, 0.5, top=1)[0] in {'1101', '1102', '1103'}


def test_only_the_last_60_sessions_set_the_volatility_so_a_name_that_calmed_down_counts_as_low():
    history = _history([0.05, 0.01, 0.03], calm_after={0: SESSIONS - 70})
    assert v8.low_vol_selection(history, _revenue(3), 40, 500_000, 1 / 3) == ['1101']


def test_a_name_that_fails_the_v7_filters_is_never_added_by_the_volatility_step():
    history = _history(AMPLITUDES)
    revenue = _revenue(len(AMPLITUDES))
    revenue['1101'] = [200.0] * 9 + [100.0] * 3
    assert '1101' not in v8.low_vol_selection(history, revenue, 40, 500_000, 0.5)


def test_the_context_enters_the_v7_rule_replaces_the_selection_and_restores_everything_on_exit():
    from scripts import strategy_v5_rules
    host = strategy_v5_rules.STRATEGY_CLASSES['volatility_adjusted_momentum']
    before = (runner.MAX_HOLDINGS, runner.POSITION_SIZE, runner.BacktestEngine.process_close_signals, host.select_history)
    params = {'host': 'volatility_adjusted_momentum', 'top': 40, 'min_volume': 500_000, 'entry': 'monthly', 'vol_keep': 0.5}
    with v8.low_vol_rule(params, pd.bdate_range('2021-01-01', periods=60), pd.DataFrame()):
        assert runner.MAX_HOLDINGS == 40 and host.select_history is not before[3]
    assert (runner.MAX_HOLDINGS, runner.POSITION_SIZE, runner.BacktestEngine.process_close_signals, host.select_history) == before


def test_a_vol_keep_other_than_one_half_or_one_third_is_refused():
    with pytest.raises(ValueError, match='UNREGISTERED_VOL_KEEP'):
        with v8.low_vol_rule({'host': 'volatility_adjusted_momentum', 'top': 40, 'min_volume': 1, 'entry': 'monthly', 'vol_keep': 0.7}, pd.bdate_range('2021-01-01', periods=60), pd.DataFrame()):
            pass


def test_installed_points_the_programs_at_the_v8_folder_binds_the_v8_functions_and_restores_everything():
    from scripts import strategy_v6_program as v6
    from scripts import strategy_v7_program as v7
    from scripts import strategy_v8_program as program
    names = ('OUT', 'BUDGET', 'PROXY_KINDS', 'bound_code', 'numbers_for', 'measurement_code')
    before = ({n: getattr(v6, n) for n in names}, v7.OUT, v7.rules.portfolio_rule, tuple(v7.V7_KINDS['portfolio']), hasattr(v7.rules, 'low_vol_rule'))
    with program.installed():
        assert v6.OUT == program.OUT and v7.OUT == program.OUT and v6.BUDGET == program.BUDGET
        assert v7.rules.portfolio_rule is v8.low_vol_rule and 'rules7.low_vol_selection' in v7.bound_code('portfolio')
    after = ({n: getattr(v6, n) for n in names}, v7.OUT, v7.rules.portfolio_rule, tuple(v7.V7_KINDS['portfolio']), hasattr(v7.rules, 'low_vol_rule'))
    assert after == before


def test_the_v8_binding_differs_from_the_v7_binding_and_a_third_variant_is_refused(monkeypatch):
    from scripts import strategy_v6_program as v6
    from scripts import strategy_v7_program as v7
    from scripts import strategy_v8_program as program
    v7_binding = v7.bound_code('portfolio')
    with program.installed():
        assert program.v7.bound_code('portfolio') != v7_binding
        monkeypatch.setattr(v6, 'charter', lambda: {'budget': program.BUDGET})
        monkeypatch.setattr(v6, 'queue', lambda: [])
        with pytest.raises(ValueError, match='TOO_MANY_VARIANTS'):
            v7.admit({'id': 'LV1', 'variants': {'a': {}, 'b': {}, 'c': {}}})


def test_the_rule_still_works_while_the_program_has_replaced_the_v7_rule_with_it():
    from scripts import strategy_v8_program as program
    params = {'host': 'volatility_adjusted_momentum', 'top': 40, 'min_volume': 1, 'entry': 'monthly', 'vol_keep': 0.5}
    with program.installed():
        with v8.low_vol_rule(params, pd.bdate_range('2021-01-01', periods=60), pd.DataFrame()):
            assert runner.MAX_HOLDINGS == 40


def test_inside_the_context_the_host_selection_is_the_low_volatility_one_not_the_plain_v7_rule():
    from scripts import strategy_v5_rules
    from scripts import strategy_v7_rules as v7
    host = strategy_v5_rules.STRATEGY_CLASSES['volatility_adjusted_momentum']
    history, revenue = _history(AMPLITUDES), _revenue(len(AMPLITUDES))
    params = {'host': 'volatility_adjusted_momentum', 'top': 40, 'min_volume': 500_000, 'entry': 'monthly', 'vol_keep': 0.5}
    with v8.low_vol_rule(params, pd.bdate_range('2021-01-01', periods=60), revenue):
        picked = host.select_history(None, history)
    assert sorted(picked) == ['1101', '1102', '1103']
    assert len(v7.revenue_momentum_selection(history, revenue, 40, 500_000)) == len(AMPLITUDES)


def test_with_every_name_kept_the_ranking_equals_the_v7_ranking_even_when_a_recent_close_is_missing():
    from scripts import strategy_v7_rules as v7
    history = _history(AMPLITUDES)
    mask = (history.stock_id == '1104') & (history.trade_date == history[history.stock_id == '1104'].trade_date.iloc[-3])
    history.loc[mask, 'close_price'] = np.nan
    revenue = _revenue(len(AMPLITUDES))
    assert v8.low_vol_selection(history, revenue, 40, 500_000, 1.0) == v7.revenue_momentum_selection(history, revenue, 40, 500_000)


def test_the_binding_covers_the_rules_file_so_a_changed_constant_is_caught():
    from scripts import strategy_v7_program as v7
    from scripts import strategy_v8_program as program
    with program.installed():
        assert 'scripts/strategy_v8_rules.py' in v7.bound_code('portfolio')


def test_the_book_note_reports_the_average_names_held_and_the_average_cash_share_from_the_stored_run(tmp_path, monkeypatch):
    from scripts import strategy_v8_program as program
    run = tmp_path / 'runs' / 'RUN1'
    run.mkdir(parents=True)
    rows = [('2021-01-04', 'CASH', 600.0), ('2021-01-04', '1101', 200.0), ('2021-01-04', '1102', 200.0),
            ('2021-01-05', 'CASH', 100.0)] + [('2021-01-05', f'11{n:02d}', 225.0) for n in range(1, 5)]
    pd.DataFrame(rows, columns=['date', 'stock_id', 'value']).to_csv(run / 'positions_daily.csv', index=False)
    monkeypatch.setattr(program, 'OUT', tmp_path)
    note = program._book_note({'run_id': 'RUN1', 'span': [2021, 2021]})
    assert '3.0' in note and '35%' in note
    assert program._book_note({'run_id': 'MISSING', 'span': [2021, 2021]}) == ''


def test_the_window_note_gives_the_same_runs_base_and_stress_return_and_drawdown_over_the_whole_registered_window(tmp_path, monkeypatch):
    from scripts import strategy_v8_program as program
    run = tmp_path / 'runs' / 'RUN1'
    run.mkdir(parents=True)
    dates = pd.bdate_range('2017-01-02', '2023-12-29')
    base = np.concatenate([np.linspace(1, 2, len(dates) // 2), np.linspace(2, 1.5, len(dates) - len(dates) // 2)])
    pd.DataFrame({'date': dates, 'nav_base': base, 'nav_stress': base * 0.9}).to_csv(run / 'nav.csv', index=False)
    monkeypatch.setattr(program, 'OUT', tmp_path)
    windows = {'development': [2017, 2023]}
    cut = {'window': 'development', 'span': [2019, 2020], 'run_id': 'RUN1'}
    note = program._window_note(cut, windows)
    assert '-25.0%' in note and '2017-2023' in note and '+6.0%' in note
    assert program._window_note({'window': 'development', 'span': [2017, 2023], 'run_id': 'RUN1'}, windows) == ''
    assert program._window_note({'window': 'development', 'span': [2019, 2020], 'run_id': 'NONE'}, windows) == ''
