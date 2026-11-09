import json

import numpy as np
import pandas as pd
import pytest

from core.research import trade_analytics as ta

BASE = {'cagr': 0.1, 'total_return': 0.3, 'sharpe': 1.0, 'win_rate': 0.5, 'profit_factor': 1.2, 'alpha': 0.02,
        'capacity': 1e7, 'max_drawdown': -0.2, 'cost_ratio': 0.01, 'turnover_annual': 3.0, 'yearly_return_std': 0.1}


def run(run_id, status='SUCCESS', group='G', **overrides):
    metrics = {**BASE, **overrides}
    return {'run_id': run_id, 'comparison_group': group, 'status': status,
            'metrics': {k: v for k, v in metrics.items() if v is not None}}


def by_id(results):
    return {item['run_id']: item for item in results}


def test_mdd_direction_smaller_drawdown_scores_higher():
    scores = by_id(ta.avg_scores([run('A', max_drawdown=-0.10), run('B', max_drawdown=-0.30), run('C', max_drawdown=-0.20)]))
    assert scores['A']['percentiles']['max_drawdown_abs'] == 1.0
    assert scores['A']['avg_score'] > scores['C']['avg_score'] > scores['B']['avg_score']


def test_missing_metric_excluded_not_rewarded():
    scores = by_id(ta.avg_scores([run('A', profit_factor=None), run('B', profit_factor=0.5), run('C', profit_factor=2.0)]))
    a = scores['A']
    assert 'profit_factor' not in a['percentiles']
    assert (a['scored_count'], a['total']) == (10, 11)
    assert a['avg_score'] == pytest.approx(np.mean(list(a['percentiles'].values())))
    assert a['avg_score'] == pytest.approx(2 / 3)  # never zero-filled, never the best rank
    assert a['avg_score'] < scores['C']['avg_score']


def test_insufficient_metrics():
    sparse = {'run_id': 'S', 'comparison_group': 'G', 'status': 'SUCCESS', 'metrics': {'cagr': 0.1, 'sharpe': 1.0}}
    scores = by_id(ta.avg_scores([run('A'), run('B'), sparse]))
    assert scores['S']['avg_score'] is None and scores['S']['reason'] == 'INSUFFICIENT_METRICS'


def test_fewer_than_three_runs_is_none():
    scores = ta.avg_scores([run('A'), run('B'), run('X', status='NOT_TESTED_BUDGET')])
    assert all(item['avg_score'] is None for item in scores)
    assert {item['reason'] for item in scores if item['status'] == 'SUCCESS'} == {'TOO_FEW_COMPARABLE_RUNS'}


def test_mixed_groups_raise():
    with pytest.raises(ValueError, match='MIXED_COMPARISON_GROUPS'):
        ta.avg_scores([run('A'), run('B'), run('C', group='OTHER')])


def test_heatmap_excludes_non_success():
    scores = ta.avg_scores([run('A'), run('B'), run('C'), run('D', status='DATA_BLOCKED')])
    frame = ta.heatmap_frame(scores)
    assert frame['y'] == ['A', 'B', 'C']
    assert frame['excluded'] == [{'run_id': 'D', 'status': 'DATA_BLOCKED'}]
    assert len(frame['z'][0]) == len(frame['x']) == len(ta.METRIC_DIRECTIONS)
    assert frame['customdata'][0][frame['x'].index('max_drawdown_abs')] == pytest.approx(0.2)


def test_top_n_returns_fewer_when_not_enough():
    sparse = {'run_id': 'S', 'comparison_group': 'G', 'status': 'SUCCESS', 'metrics': {'cagr': 9.0}}
    top = ta.top_n(ta.avg_scores([run('B', cagr=0.2), run('A'), sparse]))
    assert [item['run_id'] for item in top] == ['B', 'A']


def test_top_n_ties_by_run_id():
    top = ta.top_n(ta.avg_scores([run('C'), run('A'), run('B'), run('D')]), n=3)
    assert [item['run_id'] for item in top] == ['A', 'B', 'C']


def trades(rows):
    columns = ['stock_id', 'buy_date', 'sell_date', 'buy_price', 'sell_price', 'shares', 'cost']
    return pd.DataFrame(rows, columns=columns)


def test_profit_factor_no_losing_trades_and_recovery_not_recovered():
    nav = pd.Series([100.0, 90.0, 95.0], index=['2025-01-02', '2025-01-03', '2025-01-06'])
    out = ta.performance(nav, initial_cash=100.0, trades=trades([['A', '2025-01-02', '2025-01-03', 10, 11, 1, 0.1]]))
    assert out['profit_factor'] == {'value': None, 'reason': 'NO_LOSING_TRADES'}
    assert out['recovery_days'] == {'value': None, 'reason': 'NOT_RECOVERED'}
    assert out['max_drawdown']['value'] == pytest.approx(-0.10)
    assert out['observation_labels']['mdd_ge_minus_30'] is True
    empty = ta.performance(nav, initial_cash=100.0, trades=trades([]))
    assert empty['profit_factor']['reason'] == 'NO_TRADES' and empty['trade_count']['value'] == 0


