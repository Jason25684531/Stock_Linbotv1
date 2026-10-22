"""Synthetic engineering checks for the native22 validation; never investment evidence."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.backtest import runner
from core.backtest.costs import CostModel
from core.backtest.execution import apply_slippage
from core.backtest.portfolio import Portfolio
from core.backtest.runner import BacktestEngine
from test_next_session_execution import engine, prepare_run

@pytest.mark.parametrize('sessions', [['2026-10-16', '2026-10-17'], ['2026-10-16', '2026-10-16'], ['2026-10-19', '2026-10-16']])
def test_weekend_duplicate_or_unordered_sessions_fail_loudly(sessions):
    with pytest.raises(ValueError):
        BacktestEngine(execution_contract='next_session_open_v2', persist_results=False, trading_sessions=sessions)


def test_insufficient_cash_never_overbuys():
    e = engine()
    e.portfolio = Portfolio(50_000)
    for sid in ('1101', '1102', '1103'):
        e.buy(sid, 40.0, '2026-10-19')
    assert len(e.positions) == 1 and e.capital >= 0
    position = e.positions['1101']
    assert position['total_cost'] + e.capital == pytest.approx(50_000)


def test_costs_minimum_fee_tax_on_sell_only_and_adverse_slippage():
    costs = CostModel(0.001425, 0.003, 20)
    assert costs.buy_cost(1_000) == 20
    assert costs.buy_cost(1_000_000) == 1425
    assert costs.sell_cost(1_000_000) == 1425 + 3000
    rng = SimpleNamespace(uniform=lambda low, high: high)
    assert apply_slippage(100, 'buy', 0.001, rng) > 100 > apply_slippage(100, 'sell', 0.001, rng)


def test_same_inputs_reproduce_identical_execution(monkeypatch):
    first = prepare_run(monkeypatch).run(return_metrics=True)
    second = prepare_run(monkeypatch).run(return_metrics=True)
    assert first['execution_events'] == second['execution_events']
    assert first['risk_metrics'].get('trade_count').value == second['risk_metrics'].get('trade_count').value


def test_fundamental_replay_defers_missing_price_without_stale_fill():
    from core.research.fundamental_strategy_validation import simulate_target_weights

    dates = pd.to_datetime(['2026-10-16', '2026-10-19', '2026-10-20'])
    prices = pd.DataFrame({'1101': [10.0, np.nan, 12.0], '1102': [20.0, 20.0, 20.0]}, index=dates)
    targets = pd.DataFrame({'signal_date': pd.to_datetime(['2026-10-15', '2026-10-16']), 'asof_date': pd.to_datetime(['2026-10-15', '2026-10-16']),
                            'execution_date': dates[:2], 'asset_id': ['1101', '1102'], 'target_weight': [1.0, 1.0]})
    result = simulate_target_weights(targets, prices, initial_capital=100_000)
    trades = [(t['execution_date'], t['asset_id'], t['side'], t['price']) for t in result['trades']]
    assert trades == [(dates[0], '1101', 'buy', 10.0)]  # no sell at a stale 10.0 on 10/19
    assert result['execution_log'].to_dict('records')[0]['reason'] == 'MISSING_EXECUTION_PRICE'


def _panel(seed):
    rng = np.random.default_rng(seed)
    index = pd.bdate_range('2024-01-01', periods=300)
    columns = [f'{1100 + i}' for i in range(6)]
    close = pd.DataFrame(np.exp(np.cumsum(rng.normal(0, .02, (300, 6)), axis=0)) * 50, index=index, columns=columns)
    frame = lambda scale: close * scale
    volume = pd.DataFrame(rng.integers(1_000, 9_000, (300, 6)).astype(float), index=index, columns=columns)
    return {'close': close, 'open': frame(.99), 'high': frame(1.02), 'low': frame(.98), 'volume': volume, 'amount': close * volume,
            'adjusted_close': close, 'adjusted_open': frame(.99), 'adjusted_high': frame(1.02), 'adjusted_low': frame(.98)}


def test_factor_values_ignore_future_rows():
    from core.research.day2_candidates import CandidateSpec, _alpha101, _alpha191, compute_candidate
    from core.research.factors.definitions import FACTOR_REGISTRY, compute_factor

    cut = 260
    base, noise = _panel(1), _panel(2)
    future = {k: pd.concat([v.iloc[:cut], noise[k].iloc[cut:]]) for k, v in base.items()}
    day3 = {'A101_003': lambda f: _alpha101('A101_003', f), 'A191_088': lambda f: _alpha191('A191_088', f),
            'A191_132': lambda f: _alpha191('A191_132', f),
            'RS_004_NEAR_HIGH_120D': lambda f: compute_candidate(CandidateSpec('RS_004_NEAR_HIGH_120D', 'near_high_120d', 'D', 'Alpha', 'TIER1', lookback=120, kind='near_high'), f)}
    d5 = {name: (lambda f, n=name: compute_factor(FACTOR_REGISTRY[n], dict(f)).values)
          for name in ('amihud_20d', 'momentum_12_1', 'momentum_20d', 'momentum_60d', 'near_high_252d')}
    for name, fn in {**day3, **d5}.items():
        pd.testing.assert_frame_equal(fn(base).iloc[:cut], fn(future).iloc[:cut], check_freq=False, obj=name)
        assert fn(base).iloc[cut - 1].notna().any(), name
