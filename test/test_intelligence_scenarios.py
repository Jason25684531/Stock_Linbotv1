import numpy as np
import pytest
import pandas as pd

from core.research.intelligence import scenarios as sc


def _calendar(years):
    return pd.DatetimeIndex([d for y in years for d in pd.bdate_range(f'{y}-01-01', f'{y}-12-31')])


def test_sample_rule_needs_three_complete_verified_years():
    cal = _calendar(range(2017, 2021))
    status = {2017: 'VERIFIED', 2018: 'VERIFIED', 2019: 'VERIFIED', 2020: 'PARTIAL'}
    ok = sc.sample_check(cal, cal, status)
    assert ok['status'] == 'SUFFICIENT' and ok['span'] == [2017, 2019] and ok['rule']['id'] == 'SR1'

    two = sc.sample_check(cal[cal >= '2017-07-01'], cal, status)
    assert two['status'] == 'INSUFFICIENT_VERIFIED_HISTORY'
    assert [y['complete'] for y in two['years'] if y['year'] in (2017, 2018)] == [False, True]

    missing = cal.delete(300)
    check = sc.sample_check(missing, cal, status)
    year = [y for y in check['years'] if y['year'] == cal[300].year][0]
    assert not year['complete'] and year['missing'] == [cal[300].strftime('%Y-%m-%d')]
    assert check['status'] == 'INSUFFICIENT_VERIFIED_HISTORY'


def test_same_seed_same_output_and_different_seed_differs():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0003, 0.01, 800)
    a, b = sc.simulate(r, seed=7, paths=300), sc.simulate(r, seed=7, paths=300)
    assert a == b
    assert sc.simulate(r, seed=8, paths=300)['drawdown']['median'] != a['drawdown']['median']
    assert a['paths'] == 300 and a['horizon'] == 252 and a['block'] == 20
    assert 'NOT a forecast' in a['notice_en']


def test_always_rising_and_always_falling():
    up = sc.simulate(np.full(800, 0.001), seed=1, paths=50)
    assert up['drawdown']['median'] == 0 and up['underwater']['censored_share'] == 0
    down = sc.simulate(np.full(800, -0.001), seed=1, paths=50)
    assert down['underwater']['censored_share'] == 1.0
    assert down['underwater']['share_underwater_at'] == {'63': 1.0, '126': 1.0, '189': 1.0, '252': 1.0}


def test_a_path_ending_under_water_is_censored_even_after_a_longer_recovered_spell():
    returns = np.r_[np.full(10, -0.01), np.full(30, 0.01), np.full(3, -0.01)]
    _, longest, censored, under = sc._drawdown_and_underwater(returns[None, :])
    assert censored[0] and under[0, -1]
    out = sc.simulate(np.r_[np.full(50, 0.001), -0.01], seed=2, paths=40, horizon=63, block=5, sensitivity=())
    assert out['underwater']['censored_share'] == out['underwater']['share_underwater_at']['63']


def test_worst_window_drawdown_scans_every_window():
    dates = pd.bdate_range('2018-01-01', periods=600)
    values = np.r_[np.linspace(100, 200, 300), np.linspace(200, 120, 100), np.linspace(120, 300, 200)]
    out = sc.stress(pd.Series(values, index=dates), None)
    assert out['worst_window_max_drawdown']['value'] == pytest.approx(120 / 200 - 1)


def test_historical_windows_are_recomputable():
    dates = pd.bdate_range('2018-01-01', periods=600)
    nav = pd.Series(np.r_[np.linspace(100, 200, 300), np.linspace(200, 120, 300)], index=dates)
    bench = pd.Series(np.r_[np.linspace(100, 50, 300), np.linspace(50, 60, 300)], index=dates)
    out = sc.stress(nav, bench)
    worst = out['strategy_worst_252']
    assert worst['return'] == nav[worst['end']] / nav[worst['start']] - 1
    assert out['benchmark_worst_252']['benchmark_return'] < 0