def test_metrics_never_inf_and_json_strict():
    dates = pd.bdate_range('2024-12-02', periods=60)
    rng = np.random.default_rng(1)
    bench = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 60)), index=dates)
    nav = pd.Series(1000 * np.cumprod(1 + rng.normal(0.001, 0.01, 60)), index=dates.strftime('%Y-%m-%d'))
    book = trades([['A', '2024-12-02', '2024-12-10', 10, 12, 10, 1.0], ['B', '2024-12-03', '2025-01-10', 10, 9, 10, 1.0]])
    out = ta.performance(nav, initial_cash=1000.0, benchmark=bench, trades=book)
    json.dumps(out, allow_nan=False)
    assert out['beta']['value'] is not None and out['alpha']['value'] is not None
    assert set(out['yearly_returns']['value']) == {2024, 2025}
    assert out['profit_factor']['value'] == pytest.approx(19.0 / 11.0)
    assert out['cost_ratio']['value'] == pytest.approx(0.002)
    flat = ta.performance(pd.Series([1000.0] * 5, index=dates[:5]), initial_cash=1000.0)
    json.dumps(flat, allow_nan=False)
    assert flat['sharpe']['value'] is None and flat['excess_return']['reason'] == 'BENCHMARK_UNAVAILABLE'
    assert flat['observation_labels']['sharpe_gt_1_5'] is None
    json.dumps(ta.avg_scores([run('A', sharpe=float('inf')), run('B'), run('C')]), allow_nan=False)


def bar(low, high, close, volume=1000):
    return {'low': low, 'high': high, 'close': close, 'volume': volume}


def test_mae_mfe_numeric():
    bars = {'2025-01-02': {'A': bar(98, 102, 100)}, '2025-01-03': {'A': bar(95, 108, 104)},
            '2025-01-06': {'A': bar(101, 106, 105)}}
    book = trades([['A', '2025-01-02', '2025-01-06', 100, 105, 10, 0.0], ['Z', '2025-01-02', '2025-01-03', 10, 11, 1, 0.0]])
    result = ta.mae_mfe(book, bars)
    first = result.iloc[0]
    assert first['mae'] == pytest.approx(-0.05) and first['mfe'] == pytest.approx(0.08)
    assert (first['holding_days'], first['outcome']) == (3, 'WIN') and first['return'] == pytest.approx(0.05)
    assert np.isnan(result.iloc[1]['mae']) and result.iloc[1]['reason'] == 'MISSING_BARS'
    same = ta.mae_mfe(book, lambda day: bars.get(day), calendar=list(bars))
    assert same.iloc[0]['mae'] == pytest.approx(-0.05)


def test_liquidity_limit_and_suspension_days():
    bars = {'2025-01-02': {'A': bar(99, 101, 100), 'B': bar(9, 11, 10)},
            '2025-01-03': {'A': bar(100, 110, 110)},
            '2025-01-06': {'A': bar(109, 112, 111), 'B': bar(9, 11, 10)},
            '2025-01-07': {'A': bar(110, 113, 112)}}
    book = trades([['A', '2025-01-03', '2025-01-07', 110, 112, 100, 0.0], ['B', '2025-01-02', '2025-01-06', 10, 10, 100, 0.0]])
    out = ta.liquidity(book, bars)
    assert out['limit_days']['value'] == 1
    assert out['suspension_days']['value'] == 1
    assert out['order_share_max']['value'] == pytest.approx(0.1) and out['min_volume']['value'] == 1000
    assert out['attention_flag'] == {'value': None, 'reason': 'NO_LOCAL_SOURCE'}
    json.dumps(out, allow_nan=False)


def test_factor_analysis_drops_windows_past_horizon_end():
    dates = {'2025-12-22': '2025-12-29', '2025-12-24': '2025-12-31', '2025-12-30': '2026-01-06'}
    panel, forward = [], []
    for day, window_end in dates.items():
        for i in range(10):
            panel += [{'asof_date': day, 'asset_id': i, 'factor_id': 'f1', 'value': i},
                      {'asof_date': day, 'asset_id': i, 'factor_id': 'f2', 'value': -i}]
            forward.append({'asof_date': day, 'asset_id': i, 'horizon': 5, 'window_end': window_end, 'forward_return': i * 0.01})
    out = ta.factor_analysis(pd.DataFrame(panel), pd.DataFrame(forward), min_assets=5)
    assert out['status'] == 'OK' and out['dropped_windows'] == 10
    f1 = out['factors']['f1'][5]
    assert f1['valid_ic_days'] == 2 and f1['mean_ic']['value'] == pytest.approx(1.0)
    assert f1['factor_return']['value'] == pytest.approx(0.08)
    assert out['factors']['f2'][5]['factor_return']['value'] == pytest.approx(-0.08)
    assert list(f1['yearly_ic']) == [2025]
    assert out['factors']['f1']['coverage']['value'] == pytest.approx(1.0)
    assert out['correlation']['value']['f1']['f2'] == pytest.approx(-1.0)
    starved = ta.factor_analysis(pd.DataFrame(panel), pd.DataFrame(forward), min_assets=50)
    assert starved['status'] == 'NOT_COMPUTABLE'
    assert starved['factors']['f1'][5]['mean_ic']['reason'].startswith('NOT_COMPUTABLE')
