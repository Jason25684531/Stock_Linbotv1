import math

import pandas as pd
import pytest

from core.research.intelligence import performance as perf


def _nav(values, start='2017-01-02', stress=None):
    dates = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({'date': dates, 'nav_base': values, 'nav_stress': stress or values})


def test_verified_span_picks_the_longest_consecutive_run():
    status = {2016: 'PARTIAL', 2017: 'VERIFIED', 2018: 'VERIFIED', 2019: 'PARTIAL', 2020: 'VERIFIED', 2021: 'VERIFIED', 2022: 'VERIFIED'}
    assert perf.verified_span(status, range(2016, 2023)) == (2020, 2022)
    assert perf.verified_span({2023: 'PARTIAL'}, [2023]) is None


def test_track_starts_from_the_previous_close_and_never_joins_gaps():
    nav = pd.DataFrame({'date': pd.to_datetime(['2018-12-28', '2019-01-02', '2019-12-31', '2020-01-02']),
                        'nav_base': [100.0, 110.0, 121.0, 50.0], 'nav_stress': [100.0, 109.0, 119.0, 49.0]})
    track = perf.slice_track(nav, 2019, 2019)
    assert list(track.nav_base) == [100.0, 110.0, 121.0]
    assert track.date.max() == pd.Timestamp('2019-12-31')
    assert list(perf._yearly(track)) == ['2019'] and perf._yearly(track)['2019']['base'] == pytest.approx(0.21)
    full = perf._yearly(nav)
    assert '2018' not in full and full['2019']['base'] == pytest.approx(0.21) and full['2020']['base'] == pytest.approx(50 / 121 - 1)


def test_drawdown_episodes_and_unrecovered_tail():
    dates = pd.bdate_range('2020-01-01', periods=8)
    eq = [100, 90, 80, 100, 110, 99, 105, 100]
    eps = perf.drawdown_episodes(dates, eq)
    assert eps[0]['depth'] == pytest.approx(-0.2)
    assert eps[0]['peak_to_recovery_sessions'] == 3 and eps[0]['trough_to_recovery_sessions'] == 1
    assert eps[-1]['recovery_date'] is None and eps[-1]['underwater_sessions'] == 3
    assert eps[-1]['depth'] == pytest.approx(99 / 110 - 1)


def test_summary_against_hand_computation():
    values = [100.0 * (1.001 ** i) for i in range(253)]
    nav = _nav(values, stress=[100.0 * (1.0005 ** i) for i in range(253)])
    trades = pd.DataFrame({'stock_id': ['1101', '1102'], 'buy_date': ['2017-01-03', '2017-02-01'], 'sell_date': ['2017-01-10', '2017-02-08'],
                           'profit_pct': [5.0, -2.5], 'buy_price': [10.0, 20.0], 'shares': [1000, 1000]})
    out = perf.summarize(nav, trades, None, cost_model={'fee_rate': 0.001425, 'minimum_fee': 20})
    assert out['cagr']['value'] == pytest.approx(1.001 ** 252 - 1)
    assert out['cagr_stress']['value'] == pytest.approx(1.0005 ** 252 - 1)
    assert out['max_drawdown']['value'] == 0
    assert out['profit_factor']['value'] == pytest.approx(2.0)
    assert out['expectancy']['value'] == pytest.approx(0.0125)
    assert out['avg_hold_days']['value'] == pytest.approx(5.0)
    assert out['benchmark_cagr_total_return'] == {'value': None, 'reason': 'BENCHMARK_MISSING'}
    assert out['excess_cagr'] == {'value': None, 'reason': 'BENCHMARK_MISSING'}
    assert out['annualization'] == {'sessions_per_year': 252, 'risk_free_rate': 0.0, 'returns': 'daily simple returns of nav_base, costs included'}


def test_no_trades_is_null_not_zero():
    out = perf.summarize(_nav([100.0, 101.0, 102.0]), pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'profit_pct']), None)
    assert out['win_rate']['value'] is None and out['win_rate']['reason']
    assert out['profit_factor']['value'] is None


def test_non_positive_equity_gives_no_cagr():
    out = perf.summarize(_nav([100.0, 50.0, 0.0, 10.0]), None, None)
    assert out['cagr'] == {'value': None, 'reason': 'NON_POSITIVE_EQUITY'}


def test_benchmark_and_downside_capture():
    nav = _nav([100.0, 99.0, 99.0, 101.0])
    bench = pd.DataFrame({'date': nav.date, 'taiex_price': [100.0, 98.0, 98.0, 100.0], 'taiex_total_return': [100.0, 98.0, 98.0, 100.0]})
    out = perf.summarize(nav, None, bench)
    assert out['downside_capture']['value'] == pytest.approx(0.01 / 0.02)
    assert out['benchmark_max_drawdown_price']['value'] == pytest.approx(-0.02)
    bench.loc[2, 'taiex_total_return'] = math.nan
    assert perf.summarize(nav, None, bench)['excess_cagr']['reason'] == 'BENCHMARK_MISSING_SESSIONS:1'


def test_trade_level_mfe_mae_and_forward_returns():
    days = pd.bdate_range('2020-01-01', periods=30)
    bars = pd.DataFrame({'trade_date': days, 'stock_id': '1101', 'high': 11.0, 'low': 9.0, 'close': 10.0 + pd.Series(range(30)) * 0.1})
    trades = pd.DataFrame({'stock_id': ['1101'], 'buy_date': [days[0].strftime('%Y-%m-%d')], 'sell_date': [days[3].strftime('%Y-%m-%d')],
                           'profit_pct': [1.0], 'buy_price': [10.0], 'shares': [1000], 'distributions': [None]})
    out = perf.trade_level(trades, bars, actions=None)
    assert out['mfe_median']['value'] == pytest.approx(0.1)
    assert out['mae_median']['value'] == pytest.approx(-0.1)
    assert out['forward_return_5']['value'] == pytest.approx(10.5 / 10 - 1)
    held = trades.assign(distributions=[123.0])
    assert perf.trade_level(held, bars, actions=None)['mfe_median']['reason'].startswith('NO_TRADES_WITHOUT_CORPORATE_ACTION')
