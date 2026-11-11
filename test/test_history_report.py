import pandas as pd

from scripts import historical_report as hr


def test_action_exposure_counts_trades_across_events_and_unexplained_jumps():
    trades = pd.DataFrame({'stock_id': ['1101', '2330', '2002'], 'buy_date': ['2024-01-02', '2024-01-02', '2024-01-02'],
                           'sell_date': ['2024-01-10', '2024-01-10', '2024-01-03']})
    marks = {'1101': pd.DatetimeIndex(['2024-01-05']), '2002': pd.DatetimeIndex(['2024-01-08'])}
    jumps = {'2330': pd.DatetimeIndex(['2024-01-09'])}
    out = hr.action_exposure(trades, marks, jumps)
    assert out == {'trades': 3, 'exposed': 1, 'share': 1 / 3, 'unexplained_jump_trades': 1, 'unexplained_jump_years': [2024]}


def test_final_year_with_unexplained_jump_is_excluded_because_open_positions_are_untracked():
    jumps = {'2330': pd.DatetimeIndex(['2025-07-14'])}
    assert hr.untracked_open_position_years('2025-12-31', jumps) == [2025]
    assert hr.untracked_open_position_years('2025-12-31', {'2330': pd.DatetimeIndex(['2024-07-14'])}) == []
    assert hr.untracked_open_position_years('2025-12-31', {'2330': pd.DatetimeIndex(['2024-07-14'])}, max_hold_days=300) == [2024]


def test_missing_gap_file_is_an_error_not_zero_gaps(tmp_path, monkeypatch):
    import pytest
    from scripts import historical_foundation as hf
    monkeypatch.setattr(hf, 'REPORTS', tmp_path)
    with pytest.raises(FileNotFoundError):
        hr.final_tables({'evaluation_id': 'EV1-000000000000', 'coverage': [], 'requirements': [], 'summaries': {}, 'trust': {}}, None)


def test_reports_are_write_once(tmp_path):
    hr.write_once('a.md', 'x', tmp_path)
    hr.write_once('a.md', 'x', tmp_path)  # identical content: no error
    try:
        hr.write_once('a.md', 'y', tmp_path)
        raise AssertionError('overwrote stored evidence')
    except FileExistsError:
        pass


def test_uncredited_distributions_per_year():
    trades = pd.DataFrame({'stock_id': ['1101', '2330'], 'buy_date': ['2024-01-02', '2024-03-01'], 'sell_date': ['2024-02-01', '2024-03-05'],
                           'shares': [1000, 2000]})
    events = pd.DataFrame({'stock_id': ['1101', '2330', '1101'], 'ex_date': pd.to_datetime(['2024-01-10', '2024-06-01', '2024-03-01']),
                           'value': [2.5, 3.0, 1.0]})
    nav = pd.DataFrame({'date': ['2024-01-02', '2024-12-31'], 'nav_base': [1_000_000.0, 1_100_000.0]})
    assert hr.uncredited_distributions(trades, events, nav) == {'2024': {'value': 2500.0, 'share_of_start_nav': 0.0025}}


def test_run_without_trades_reads_as_zero_trades(tmp_path):
    (tmp_path / 'trades.csv').write_text('\n', encoding='utf-8')
    trades = hr.read_trades(tmp_path / 'trades.csv')
    assert trades.empty and 'sell_date' in trades.columns


def test_zero_trade_run_gets_a_signal_investigation_priority_not_a_performance_one():
    row = {'state': 'REUSE', 'reason': 'IDENTICAL_RUN_IDENTITY', 'no_trades': True}
    d = hr.decision(row, {'data_trust_verified': None}, {'years': {}, 'segments': {'VERIFIED_ONLY': {}}})
    assert d['priority'] == 'NO_TRADES_INVESTIGATE_SIGNAL'


def test_spec_before_run_compares_instants_not_strings():
    assert hr.spec_before('2026-11-10T11:51:01+08:00', '2026-11-10T04:10:00+00:00')
    assert not hr.spec_before('2026-11-10T11:51:01+08:00', '2026-11-10T03:00:00+00:00')


def test_undefined_completeness_is_none_with_reason():
    assert hr._share(0, 0) is None and hr._share(None, 10) is None and hr._share(5, 10) == 0.5
